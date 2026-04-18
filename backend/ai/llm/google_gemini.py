"""Google Gemini LLM provider implementation."""

import json
import logging
import uuid

from google import genai
from google.genai import types as genai_types

from ai.llm.base import LLMProvider, LLMMessage, LLMResponse, ToolDefinition, ToolCall, Role
from ai.llm.config import LLMConfig
from ai.llm.retry import with_retry

logger = logging.getLogger(__name__)


class GoogleGeminiProvider(LLMProvider):
    """Google Gemini provider using the google-genai SDK."""

    def __init__(self, config: LLMConfig):
        self.config = config
        self.client = genai.Client(api_key=config.GOOGLE_API_KEY)
        self.model = config.GOOGLE_GEMINI_MODEL

    def _build_contents(self, messages: list[LLMMessage]) -> list[genai_types.Content]:
        """Convert LLMMessage list to Gemini content format."""
        contents = []
        system_text = ""

        for msg in messages:
            if msg.role == Role.SYSTEM:
                system_text += msg.content + "\n"
                continue

            if msg.role == Role.TOOL:
                contents.append(genai_types.Content(
                    role="function",
                    parts=[genai_types.Part(
                        function_response=genai_types.FunctionResponse(
                            name=msg.name or "tool",
                            response={"result": msg.content},
                        )
                    )],
                ))
                continue

            role = "user" if msg.role == Role.USER else "model"
            parts = []

            if msg.content:
                parts.append(genai_types.Part(text=msg.content))

            if msg.image_base64:
                import base64
                image_bytes = base64.b64decode(msg.image_base64)
                parts.append(genai_types.Part(
                    inline_data=genai_types.Blob(
                        mime_type=msg.image_media_type,
                        data=image_bytes,
                    )
                ))

            if msg.role == Role.ASSISTANT and msg.tool_calls:
                for tc in msg.tool_calls:
                    parts.append(genai_types.Part(
                        function_call=genai_types.FunctionCall(
                            name=tc.name,
                            args=tc.arguments,
                        )
                    ))

            if parts:
                contents.append(genai_types.Content(role=role, parts=parts))

        return contents, system_text

    def _build_tools(self, tools: list[ToolDefinition]) -> list[genai_types.Tool]:
        """Convert ToolDefinition list to Gemini tools format."""
        declarations = []
        for tool in tools:
            declarations.append(genai_types.FunctionDeclaration(
                name=tool.name,
                description=tool.description,
                parameters=tool.parameters,
            ))
        return [genai_types.Tool(function_declarations=declarations)]

    def _parse_response(self, response) -> LLMResponse:
        """Parse Gemini API response into LLMResponse."""
        candidate = response.candidates[0]
        content_text = ""
        tool_calls = []

        for part in candidate.content.parts:
            if hasattr(part, 'text') and part.text:
                content_text += part.text
            if hasattr(part, 'function_call') and part.function_call:
                fc = part.function_call
                tool_calls.append(ToolCall(
                    id=f"call_{uuid.uuid4().hex[:8]}",
                    name=fc.name,
                    arguments=dict(fc.args) if fc.args else {},
                ))

        usage = None
        if hasattr(response, 'usage_metadata') and response.usage_metadata:
            um = response.usage_metadata
            usage = {
                "prompt_tokens": getattr(um, 'prompt_token_count', 0),
                "completion_tokens": getattr(um, 'candidates_token_count', 0),
                "total_tokens": getattr(um, 'total_token_count', 0),
            }

        finish_reason = "stop"
        if tool_calls:
            finish_reason = "tool_calls"

        return LLMResponse(
            content=content_text,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
        )

    @with_retry(max_retries=3, retriable_exceptions=(Exception,))
    async def chat(
        self,
        messages: list[LLMMessage],
        tools: list[ToolDefinition] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        contents, system_text = self._build_contents(messages)

        config = genai_types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
        )
        if system_text:
            config.system_instruction = system_text
        if tools:
            config.tools = self._build_tools(tools)

        response = await self.client.aio.models.generate_content(
            model=self.model,
            contents=contents,
            config=config,
        )
        return self._parse_response(response)

    @with_retry(max_retries=3, retriable_exceptions=(Exception,))
    async def chat_with_vision(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        contents, system_text = self._build_contents(messages)

        config = genai_types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
        )
        if system_text:
            config.system_instruction = system_text

        response = await self.client.aio.models.generate_content(
            model=self.model,
            contents=contents,
            config=config,
        )
        return self._parse_response(response)

    async def close(self) -> None:
        pass  # google-genai client doesn't need explicit cleanup
