"""Broken and oversized image detection via HTTP HEAD requests."""

import asyncio
import logging
from typing import Optional
from urllib.parse import urlparse

import httpx

from ai.agent.state import AuditIssue, IssueCategory, IssueSeverity

logger = logging.getLogger(__name__)

OVERSIZED_THRESHOLD_BYTES = 500 * 1024  # 500 KB
MAX_IMAGES_TO_CHECK = 40
MAX_CONCURRENT = 6


async def check_images(
    images: list[dict],
    base_url: str,
    image_resources: Optional[list[dict]] = None,
    max_concurrent: int = MAX_CONCURRENT,
    timeout: float = 10.0,
) -> tuple[list[dict], list[dict], list]:
    """Check images for broken URLs and excessive file sizes.

    Args:
        images: Image dicts from content-script (src, alt, naturalWidth, naturalHeight).
        base_url: Page URL (for logging context).
        image_resources: PerformanceResourceTiming data with transferSize per URL.
        max_concurrent: Max concurrent HTTP HEAD requests.
        timeout: Per-request timeout in seconds.

    Returns:
        (broken_images, oversized_images, issues)
    """
    if not images:
        return [], [], []

    # Build transfer-size lookup from resource timing
    size_map: dict[str, int] = {}
    if image_resources:
        for res in image_resources:
            url = res.get("url", "")
            size = res.get("transferSize", 0)
            if url and size and size > 0:
                size_map[url] = size

    # Deduplicate by src URL
    seen: set[str] = set()
    unique: list[dict] = []
    for img in images:
        src = img.get("src", "").strip()
        if not src or src.startswith("data:") or src.startswith("blob:"):
            continue
        if src not in seen:
            seen.add(src)
            unique.append(img)

    unique = unique[:MAX_IMAGES_TO_CHECK]
    if not unique:
        return [], [], []

    broken: list[dict] = []
    oversized: list[dict] = []
    semaphore = asyncio.Semaphore(max_concurrent)

    # Browser-like headers prevent 403 bot-blocking on image CDNs
    _HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Referer": base_url,
    }

    async def check_one(img: dict) -> Optional[dict]:
        src = img.get("src", "")
        alt = img.get("alt", "")
        async with semaphore:
            try:
                async with httpx.AsyncClient(
                    timeout=timeout,
                    follow_redirects=True,
                    verify=False,
                    headers=_HEADERS,
                ) as client:
                    resp = await client.head(src)

                    # HEAD blocked, or unsupported (some servers 404 on HEAD but
                    # 200 on GET) → retry with GET (range request to avoid full download)
                    if resp.status_code in (403, 404, 405):
                        resp = await client.get(
                            src,
                            headers={**_HEADERS, "Range": "bytes=0-4095"},
                        )

                    status = resp.status_code
                    content_length = int(resp.headers.get("content-length", 0) or 0)

                # Only 404 / 410 are definitively broken images.
                # 403 = CDN bot-blocking (image loads fine in browser).
                if status in (404, 410):
                    return {
                        "type": "broken",
                        "src": src,
                        "alt": alt,
                        "status_code": status,
                    }
                if status >= 500:
                    return {
                        "type": "broken",
                        "src": src,
                        "alt": alt,
                        "status_code": status,
                        "reason": "server error",
                    }

                # Prefer resource-timing size, fall back to Content-Length header
                transfer_size = size_map.get(src, content_length)
                if transfer_size > OVERSIZED_THRESHOLD_BYTES:
                    return {
                        "type": "oversized",
                        "src": src,
                        "alt": alt,
                        "status_code": status,
                        "file_size_bytes": transfer_size,
                        "file_size_kb": round(transfer_size / 1024, 1),
                    }

                return None
            except httpx.TimeoutException:
                return {
                    "type": "broken",
                    "src": src,
                    "alt": alt,
                    "status_code": 0,
                    "reason": "timeout",
                }
            except Exception as exc:
                logger.debug(f"Image check skipped ({src}): {exc}")
                return None

    results = await asyncio.gather(*[check_one(img) for img in unique])

    for r in results:
        if r is None:
            continue
        entry = {k: v for k, v in r.items() if k != "type"}
        if r["type"] == "broken":
            broken.append(entry)
        elif r["type"] == "oversized":
            oversized.append(entry)

    issues: list = []
    if broken:
        issues.append(AuditIssue(
            category=IssueCategory.PERFORMANCE,
            severity=IssueSeverity.SERIOUS if len(broken) > 3 else IssueSeverity.MODERATE,
            title=f"{len(broken)} broken image{'s' if len(broken) != 1 else ''}",
            description=(
                "Images returning HTTP errors: "
                + ", ".join(b["src"].split("/")[-1][:60] for b in broken[:3])
            ),
            recommendation="Fix or replace broken image URLs to prevent missing images for users.",
        ))

    if oversized:
        total_kb = int(sum(o.get("file_size_kb", 0) for o in oversized))
        issues.append(AuditIssue(
            category=IssueCategory.PERFORMANCE,
            severity=IssueSeverity.MODERATE,
            title=f"{len(oversized)} oversized image{'s' if len(oversized) != 1 else ''} (>500 KB)",
            description=f"Large images found; combined extra weight ≈ {total_kb} KB.",
            recommendation=(
                "Compress images using WebP format, resize to display dimensions, "
                "and enable lazy loading for below-the-fold images."
            ),
        ))

    return broken, oversized, issues
