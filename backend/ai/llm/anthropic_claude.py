"""Anthropic Claude LLM provider implementation."""

import json
import logging
from typing import Optional

import anthropic

from ai.llm.base import LLMProvider, LLMMessage, LLMResponse, ToolDefinition, ToolCall, Role
from ai.llm.config import LLMConfig
from ai.llm.retry import with_retry

logger = logging.getLogger(__name__)


class AnthropicClaudeProvider(LLMProvider):
    """Anthropic Claude provider using the anthropic SDK."""

    def __init__(self, config: LLMConfig):
        self.config = config
        self.client = anthropic.AsyncAnthropic(
            api_key=config.ANTHROPIC_API_KEY,
            timeout=config.LLM_TIMEOUT,
        )
        self.model = config.ANTHROPIC_MODEL

    def _build_messages(self, messages: list[LLMMessage]) -> tuple[str, list[dict]]:
        """Convert LLMMessage list to Anthropic API format.

        Returns:
            (system_prompt, messages_list)
        """
        system_prompt = ""
        result = []

        for msg in messages:
            if msg.role == Role.SYSTEM:
                system_prompt += msg.content + "\n"
                continue

            if msg.role == Role.TOOL:
                result.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.tool_call_id,
                            "content": msg.content,
                        }
                    ],
                })
                continue

            if msg.role == Role.ASSISTANT:
                content = []
                if msg.content:
                    content.append({"type": "text", "text": msg.content})
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        content.append({
                            "type": "tool_use",
                            "id": tc.id,
                            "name": tc.name,
                            "input": tc.arguments,
                        })
                result.append({"role": "assistant", "content": content or msg.content})
                continue

            # User message
            if msg.image_base64:
                result.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": msg.image_media_type,
                                "data": msg.image_base64,
                            },
                        },
                        {"type": "text", "text": msg.content},
                    ],
                })
            else:
                result.append({"role": "user", "content": msg.content})

        return system_prompt.strip(), result

    def _build_tools(self, tools: list[ToolDefinition]) -> list[dict]:
        """Convert ToolDefinition list to Anthropic tools format."""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.parameters,
            }
            for tool in tools
        ]

    def _parse_response(self, response) -> LLMResponse:
        """Parse Anthropic API response into LLMResponse."""
        content_text = ""
        tool_calls = []

        for block in response.content:
            if block.type == "text":
                content_text += block.text
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    arguments=block.input if isinstance(block.input, dict) else {},
                ))

        usage = None
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.input_tokens,
                "completion_tokens": response.usage.output_tokens,
                "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
            }

        finish_reason = response.stop_reason or "end_turn"
        if finish_reason == "tool_use":
            finish_reason = "tool_calls"

        return LLMResponse(
            content=content_text,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
        )

    @with_retry(max_retries=3, retriable_exceptions=(anthropic.APIStatusError,))
    async def chat(
        self,
        messages: list[LLMMessage],
        tools: list[ToolDefinition] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        system_prompt, api_messages = self._build_messages(messages)

        kwargs = {
            "model": self.model,
            "messages": api_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        if tools:
            kwargs["tools"] = self._build_tools(tools)

        response = await self.client.messages.create(**kwargs)
        return self._parse_response(response)

    @with_retry(max_retries=3, retriable_exceptions=(anthropic.APIStatusError,))
    async def chat_with_vision(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        system_prompt, api_messages = self._build_messages(messages)

        kwargs = {
            "model": self.model,
            "messages": api_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_prompt:
            kwargs["system"] = system_prompt

        response = await self.client.messages.create(**kwargs)
        return self._parse_response(response)

    async def close(self) -> None:
        await self.client.close()
