"""Sprint 5 — Homepage-specific audit checks.

Covers:
  9. Nav menu links → duplicate URL detection
  11. Social media links → 404 check
  12. Social media links → opens in new tab check
"""

import asyncio
import logging
from typing import Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

MAX_CONCURRENT_CHECKS = 8
LINK_TIMEOUT = 10.0


# --------------------------------------------------------------------------- #
#  Shared async URL status checker                                             #
# --------------------------------------------------------------------------- #

async def _check_url_statuses(
    urls: list[str],
    allow_external: bool = True,
    base_url: str = "",
    max_concurrent: int = MAX_CONCURRENT_CHECKS,
    timeout: float = LINK_TIMEOUT,
) -> dict[str, int]:
    """Return {url: http_status_code} for each URL.

    Status 0 = timeout / connection error.
    Only 404/410 are definitively broken; 403 = bot-blocked (link exists).
    """
    if not urls:
        return {}

    base_domain = urlparse(base_url).netloc
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _check_once(client: httpx.AsyncClient, url: str) -> int:
        resp = await client.head(url)
        if resp.status_code in (403, 404, 405):
            # Some servers (esp. ASP.NET/.aspx pages) don't implement
            # HEAD correctly and return 404 even though the page exists —
            # confirm with GET before calling it broken.
            resp = await client.get(url)
        return resp.status_code

    async def check_one(url: str) -> tuple[str, int]:
        async with semaphore:
            last_status = 0
            for attempt in range(2):
                try:
                    async with httpx.AsyncClient(
                        timeout=timeout,
                        follow_redirects=True,
                        verify=False,
                        headers=_BROWSER_HEADERS,
                    ) as client:
                        status = await _check_once(client, url)
                    if not _is_broken(status):
                        return url, status  # confirmed OK — no need to retry
                    last_status = status
                except (httpx.TimeoutException, Exception):
                    last_status = 0
                if attempt == 0:
                    await asyncio.sleep(1.0)  # brief backoff before retrying

            # Only report a status confirmed broken on the retry too. A
            # single transient failure — a burst of concurrent checks against
            # a slow/lightly bot-protected site can produce one even for a
            # link that opens fine in a real browser — should not be enough
            # to flag something as broken.
            return url, last_status

    results = await asyncio.gather(*[check_one(u) for u in urls])
    return dict(results)


def _is_broken(status: int) -> bool:
    return status in (404, 410) or status >= 500


# --------------------------------------------------------------------------- #
#  9. Navigation menu links — duplicate URL detection                         #
# --------------------------------------------------------------------------- #

def find_nav_duplicate_links(nav_links: list[dict]) -> list[dict]:
    """Find navigation links whose URL appears more than once in the nav menu.

    Returns duplicate_links: [{href, text, occurrences}]
    """
    return [
        {"href": l.get("href"), "text": l.get("text", ""), "occurrences": l.get("occurrences", 1)}
        for l in (nav_links or [])
        if (l.get("occurrences") or 1) > 1
    ]


# --------------------------------------------------------------------------- #
#  11 & 12. Social media links — 404 check + new-tab check                     #
# --------------------------------------------------------------------------- #

async def check_social_media_links(
    social_links: list[dict],
) -> tuple[list[dict], list[dict], list[dict]]:
    """Check social media links for broken URLs and missing target='_blank'.

    Returns (broken_links, no_new_tab_links, all_checked).
    broken_links:    [{href, platform, status_code}]
    no_new_tab_links:[{href, platform, text}]
    all_checked:     [{href, platform, status_code, ok, opens_new_tab}]
    """
    if not social_links:
        return [], [], []

    seen: set[str] = set()
    unique = []
    for link in social_links:
        href = (link.get("href") or "").strip()
        if href and href not in seen:
            seen.add(href)
            unique.append(link)

    statuses = await _check_url_statuses([l["href"] for l in unique])

    broken: list[dict] = []
    no_new_tab: list[dict] = []
    all_checked: list[dict] = []

    for link in unique:
        href = link["href"]
        sc = statuses.get(href, 0)
        ok = not _is_broken(sc)
        all_checked.append({
            "href": href,
            "platform": link.get("platform", ""),
            "status_code": sc,
            "ok": ok,
            "opens_new_tab": link.get("opens_new_tab", False),
        })
        if not ok:
            broken.append({"href": href, "platform": link.get("platform", ""), "status_code": sc})
        if not link.get("opens_new_tab"):
            no_new_tab.append({"href": href, "platform": link.get("platform", ""), "text": link.get("text", "")})

    return broken, no_new_tab, all_checked
