"""Broken link detection via async HTTP HEAD/GET requests."""

import asyncio
import logging
from typing import Optional
from urllib.parse import urldefrag, urlparse

import httpx

from ai.agent.state import AuditIssue, IssueCategory, IssueSeverity

logger = logging.getLogger(__name__)

# Browser-like headers to avoid false 403s from bot-blocking
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


async def check_links(
    links: list[dict],
    base_url: str,
    max_concurrent: int = 10,
    timeout: float = 10.0,
) -> tuple[list[dict], list[AuditIssue]]:
    """Check links for broken ones using HEAD (with GET fallback) requests.

    Only 404 / 410 responses are reported as broken links. 403 responses are
    skipped because many sites block automated HEAD requests while serving the
    page fine in a real browser.

    Returns:
        Tuple of (broken_links_details, issues)
    """
    if not links:
        return [], []

    base_domain = urlparse(base_url).netloc
    # Strip fragment from base URL for comparison
    base_url_no_frag = urldefrag(base_url)[0]

    # Filter to internal links only, deduplicate by normalized (fragment-stripped) URL
    seen: set[str] = set()
    internal_links: list[str] = []
    for link in links:
        href = link.get("href", "")
        if not href or href.startswith("javascript:"):
            continue
        if href.startswith("tel:") or href.startswith("mailto:"):
            continue
        try:
            parsed = urlparse(href)
            # Skip pure anchor-only hrefs
            if not parsed.scheme and not parsed.netloc and not parsed.path:
                continue
            # Skip external domains
            if parsed.netloc and parsed.netloc != base_domain:
                continue
            # Normalize: strip fragment so #section links don't produce
            # duplicate (and false-positive) checks
            normalized = urldefrag(href)[0]
            if not normalized:
                continue
            if normalized not in seen:
                seen.add(normalized)
                internal_links.append(normalized)
        except Exception:
            continue

    # Cap at 30 links to check
    internal_links = internal_links[:30]

    if not internal_links:
        return [], []

    semaphore = asyncio.Semaphore(max_concurrent)

    async def check_one(url: str) -> Optional[dict]:
        async with semaphore:
            try:
                async with httpx.AsyncClient(
                    timeout=timeout,
                    follow_redirects=True,
                    verify=False,
                    headers=_BROWSER_HEADERS,
                ) as client:
                    resp = await client.head(url)

                    # HEAD blocked by server → retry with GET
                    if resp.status_code in (403, 405):
                        resp = await client.get(url)

                    # Only 404 / 410 are definitively broken.
                    # 403 = bot-blocked (page exists), 5xx = transient server error
                    if resp.status_code in (404, 410):
                        return {
                            "url": url,
                            "status_code": resp.status_code,
                            "reason": "Not found",
                        }
                    if resp.status_code >= 500:
                        return {
                            "url": url,
                            "status_code": resp.status_code,
                            "reason": "Server error",
                        }
            except httpx.TimeoutException:
                return {"url": url, "status_code": 0, "reason": "timeout"}
            except Exception as e:
                return {"url": url, "status_code": 0, "reason": str(e)[:80]}
            return None

    results = await asyncio.gather(*[check_one(url) for url in internal_links])
    broken = [r for r in results if r is not None]

    issues: list[AuditIssue] = []
    if broken:
        broken_404 = [b for b in broken if b["status_code"] in (404, 410)]
        broken_5xx = [b for b in broken if b["status_code"] >= 500]

        if broken_404:
            urls_str = ", ".join(b["url"].split("/")[-1] or b["url"] for b in broken_404[:3])
            issues.append(AuditIssue(
                category=IssueCategory.LINKS,
                severity=IssueSeverity.SERIOUS if len(broken_404) > 2 else IssueSeverity.MODERATE,
                title=f"{len(broken_404)} broken links (404/410)",
                description=f"Found page-not-found errors: {urls_str}",
                recommendation="Fix or remove broken links to improve user experience and SEO.",
            ))

        if broken_5xx:
            issues.append(AuditIssue(
                category=IssueCategory.LINKS,
                severity=IssueSeverity.MINOR,
                title=f"{len(broken_5xx)} links with server errors",
                description="Some links returned 5xx server errors.",
                recommendation="Review and fix links that return server errors.",
            ))

    return broken, issues
