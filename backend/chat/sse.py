"""SSE stream generator for chat sessions."""

import asyncio
import json
import logging
from typing import AsyncGenerator

from chat.session import session_manager

logger = logging.getLogger(__name__)


async def sse_stream(session_id: str) -> AsyncGenerator[str, None]:
    """Async generator that yields SSE-formatted events from a session's queue.

    Yields strings in the format:
        event: {type}\ndata: {json}\n\n
    """
    queue = session_manager.get_or_create_queue(session_id)
    logger.info(f"SSE stream started for session {session_id}")

    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                # Send keepalive comment to prevent connection timeout
                yield ": keepalive\n\n"
                continue

            if event is None:
                # Sentinel: stream is done
                logger.info(f"SSE stream received done sentinel for {session_id}")
                break

            try:
                data = json.dumps(event.data, default=str)
                logger.debug(f"SSE event: {event.event} ({len(data)} chars)")
                yield f"event: {event.event}\ndata: {data}\n\n"
            except Exception as e:
                logger.error(f"SSE serialization error for {event.event}: {e}", exc_info=True)
                # Send a simplified error event so the frontend isn't left hanging
                error_data = json.dumps({"message": f"Data serialization error: {e}"})
                yield f"event: error\ndata: {error_data}\n\n"

    except asyncio.CancelledError:
        logger.info(f"SSE stream cancelled for {session_id}")
    except Exception as e:
        logger.error(f"SSE stream error for {session_id}: {e}", exc_info=True)
    finally:
        session_manager.cleanup_session(session_id)
