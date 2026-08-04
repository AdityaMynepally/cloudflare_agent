"""WebSocket bridge between the AI agent and Chrome extension.

Self-healing capabilities:
  1. Retry on timeout  — send_with_retry() retries up to MAX_RETRIES times
                         with exponential backoff (2 → 4 → 8 s).
  2. Reconnect healing — if the WebSocket closes mid-command, the bridge
                         waits up to RECONNECT_WAIT seconds for the extension
                         to reconnect, then re-sends the command on the new
                         socket automatically.
  3. Service-worker wake-up ping — before every command attempt a lightweight
                         ping is sent to the extension; if no pong arrives
                         within PING_TIMEOUT seconds the service worker is
                         considered asleep and the bridge waits for reconnect
                         before proceeding.
"""

import asyncio
import itertools
import logging
from typing import Optional

from fastapi import WebSocket

logger = logging.getLogger(__name__)

MAX_RETRIES      = 3
RETRY_BASE_DELAY = 2.0    # doubles each attempt: 2 → 4 → 8 s
PING_TIMEOUT     = 5.0    # seconds to wait for pong before treating SW as dead
RECONNECT_WAIT   = 35.0   # seconds to wait for extension to reconnect


class HealingError(Exception):
    """Raised when all self-healing retries are exhausted."""


