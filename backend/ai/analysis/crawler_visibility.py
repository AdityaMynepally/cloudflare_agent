"""Crawler visibility gap — compares the raw (pre-JavaScript) response body
against the already browser-rendered DOM, using the SAME text extractor on
both sides.

Content present only in the rendered version is invisible to AI-answer-
engine crawlers (GPTBot, ClaudeBot, PerplexityBot, etc.) — none of them
execute JavaScript. Both sides must share one extractor: measuring raw
text with one method and rendered text with another makes the difference
between the methods masquerade as a content gap.

The raw side is fetched primarily by the extension — a `fetch()` issued
inside the already-loaded Chrome tab, which carries real browser cookies
and TLS fingerprint and routinely passes dealer-site WAFs that block a
plain server-side request outright. The backend `httpx` fetch here is a
fallback for when the extension didn't supply one, not the primary path.
"""

import logging
import re
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
}
FETCH_TIMEOUT = 15.0

_SCRIPT_STYLE_RE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_SVG_RE = re.compile(r"<svg[^>]*>.*?</svg>", re.IGNORECASE | re.DOTALL)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def extract_visible_text(html: str) -> str:
    """Strip script/style/svg/comments/tags, collapse whitespace.

    Used for BOTH the raw and the rendered side of the comparison — the
    one methodological requirement that keeps the ratio meaningful.
    """
    if not html:
        return ""
    text = _SCRIPT_STYLE_RE.sub(" ", html)
    text = _SVG_RE.sub(" ", text)
    text = _COMMENT_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def _band(share: float) -> str:
    if share >= 0.8:
        return "good"
    if share >= 0.5:
        return "partial"
    return "poor"


async def _fetch_raw(url: str) -> tuple[str, Optional[str]]:
    """Plain HTTP GET — no JavaScript execution. Returns (status, body)."""
    try:
        async with httpx.AsyncClient(
            timeout=FETCH_TIMEOUT, follow_redirects=True, verify=False, headers=_HEADERS,
        ) as client:
            resp = await client.get(url)
    except Exception:
        return "unknown", None

    if resp.status_code == 200:
        return "success", resp.text
    if resp.status_code in (403, 429, 503):
        return "blocked", None
    return "unknown", None


async def check_crawler_visibility(
    url: str, rendered_html: str, extension_result: Optional[dict] = None,
) -> dict:
    """Compare raw (pre-JS) text volume against rendered-DOM text volume.

    `rendered_html` must be the browser-captured
    document.documentElement.outerHTML for this same URL, from the same
    audit run. `extension_result`, when given, is `{status, text}` from
    the extension's own same-tab fetch of this same URL — see module
    docstring for why that's preferred over a direct backend request.
    """
    if not rendered_html:
        return {"status": "unknown", "note": "No rendered HTML captured for this page"}

    raw_html: Optional[str] = None
    if extension_result and (extension_result.get("status") or 0) == 200:
        raw_html = extension_result.get("text")

    if not raw_html:
        status, raw_html = await _fetch_raw(url)
        if status != "success":
            return {
                "status": "unmeasured",
                "reason": status,
                "note": (
                    "Raw page fetch was blocked or indeterminate — crawler-visible "
                    "share cannot be measured, and must not be reported as 0%."
                ),
            }

    raw_text = extract_visible_text(raw_html)
    rendered_text = extract_visible_text(rendered_html)

    raw_chars = len(raw_text)
    rendered_chars = len(rendered_text)

    if rendered_chars == 0:
        return {"status": "unknown", "note": "No rendered text extracted to compare against"}

    share = min(raw_chars / rendered_chars, 1.0)

    return {
        "status": "measured",
        "raw_chars": raw_chars,
        "rendered_chars": rendered_chars,
        "crawler_visible_share": round(share, 3),
        "band": _band(share),
        "note": (
            f"Non-JS crawlers (GPTBot, ClaudeBot, PerplexityBot, etc.) see roughly "
            f"{round(share * 100)}% of the text a browser renders on this page."
        ),
    }
