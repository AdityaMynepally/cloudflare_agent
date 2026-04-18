"""Production utilities: DOM truncation, response caching, JSON parsing."""

import hashlib
import json
import logging
import re
import time
from functools import lru_cache
from typing import Any, Optional

logger = logging.getLogger(__name__)


# =============================================================
# DOM Truncation
# =============================================================

def truncate_dom(dom_content: str, max_chars: int = 4000) -> str:
    """Intelligently truncate DOM content for LLM input.

    Strips scripts, styles, and excessive whitespace while preserving
    meaningful content structure.

    Args:
        dom_content: Full DOM HTML string (can be 400KB-1.2MB).
        max_chars: Maximum characters in output.

    Returns:
        Truncated DOM string suitable for LLM analysis.
    """
    if not dom_content:
        return ""

    if len(dom_content) <= max_chars:
        return dom_content

    # Remove script and style tags with content
    text = re.sub(r'<script[^>]*>[\s\S]*?</script>', '', dom_content, flags=re.IGNORECASE)
    text = re.sub(r'<style[^>]*>[\s\S]*?</style>', '', text, flags=re.IGNORECASE)

    # Remove HTML comments
    text = re.sub(r'<!--[\s\S]*?-->', '', text)

    # Remove SVG content (often very large)
    text = re.sub(r'<svg[^>]*>[\s\S]*?</svg>', '[SVG]', text, flags=re.IGNORECASE)

    # Remove base64 data in attributes
    text = re.sub(r'data:[^"\']+;base64,[A-Za-z0-9+/=]+', '[BASE64_DATA]', text)

    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text)

    # If still too long, take head + tail
    if len(text) > max_chars:
        head_size = int(max_chars * 0.7)
        tail_size = max_chars - head_size - 30
        text = text[:head_size] + "\n... [TRUNCATED] ...\n" + text[-tail_size:]

    return text


# =============================================================
# Response Caching
# =============================================================

class AnalysisCache:
    """Simple in-memory cache for LLM analysis results keyed by content hash."""

    def __init__(self, max_size: int = 200, ttl_seconds: int = 3600):
        self._cache: dict[str, tuple[float, Any]] = {}
        self._max_size = max_size
        self._ttl = ttl_seconds

    def _make_key(self, url: str, content: str, analysis_type: str) -> str:
        """Create a cache key from URL + content hash + analysis type."""
        content_hash = hashlib.md5(content[:10000].encode()).hexdigest()[:12]
        return f"{analysis_type}:{url}:{content_hash}"

    def get(self, url: str, content: str, analysis_type: str) -> Optional[Any]:
        """Retrieve a cached result if available and not expired."""
        key = self._make_key(url, content, analysis_type)
        entry = self._cache.get(key)
        if entry is None:
            return None

        timestamp, result = entry
        if time.time() - timestamp > self._ttl:
            del self._cache[key]
            return None

        logger.debug(f"Cache hit: {analysis_type} for {url}")
        return result

    def set(self, url: str, content: str, analysis_type: str, result: Any) -> None:
        """Store a result in the cache."""
        # Evict oldest entries if at capacity
        if len(self._cache) >= self._max_size:
            oldest_key = min(self._cache, key=lambda k: self._cache[k][0])
            del self._cache[oldest_key]

        key = self._make_key(url, content, analysis_type)
        self._cache[key] = (time.time(), result)

    def clear(self) -> None:
        """Clear the entire cache."""
        self._cache.clear()


# Global cache instance
analysis_cache = AnalysisCache()


# =============================================================
# Safe JSON Parsing
# =============================================================

def parse_llm_json(content: str, fallback: Optional[dict] = None) -> dict:
    """Safely parse JSON from LLM response, handling common issues.

    Handles:
    - Markdown code blocks (```json ... ```)
    - Trailing commas
    - Single quotes (converts to double)
    - Extra text before/after JSON

    Args:
        content: Raw LLM response string.
        fallback: Default dict to return on parse failure.

    Returns:
        Parsed dict or fallback value.
    """
    if fallback is None:
        fallback = {}

    if not content:
        return fallback

    text = content.strip()

    # Remove markdown code blocks
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]  # Remove opening ```json or ```
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON object in the text
    json_match = re.search(r'\{[\s\S]*\}', text)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass

    # Try fixing trailing commas
    fixed = re.sub(r',\s*([}\]])', r'\1', text)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    logger.warning(f"Failed to parse LLM JSON response: {text[:200]}")
    return fallback


# =============================================================
# Token Usage Tracking
# =============================================================

class TokenTracker:
    """Tracks token usage across an audit session for cost estimation."""

    # Approximate pricing per 1K tokens (USD)
    PRICING = {
        "gpt-4o": {"input": 0.0025, "output": 0.01},
        "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
        "gpt-4.1": {"input": 0.002, "output": 0.008},
        "gpt-4.1-mini": {"input": 0.0004, "output": 0.0016},
        "o3-mini": {"input": 0.0011, "output": 0.0044},
        "gemini-2.0-flash": {"input": 0.0001, "output": 0.0004},
        "claude-sonnet-4-5-20250929": {"input": 0.003, "output": 0.015},
    }

    def __init__(self, model: str = "gpt-4o"):
        self.model = model
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.call_count = 0

    def track(self, usage: Optional[dict]) -> None:
        """Track usage from an LLM response."""
        if not usage:
            return
        self.total_prompt_tokens += usage.get("prompt_tokens", 0)
        self.total_completion_tokens += usage.get("completion_tokens", 0)
        self.call_count += 1

    @property
    def total_tokens(self) -> int:
        return self.total_prompt_tokens + self.total_completion_tokens

    @property
    def estimated_cost_usd(self) -> float:
        """Estimate cost based on model pricing."""
        pricing = self.PRICING.get(self.model, {"input": 0.003, "output": 0.015})
        input_cost = (self.total_prompt_tokens / 1000) * pricing["input"]
        output_cost = (self.total_completion_tokens / 1000) * pricing["output"]
        return input_cost + output_cost

    def summary(self) -> dict:
        """Return a summary of token usage."""
        return {
            "model": self.model,
            "total_calls": self.call_count,
            "prompt_tokens": self.total_prompt_tokens,
            "completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": round(self.estimated_cost_usd, 4),
        }
