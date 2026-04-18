"""LLM configuration via pydantic-settings, driven by environment variables / .env file."""

from pydantic_settings import BaseSettings
from pydantic import Field


class LLMConfig(BaseSettings):
    """Configuration for the LLM subsystem. All values come from environment variables."""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    # Provider selection
    LLM_PROVIDER: str = Field(default="openai", description="LLM provider: openai | azure_openai | google_gemini | anthropic_claude")

    # Feature flags
    AI_ANALYSIS_ENABLED: bool = Field(default=False, description="Enable AI-powered analysis (replaces hardcoded logic)")
    AI_VISION_ENABLED: bool = Field(default=False, description="Enable vision-based screenshot analysis")
    AI_AGENT_ENABLED: bool = Field(default=False, description="Enable autonomous AI agent")

    # OpenAI (standard - api.openai.com)
    OPENAI_API_KEY: str = Field(default="", description="OpenAI API key")
    OPENAI_MODEL: str = Field(default="gpt-4o", description="OpenAI model name")

    # Azure OpenAI (placeholder for future use)
    AZURE_OPENAI_API_KEY: str = Field(default="", description="Azure OpenAI API key")
    AZURE_OPENAI_ENDPOINT: str = Field(default="", description="Azure OpenAI endpoint URL")
    AZURE_OPENAI_DEPLOYMENT: str = Field(default="gpt-4o", description="Azure OpenAI deployment name")
    AZURE_OPENAI_API_VERSION: str = Field(default="2024-10-21", description="Azure OpenAI API version")

    # Google Gemini
    GOOGLE_API_KEY: str = Field(default="", description="Google AI API key")
    GOOGLE_GEMINI_MODEL: str = Field(default="gemini-2.0-flash", description="Gemini model name")

    # Anthropic Claude
    ANTHROPIC_API_KEY: str = Field(default="", description="Anthropic API key")
    ANTHROPIC_MODEL: str = Field(default="claude-sonnet-4-5-20250929", description="Claude model name")

    # Shared settings
    LLM_TEMPERATURE: float = Field(default=0.0, description="Default sampling temperature")
    LLM_MAX_TOKENS: int = Field(default=4096, description="Default max response tokens")
    LLM_TIMEOUT: int = Field(default=60, description="Request timeout in seconds")

    # Agent settings
    AGENT_MAX_ITERATIONS: int = Field(default=30, description="Max tool-calling loop iterations")
    AGENT_MAX_PAGES: int = Field(default=10, description="Max pages agent will visit per audit")
    AGENT_COMMAND_TIMEOUT: int = Field(default=30, description="WebSocket command timeout in seconds")

    # DOM truncation
    DOM_MAX_CHARS: int = Field(default=4000, description="Max DOM chars sent to LLM")
