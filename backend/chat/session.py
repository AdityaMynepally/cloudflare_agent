"""In-memory chat session manager with SSE event queues."""

import asyncio
import logging
from datetime import datetime
from typing import Any, Optional

from chat.models import ChatMessage, SSEEvent

logger = logging.getLogger(__name__)


class ChatSessionManager:
    """Manages chat sessions, message history, and SSE event queues."""

    def __init__(self):
        self._messages: dict[str, list[ChatMessage]] = {}
        self._event_queues: dict[str, asyncio.Queue] = {}
        self._audit_results: dict[str, dict] = {}

    def get_or_create_queue(self, session_id: str) -> asyncio.Queue:
        """Get or create an SSE event queue for a session."""
        if session_id not in self._event_queues:
            self._event_queues[session_id] = asyncio.Queue()
        return self._event_queues[session_id]

    async def emit_event(self, session_id: str, event: SSEEvent):
        """Push an SSE event to the session's queue."""
        queue = self.get_or_create_queue(session_id)
        await queue.put(event)

    async def emit_done(self, session_id: str):
        """Signal the SSE stream to close."""
        queue = self.get_or_create_queue(session_id)
        await queue.put(None)  # Sentinel

    def add_message(self, message: ChatMessage):
        """Add a message to the session's history."""
        if message.session_id not in self._messages:
            self._messages[message.session_id] = []
        self._messages[message.session_id].append(message)

    def get_messages(self, session_id: str) -> list[ChatMessage]:
        """Get all messages for a session."""
        return self._messages.get(session_id, [])

    def store_audit_result(self, session_id: str, result: dict):
        """Store the final audit result for a session."""
        self._audit_results[session_id] = result

    def get_audit_result(self, session_id: str) -> Optional[dict]:
        """Get stored audit result."""
        return self._audit_results.get(session_id)

    def cleanup_session(self, session_id: str):
        """Remove event queue (but keep messages and results)."""
        self._event_queues.pop(session_id, None)


# Global instance
session_manager = ChatSessionManager()
