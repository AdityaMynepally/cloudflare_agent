"""Abstract LLM provider interface with support for chat, tool calling, and vision."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class LLMMessage:
    """A single message in a conversation."""
    role: Role
    content: str = ""
    tool_calls: list["ToolCall"] = field(default_factory=list)
    tool_call_id: Optional[str] = None
    name: Optional[str] = None
    image_base64: Optional[str] = None  # For vision messages
    image_media_type: str = "image/png"

    @classmethod
    def system(cls, content: str) -> "LLMMessage":
        return cls(role=Role.SYSTEM, content=content)

    @classmethod
    def user(cls, content: str, image_base64: Optional[str] = None) -> "LLMMessage":
        return cls(role=Role.USER, content=content, image_base64=image_base64)

    @classmethod
    def assistant(cls, content: str = "", tool_calls: list["ToolCall"] = None) -> "LLMMessage":
        return cls(role=Role.ASSISTANT, content=content, tool_calls=tool_calls or [])

    @classmethod
    def tool_result(cls, tool_call_id: str, content: str, name: str = "") -> "LLMMessage":
        return cls(role=Role.TOOL, content=content, tool_call_id=tool_call_id, name=name)


@dataclass
class ToolDefinition:
    """A tool the LLM can call, using OpenAI function calling format."""
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema


@dataclass
class ToolCall:
    """A tool call returned by the LLM."""
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    """Response from an LLM provider."""
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: Optional[dict[str, int]] = None  # prompt_tokens, completion_tokens, total_tokens

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    async def chat(
        self,
        messages: list[LLMMessage],
        tools: list[ToolDefinition] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Send a chat completion request, optionally with tool definitions.

        Args:
            messages: Conversation messages.
            tools: Optional tool definitions for function calling.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens in response.

        Returns:
            LLMResponse with content and/or tool calls.
        """
        ...

    @abstractmethod
    async def chat_with_vision(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Send a chat completion with image content (vision).

        Messages may contain image_base64 fields for multimodal input.

        Args:
            messages: Conversation messages (may include images).
            temperature: Sampling temperature.
            max_tokens: Maximum tokens in response.

        Returns:
            LLMResponse with content.
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """Clean up resources (e.g., HTTP clients)."""
        ...
