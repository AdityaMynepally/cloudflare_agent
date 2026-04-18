"""Standard OpenAI LLM provider implementation (api.openai.com)."""

import json
import logging
from typing import Optional

from openai import AsyncOpenAI, APIStatusError

from ai.llm.base import LLMProvider, LLMMessage, LLMResponse, ToolDefinition, ToolCall, Role
from ai.llm.config import LLMConfig
from ai.llm.retry import with_retry

logger = logging.getLogger(__name__)


class OpenAIProvider(LLMProvider):
    """Standard OpenAI provider using the openai SDK with AsyncOpenAI client."""

    def __init__(self, config: LLMConfig):
        self.config = config
        self.client = AsyncOpenAI(
            api_key=config.OPENAI_API_KEY,
            timeout=config.LLM_TIMEOUT,
        )
        self.model = config.OPENAI_MODEL

    def _build_messages(self, messages: list[LLMMessage]) -> list[dict]:
        """Convert LLMMessage list to OpenAI API format."""
        result = []
        for msg in messages:
            if msg.role == Role.TOOL:
                result.append({
                    "role": "tool",
                    "content": msg.content,
                    "tool_call_id": msg.tool_call_id,
                })
                continue

            entry: dict = {"role": msg.role.value}

            # Vision: multimodal content array
            if msg.image_base64:
                entry["content"] = [
                    {"type": "text", "text": msg.content},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{msg.image_media_type};base64,{msg.image_base64}",
                            "detail": "auto",
                        },
                    },
                ]
            else:
                entry["content"] = msg.content

            # Assistant messages with tool calls
            if msg.role == Role.ASSISTANT and msg.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in msg.tool_calls
                ]

            result.append(entry)
        return result

    def _build_tools(self, tools: list[ToolDefinition]) -> list[dict]:
        """Convert ToolDefinition list to OpenAI tools format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in tools
        ]

    def _parse_response(self, response) -> LLMResponse:
        """Parse OpenAI API response into LLMResponse."""
        choice = response.choices[0]
        message = choice.message

        tool_calls = []
        if message.tool_calls:
            for tc in message.tool_calls:
                try:
                    arguments = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    arguments = {"raw": tc.function.arguments}
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=arguments,
                ))

        usage = None
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        return LLMResponse(
            content=message.content or "",
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
            usage=usage,
        )

    @with_retry(max_retries=3, retriable_exceptions=(APIStatusError,))
    async def chat(
        self,
        messages: list[LLMMessage],
        tools: list[ToolDefinition] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        kwargs = {
            "model": self.model,
            "messages": self._build_messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = self._build_tools(tools)
            kwargs["tool_choice"] = "auto"

        response = await self.client.chat.completions.create(**kwargs)
        return self._parse_response(response)

    @with_retry(max_retries=3, retriable_exceptions=(APIStatusError,))
    async def chat_with_vision(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=self._build_messages(messages),
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return self._parse_response(response)

    async def close(self) -> None:
        await self.client.close()
