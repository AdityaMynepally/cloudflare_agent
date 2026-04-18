"""Factory function for creating LLM provider instances."""

import logging

from ai.llm.base import LLMProvider
from ai.llm.config import LLMConfig

logger = logging.getLogger(__name__)


def create_llm_provider(config: LLMConfig | None = None) -> LLMProvider:
    """Create an LLM provider based on configuration.

    Args:
        config: LLMConfig instance. If None, loads from environment.

    Returns:
        Configured LLMProvider instance.

    Raises:
        ValueError: If the configured provider is unknown or misconfigured.
    """
    if config is None:
        config = LLMConfig()

    provider_name = config.LLM_PROVIDER.lower()

    if provider_name == "openai":
        if not config.OPENAI_API_KEY:
            raise ValueError("OpenAI requires OPENAI_API_KEY")
        from ai.llm.openai_provider import OpenAIProvider
        logger.info(f"Creating OpenAI provider (model={config.OPENAI_MODEL})")
        return OpenAIProvider(config)

    elif provider_name == "azure_openai":
        if not config.AZURE_OPENAI_API_KEY or not config.AZURE_OPENAI_ENDPOINT:
            raise ValueError(
                "Azure OpenAI requires AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT"
            )
        from ai.llm.azure_openai import AzureOpenAIProvider
        logger.info(f"Creating Azure OpenAI provider (deployment={config.AZURE_OPENAI_DEPLOYMENT})")
        return AzureOpenAIProvider(config)

    elif provider_name == "google_gemini":
        if not config.GOOGLE_API_KEY:
            raise ValueError("Google Gemini requires GOOGLE_API_KEY")
        from ai.llm.google_gemini import GoogleGeminiProvider
        logger.info(f"Creating Google Gemini provider (model={config.GOOGLE_GEMINI_MODEL})")
        return GoogleGeminiProvider(config)

    elif provider_name == "anthropic_claude":
        if not config.ANTHROPIC_API_KEY:
            raise ValueError("Anthropic Claude requires ANTHROPIC_API_KEY")
        from ai.llm.anthropic_claude import AnthropicClaudeProvider
        logger.info(f"Creating Anthropic Claude provider (model={config.ANTHROPIC_MODEL})")
        return AnthropicClaudeProvider(config)

    else:
        raise ValueError(
            f"Unknown LLM provider: '{provider_name}'. "
            f"Supported: openai, azure_openai, google_gemini, anthropic_claude"
        )
