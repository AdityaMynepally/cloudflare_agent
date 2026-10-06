"""Crawler access audit — robots.txt and llms.txt (AI-guidance file).

Site-level checks (not per page). A rate-limited or blocked request must
be reported as indeterminate, never as "the file is missing" — a 429 is
not a 404.

Fetched primarily from the extension (a `fetch()` issued inside the
already-loaded Chrome tab, which carries real browser cookies and TLS
fingerprint): dealer-site WAFs that block a plain server-side request
routinely let that same tab's own requests through. The backend `httpx`
fetch here is a fallback for when the extension didn't supply a result
(e.g. an older content-script build), not the primary path.
"""

import logging
from typing import Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
}

FETCH_TIMEOUT = 10.0

# Both files are normally a few KB; this is just a sanity ceiling so a
# pathological llms.txt can't bloat the session payload.
MAX_RAW_TEXT_CHARS = 50_000

# AI/answer-engine crawlers relevant to AEO readiness — most of these do
# not execute JavaScript, so robots.txt rules and llms.txt specifically
# shape what they can read.
AI_CRAWLER_AGENTS = [
    "GPTBot", "OAI-SearchBot", "ChatGPT-User",
    "ClaudeBot", "Claude-Web", "anthropic-ai",
    "PerplexityBot", "Perplexity-User",
    "Google-Extended", "CCBot", "Bytespider",
]


def _origin(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _status_label(status_code: int) -> str:
    """'present' | 'absent' | 'unknown' — 'unknown' covers 429/503/0/
    timeouts/anything else that is not a definitive 200 or 404/410. It
    must never be treated as equivalent to 'absent'.
    """
    if status_code == 200:
        return "present"
    if status_code in (404, 410):
        return "absent"
    return "unknown"


async def _fetch(url: str) -> tuple[str, Optional[str]]:
    """Backend-side GET fallback. Returns (status_label, body)."""
    try:
        async with httpx.AsyncClient(
            timeout=FETCH_TIMEOUT, follow_redirects=True, verify=False, headers=_HEADERS,
        ) as client:
            resp = await client.get(url)
    except Exception:
        return "unknown", None

    label = _status_label(resp.status_code)
    return label, resp.text if label == "present" else None


async def _resolve(url: str, extension_result: Optional[dict]) -> tuple[str, Optional[str]]:
    """Prefer the extension-supplied fetch result; fall back to a direct
    backend request only when the extension didn't provide one.
    """
    if extension_result is not None:
        label = _status_label(extension_result.get("status") or 0)
        if label == "present":
            return label, extension_result.get("text")
        if label == "absent":
            return label, None
        # Extension fetch itself came back indeterminate (rare — it's
        # genuine browser traffic) — one backend attempt as a last resort.

    return await _fetch(url)


def parse_robots_txt(text: str) -> dict:
    """Parse robots.txt into per-agent Allow/Disallow rules + sitemap list.

    A minimal grouping parser: a run of consecutive User-agent: lines
    shares the Allow/Disallow lines that follow, until the next User-
    agent line(s) start a new group. Sitemap: lines are site-wide.
    """
    agents: dict[str, dict] = {}
    sitemaps: list[str] = []
    current_agents: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()

        if key == "user-agent":
            if current_agents and (
                agents.get(current_agents[0], {}).get("allow")
                or agents.get(current_agents[0], {}).get("disallow")
            ):
                current_agents = []
            current_agents.append(value)
            for a in current_agents:
                agents.setdefault(a, {"allow": [], "disallow": []})
        elif key == "disallow" and current_agents and value:
            for a in current_agents:
                agents[a]["disallow"].append(value)
        elif key == "allow" and current_agents and value:
            for a in current_agents:
                agents[a]["allow"].append(value)
        elif key == "sitemap" and value:
            sitemaps.append(value)

    return {"agents": agents, "sitemaps": sitemaps}


def _agent_blocked_paths(agents: dict, agent_name: str) -> list[str]:
    """Disallow rules for agent_name, falling back to the '*' group."""
    for declared, rules in agents.items():
        if declared.lower() == agent_name.lower():
            return rules["disallow"]
    wildcard = agents.get("*")
    return wildcard["disallow"] if wildcard else []


async def check_robots_txt(base_url: str, extension_result: Optional[dict] = None) -> dict:
    """Parse robots.txt; report AI-crawler-specific blocking.

    `extension_result`, when given, is `{status, text}` from the
    extension's own same-tab fetch — see module docstring for why that's
    preferred over a direct backend request.
    """
    url = f"{_origin(base_url)}/robots.txt"
    status, body = await _resolve(url, extension_result)

    if status != "present":
        return {"status": status, "url": url, "sitemaps": [], "ai_crawler_rules": {}, "raw_text": None}

    parsed = parse_robots_txt(body)
    declared_lower = {a.lower() for a in parsed["agents"]}
    ai_rules = {
        agent: _agent_blocked_paths(parsed["agents"], agent)
        for agent in AI_CRAWLER_AGENTS
        if agent.lower() in declared_lower
    }
    fully_blocked = [agent for agent, paths in ai_rules.items() if "/" in paths]

    return {
        "status": "present",
        "url": url,
        "sitemaps": parsed["sitemaps"],
        "ai_crawler_rules": ai_rules,
        "ai_crawlers_fully_blocked": fully_blocked,
        "raw_text": body[:MAX_RAW_TEXT_CHARS],
    }


async def check_llms_txt(base_url: str, extension_result: Optional[dict] = None) -> dict:
    """llms.txt (AI-guidance file). present/absent/unknown — a 429 is never 'absent'.

    `extension_result`, when given, is `{status, text}` from the
    extension's own same-tab fetch — see module docstring for why that's
    preferred over a direct backend request.
    """
    url = f"{_origin(base_url)}/llms.txt"
    status, body = await _resolve(url, extension_result)

    result = {"status": status, "url": url}
    if status == "present":
        result["size_bytes"] = len(body.encode("utf-8"))
        result["raw_text"] = body[:MAX_RAW_TEXT_CHARS]
    return result