class ExtensionBridge:
    """Bridges agent commands to a connected Chrome extension via WebSocket.

    Thread of execution:
      • send_with_retry() is the public entry point for all commands.
      • It first pings the extension to wake the service worker.
      • On any failure it waits for a reconnect event, then retries.
      • resolve_pending() / resolve_pong() are called by the WS handler
        (api.py) when the extension sends back a result.
    """

    _req_id_counter = itertools.count(1)

    def __init__(self, websocket: WebSocket, session_id: str, timeout: int = 30):
        self.websocket     = websocket
        self.session_id    = session_id
        self.timeout       = timeout
        self._pending_future: Optional[asyncio.Future] = None
        # Tracks which request the current _pending_future is actually
        # waiting on. A long-running command (check_srp_filters can take
        # ~30s) combined with the self-healing retry path — which resends a
        # command on a fresh future without any guarantee the extension's
        # response to the FIRST attempt won't also eventually arrive — meant
        # a late/duplicate response could silently resolve whatever command
        # happened to be pending by the time it arrived, handing that
        # command a completely unrelated result (confirmed in production:
        # a used-inventory page capture got resolved with a stale
        # check_srp_filters result). Every outgoing command now carries a
        # unique req_id that the extension echoes back, so resolve_pending
        # can reject anything that doesn't match what's actually expected.
        self._pending_req_id: Optional[int] = None
        self._ping_future:    Optional[asyncio.Future] = None
        self._reconnect_event = asyncio.Event()

    # ------------------------------------------------------------------ #
    #  Reconnect healing                                                   #
    # ------------------------------------------------------------------ #

    def update_websocket(self, websocket: WebSocket) -> None:
        """Called by api.py when the extension reconnects with the same session_id.

        Updates the socket reference and signals any waiters so in-flight
        commands can be retried on the fresh connection.
        """
        logger.info(f"[Bridge:{self.session_id}] WebSocket reconnected, updating reference")
        self.websocket = websocket
        self._reconnect_event.set()

    async def _wait_for_reconnect(self) -> bool:
        """Block until the extension reconnects or RECONNECT_WAIT elapses."""
        self._reconnect_event.clear()
        try:
            await asyncio.wait_for(self._reconnect_event.wait(), timeout=RECONNECT_WAIT)
            logger.info(f"[Bridge:{self.session_id}] Extension reconnected — resuming")
            return True
        except asyncio.TimeoutError:
            logger.error(f"[Bridge:{self.session_id}] Reconnect wait timed out after {RECONNECT_WAIT}s")
            return False

    # ------------------------------------------------------------------ #
    #  Service-worker wake-up ping                                         #
    # ------------------------------------------------------------------ #

    async def _ping(self) -> bool:
        """Send a ping and wait for pong_ack. Returns True if extension is alive."""
        loop = asyncio.get_event_loop()
        self._ping_future = loop.create_future()
        try:
            await self.websocket.send_json({"type": "ping"})
            await asyncio.wait_for(self._ping_future, timeout=PING_TIMEOUT)
            return True
        except asyncio.TimeoutError:
            logger.warning(f"[Bridge:{self.session_id}] Ping timeout — service worker may be sleeping")
            return False
        except Exception as e:
            logger.warning(f"[Bridge:{self.session_id}] Ping error: {e}")
            return False
        finally:
            self._ping_future = None

    def resolve_pong(self) -> None:
        """Called by api.py when the extension sends back a 'pong' message."""
        if self._ping_future and not self._ping_future.done():
            self._ping_future.set_result(True)

    # ------------------------------------------------------------------ #
    #  Core send / receive                                                 #
    # ------------------------------------------------------------------ #

    async def send_and_wait(self, command: dict, timeout: Optional[float] = None) -> dict:
        """Send a command and await capture_result from the extension.

        timeout: override the bridge's default (self.timeout) for commands
        known to legitimately run long — e.g. check_srp_filters interactively
        tests up to 3 filters x 4 options with a 2.5s settle each, ~30s
        worst case, right at (and in practice frequently over) the 30s
        default, causing it to time out on every single run.

        Raises asyncio.TimeoutError or Exception on failure.
        """
        loop = asyncio.get_event_loop()
        req_id = next(self._req_id_counter)
        self._pending_future  = loop.create_future()
        self._pending_req_id  = req_id
        effective_timeout = timeout if timeout is not None else self.timeout
        try:
            await self.websocket.send_json({**command, "req_id": req_id})
            logger.info(f"[Bridge:{self.session_id}] Sent: {command.get('type')} (req_id={req_id})")
            result = await asyncio.wait_for(self._pending_future, timeout=effective_timeout)
            logger.info(f"[Bridge:{self.session_id}] Received result for: {command.get('type')} (req_id={req_id})")
            return result
        except asyncio.TimeoutError:
            logger.error(f"[Bridge:{self.session_id}] Timeout: {command.get('type')} (req_id={req_id})")
            raise
        except Exception as e:
            logger.error(f"[Bridge:{self.session_id}] Error: {e}")
            raise
        finally:
            self._pending_future = None
            self._pending_req_id = None

    async def send_with_retry(
        self, command: dict, retries: int = MAX_RETRIES, timeout: Optional[float] = None,
    ) -> dict:
        """Send a command with self-healing retry.

        On timeout:     wait with exponential backoff (2 → 4 → 8 s), then retry.
        On disconnect:  wait for the extension to reconnect (up to RECONNECT_WAIT s),
                        then re-send on the fresh socket.

        timeout: see send_and_wait — overrides the bridge default for this call.

        Raises HealingError after all retries are exhausted.
        """
        cmd_type = command.get("type", "unknown")
        last_error: Exception = RuntimeError("No attempts made")

        for attempt in range(1, retries + 2):
            try:
                return await self.send_and_wait(command, timeout=timeout)

            except asyncio.TimeoutError as exc:
                last_error = exc
                if attempt > retries:
                    break
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(
                    f"[Bridge:{self.session_id}] Timeout on '{cmd_type}' "
                    f"attempt {attempt}/{retries}. Retrying in {delay:.0f}s..."
                )
                await asyncio.sleep(delay)

            except Exception as exc:
                # Treat any non-timeout exception as a disconnect — wait for reconnect.
                last_error = exc
                if attempt > retries:
                    break
                logger.warning(
                    f"[Bridge:{self.session_id}] Disconnect on '{cmd_type}' "
                    f"(attempt {attempt}/{retries}): {exc}. Waiting for reconnect..."
                )
                reconnected = await self._wait_for_reconnect()
                if not reconnected:
                    break

        logger.error(
            f"[Bridge:{self.session_id}] All retries exhausted for '{cmd_type}': {last_error}"
        )
        raise HealingError(f"'{cmd_type}' failed after {retries} retries: {last_error}") from last_error

    def resolve_pending(self, data: dict, req_id: Optional[int] = None) -> bool:
        """Resolve the pending command future with capture result data.

        Discards (returns False without resolving) any result whose req_id
        doesn't match what's currently expected — a late response from an
        earlier command (or a duplicate from a retried one) must never be
        allowed to satisfy a DIFFERENT, later command's future with its
        unrelated data. req_id=None is accepted unconditionally so older
        extension builds that don't yet echo it back still work, just
        without this protection.
        """
        if not (self._pending_future and not self._pending_future.done()):
            return False
        if req_id is not None and req_id != self._pending_req_id:
            logger.warning(
                f"[Bridge:{self.session_id}] Discarding stale/mismatched result "
                f"(got req_id={req_id}, expected={self._pending_req_id})"
            )
            return False
        self._pending_future.set_result(data)
        return True
        return False

    @property
    def has_pending(self) -> bool:
        return self._pending_future is not None and not self._pending_future.done()

    # ------------------------------------------------------------------ #
    #  Convenience command methods                                         #
    # ------------------------------------------------------------------ #

    async def navigate(self, url: str) -> dict:
        return await self.send_with_retry({"type": "navigate", "url": url})

    async def capture(self) -> dict:
        return await self.send_with_retry({"type": "capture"})

    async def click(self, selector: str) -> dict:
        return await self.send_with_retry({"type": "click", "selector": selector})

    async def fill(self, selector: str, value: str) -> dict:
        return await self.send_with_retry({"type": "fill", "selector": selector, "value": value})

    async def get_elements(self) -> dict:
        return await self.send_with_retry({"type": "get_elements"})

    async def scroll(self, direction: str = "down", amount: int = 500) -> dict:
        return await self.send_with_retry({"type": "scroll", "direction": direction, "amount": amount})

    async def find_vehicle_link(self) -> dict:
        return await self.send_with_retry({"type": "find_vehicle_link"})

    async def fill_contact_form(self) -> dict:
        return await self.send_with_retry({"type": "fill_contact_form"})

    async def capture_trade_value(self, url: Optional[str] = None) -> dict:
        cmd: dict = {"type": "capture_trade_value"}
        if url:
            cmd["url"] = url
        return await self.send_with_retry(cmd)

    async def capture_feature_click(self, click_text: str, url: Optional[str] = None) -> dict:
        cmd: dict = {"type": "capture_feature_click", "clickText": click_text}
        if url:
            cmd["url"] = url
        return await self.send_with_retry(cmd)

    async def capture_history_report_check(
        self, url: Optional[str] = None, href: Optional[str] = None, platform: str = "",
    ) -> dict:
        cmd: dict = {"type": "capture_history_report_check"}
        if url:
            cmd["url"] = url
        if href:
            cmd["href"] = href
        if platform:
            cmd["platform"] = platform
        return await self.send_with_retry(cmd)
