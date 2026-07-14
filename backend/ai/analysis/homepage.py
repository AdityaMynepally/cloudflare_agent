"""Sprint 5 — Homepage-specific audit checks.

Covers:
  1. Carousel slide links → 404 check
  2. Carousel slide links → relevance check
  3. CTA button/banner links → 404 check
  4. Non-slide/non-logo homepage images → broken source check
  5/6. Carousel slide dimension consistency (desktop & mobile)
  8. Nav menu links → 404 check
  9. Nav menu links → duplicate URL detection
  10. Header logo → links to homepage
  11. Social media links → 404 check
  12. Social media links → opens in new tab check
"""

import asyncio
import logging
import re
from typing import Optional
from urllib.parse import urlparse, urldefrag

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

    async def check_one(url: str) -> tuple[str, int]:
        async with semaphore:
            try:
                async with httpx.AsyncClient(
                    timeout=timeout,
                    follow_redirects=True,
                    verify=False,
                    headers=_BROWSER_HEADERS,
                ) as client:
                    resp = await client.head(url)
                    if resp.status_code in (403, 404, 405):
                        # Some servers (esp. ASP.NET/.aspx pages) don't implement
                        # HEAD correctly and return 404 even though the page exists —
                        # confirm with GET before calling it broken.
                        resp = await client.get(url)
                    return url, resp.status_code
            except httpx.TimeoutException:
                return url, 0
            except Exception:
                return url, 0

    results = await asyncio.gather(*[check_one(u) for u in urls])
    return dict(results)


def _is_broken(status: int) -> bool:
    return status in (404, 410) or status >= 500


# --------------------------------------------------------------------------- #
#  1. Carousel slide links — 404 check                                         #
# --------------------------------------------------------------------------- #

async def check_carousel_links(
    carousel_data: list[dict],
    base_url: str,
) -> tuple[list[dict], list[dict]]:
    """Check carousel slide links for 404/410.

    Returns (broken, all_checked).
    broken:      [{href, slide_text, status_code, carousel_index}]
    all_checked: [{href, slide_text, status_code, ok, carousel_index}]
    """
    if not carousel_data:
        return [], []

    slide_links: list[dict] = []
    seen: set[str] = set()
    for ci, carousel in enumerate(carousel_data):
        for slide in carousel.get("slides", []):
            href = slide.get("href")
            if not href or href in seen:
                continue
            seen.add(href)
            slide_links.append({
                "href": href,
                "slide_text": (slide.get("slide_text") or "")[:200],
                "carousel_index": ci,
            })

    if not slide_links:
        return [], []

    statuses = await _check_url_statuses([s["href"] for s in slide_links], base_url=base_url)
    broken: list[dict] = []
    all_checked: list[dict] = []
    for entry in slide_links:
        sc = statuses.get(entry["href"], 0)
        ok = not _is_broken(sc)
        all_checked.append({**entry, "status_code": sc, "ok": ok})
        if not ok:
            broken.append({**entry, "status_code": sc})

    return broken, all_checked


# --------------------------------------------------------------------------- #
#  2. Carousel slide links — relevance check                                   #
# --------------------------------------------------------------------------- #

# Known vehicle model terms → expected URL keyword variations
_VEHICLE_MODELS = [
    ("f-150", ["f-150", "f150", "ford-f-150"]),
    ("f-250", ["f-250", "f250"]),
    ("f-350", ["f-350", "f350"]),
    ("silverado", ["silverado"]),
    ("sierra", ["sierra"]),
    ("ram 1500", ["ram-1500", "ram1500", "ram"]),
    ("tacoma", ["tacoma"]),
    ("tundra", ["tundra"]),
    ("camry", ["camry"]),
    ("corolla", ["corolla"]),
    ("civic", ["civic"]),
    ("accord", ["accord"]),
    ("pilot", ["pilot"]),
    ("odyssey", ["odyssey"]),
    ("ridgeline", ["ridgeline"]),
    ("escape", ["escape"]),
    ("explorer", ["explorer"]),
    ("expedition", ["expedition"]),
    ("mustang", ["mustang"]),
    ("bronco", ["bronco"]),
    ("equinox", ["equinox"]),
    ("traverse", ["traverse"]),
    ("colorado", ["colorado"]),
    ("canyon", ["canyon"]),
    ("highlander", ["highlander"]),
    ("santa fe", ["santa-fe", "santafe"]),
    ("tucson", ["tucson"]),
    ("palisade", ["palisade"]),
    ("telluride", ["telluride"]),
    ("sorento", ["sorento"]),
    ("sportage", ["sportage"]),
    ("cx-5", ["cx-5", "cx5"]),
    ("cx-9", ["cx-9", "cx9"]),
    ("mdx", ["mdx"]),
    ("rdx", ["rdx"]),
    ("pathfinder", ["pathfinder"]),
    ("rogue", ["rogue"]),
    ("murano", ["murano"]),
    ("frontier", ["frontier"]),
]

_OFFER_TOPIC_MAP = {
    "service":   ["service", "maintenance", "repair", "oil-change"],
    "finance":   ["finance", "financing", "apply", "credit"],
    "trade":     ["trade", "sell-your"],
    "certified": ["certified", "cpo", "pre-owned"],
    "parts":     ["parts", "accessories"],
    "new":       ["new-vehicles", "new-cars", "new-inventory", "new/"],
    "used":      ["used-vehicles", "used-cars", "used-inventory", "pre-owned"],
    "inventory": ["inventory", "search", "browse", "shop-vehicles"],
    "specials":  ["specials", "offers", "deals", "incentives", "promotions"],
    "ev":        ["electric", "ev", "hybrid", "plug-in"],
}


def assess_carousel_relevance(carousel_data: list[dict]) -> list[dict]:
    """Heuristic: does the slide topic (extracted from text) match the link URL?

    Returns list of {href, slide_text, relevant (bool|None), reason}.
    relevant=None means we couldn't determine the topic.
    """
    results = []

    for carousel in carousel_data:
        for slide in carousel.get("slides", []):
            href = slide.get("href") or ""
            slide_text = (slide.get("slide_text") or "").lower()
            link_text = (slide.get("link_text") or "").lower()

            if not href:
                results.append({
                    "href": None,
                    "slide_text": slide.get("slide_text") or "",
                    "relevant": None,
                    "reason": "slide has no link",
                })
                continue

            href_lower = href.lower()

            # 1. Vehicle model match
            matched_model = None
            for model_key, url_variants in _VEHICLE_MODELS:
                if model_key in slide_text or model_key in link_text:
                    matched_model = (model_key, url_variants)
                    break

            if matched_model:
                model_key, url_variants = matched_model
                in_url = any(v in href_lower for v in url_variants)
                results.append({
                    "href": href,
                    "slide_text": slide.get("slide_text") or "",
                    "relevant": in_url,
                    "reason": (
                        f'Slide mentions "{model_key}" — URL matches' if in_url
                        else f'Slide mentions "{model_key}" but URL does not reference it'
                    ),
                })
                continue

            # 2. Offer / topic match
            matched_topic = None
            for topic, url_kws in _OFFER_TOPIC_MAP.items():
                if topic in slide_text or topic in link_text:
                    matched_topic = (topic, url_kws)
                    break

            if matched_topic:
                topic, url_kws = matched_topic
                in_url = any(k in href_lower for k in url_kws)
                results.append({
                    "href": href,
                    "slide_text": slide.get("slide_text") or "",
                    "relevant": in_url,
                    "reason": (
                        f'Slide topic "{topic}" — URL matches' if in_url
                        else f'Slide topic "{topic}" but URL does not look related'
                    ),
                })
                continue

            # 3. Cannot determine topic
            results.append({
                "href": href,
                "slide_text": slide.get("slide_text") or "",
                "relevant": None,
                "reason": "slide topic could not be determined from text",
            })

    return results


# --------------------------------------------------------------------------- #
#  3. CTA button/banner links — 404 check                                      #
# --------------------------------------------------------------------------- #

async def check_cta_links(
    cta_links: list[dict],
    base_url: str,
) -> tuple[list[dict], list[dict]]:
    """Check CTA button/banner links for 404/410.

    Returns (broken, all_checked).
    broken:      [{href, text, status_code}]
    all_checked: [{href, text, status_code, ok}]
    """
    if not cta_links:
        return [], []

    seen: set[str] = set()
    unique = []
    for c in cta_links:
        href = (c.get("href") or "").strip()
        if href and href not in seen:
            seen.add(href)
            unique.append(c)

    statuses = await _check_url_statuses([c["href"] for c in unique], base_url=base_url)
    broken: list[dict] = []
    all_checked: list[dict] = []
    for entry in unique:
        sc = statuses.get(entry["href"], 0)
        ok = not _is_broken(sc)
        all_checked.append({"href": entry["href"], "text": entry.get("text", ""), "status_code": sc, "ok": ok})
        if not ok:
            broken.append({"href": entry["href"], "text": entry.get("text", ""), "status_code": sc})

    return broken, all_checked


# --------------------------------------------------------------------------- #
#  4. Non-slide / non-logo homepage images — broken source check               #
# --------------------------------------------------------------------------- #

async def check_homepage_content_images(
    images: list[dict],
    base_url: str,
    image_resources: Optional[list[dict]] = None,
    max_concurrent: int = MAX_CONCURRENT_CHECKS,
    timeout: float = LINK_TIMEOUT,
) -> list[dict]:
    """Check homepage images that are NOT inside a carousel and NOT logo images.

    Returns list of broken images: {src, alt, status_code}.
    """
    if not images:
        return []

    content_imgs = [
        img for img in images
        if not img.get("is_in_carousel") and not img.get("is_logo")
    ]

    if not content_imgs:
        return []

    seen: set[str] = set()
    unique = []
    for img in content_imgs:
        src = (img.get("src") or "").strip()
        if src and src not in seen:
            seen.add(src)
            unique.append(img)

    unique = unique[:30]
    semaphore = asyncio.Semaphore(max_concurrent)

    _IMG_HEADERS = {
        **_BROWSER_HEADERS,
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Referer": base_url,
    }

    async def check_one(img: dict) -> Optional[dict]:
        src = img.get("src", "")
        async with semaphore:
            try:
                async with httpx.AsyncClient(
                    timeout=timeout, follow_redirects=True, verify=False, headers=_IMG_HEADERS,
                ) as client:
                    resp = await client.head(src)
                    if resp.status_code in (403, 404, 405):
                        resp = await client.get(src, headers={**_IMG_HEADERS, "Range": "bytes=0-4095"})
                    if resp.status_code in (404, 410) or resp.status_code >= 500:
                        return {"src": src, "alt": img.get("alt", ""), "status_code": resp.status_code}
                    return None
            except httpx.TimeoutException:
                return None
            except Exception:
                return None

    results = await asyncio.gather(*[check_one(img) for img in unique])
    return [r for r in results if r is not None]


# --------------------------------------------------------------------------- #
#  5 & 6. Carousel slide dimension consistency                                 #
# --------------------------------------------------------------------------- #

def analyze_slide_dimensions(carousel_data: list[dict]) -> list[dict]:
    """Check each carousel for inconsistent slide dimensions.

    Returns list of issues: {carousel_index, expected_w, expected_h, inconsistent_slides}.
    """
    issues = []
    for ci, carousel in enumerate(carousel_data):
        slides = carousel.get("slides", [])
        dims = [(s.get("width", 0), s.get("height", 0)) for s in slides if s.get("width") and s.get("height")]
        if len(dims) < 2:
            continue

        # Use most-common dimension as expected
        from collections import Counter
        most_common_dim, _ = Counter(dims).most_common(1)[0]
        exp_w, exp_h = most_common_dim
        inconsistent = [
            {"index": i, "width": w, "height": h}
            for i, (w, h) in enumerate(dims)
            if (w, h) != most_common_dim
        ]
        if inconsistent:
            issues.append({
                "carousel_index": ci,
                "carousel_selector": carousel.get("selector", ""),
                "expected_width": exp_w,
                "expected_height": exp_h,
                "inconsistent_slides": inconsistent,
            })

    return issues


# --------------------------------------------------------------------------- #
#  8 & 9. Navigation menu links — 404 check + duplicate detection              #
# --------------------------------------------------------------------------- #

async def check_nav_links(
    nav_links: list[dict],
    base_url: str,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Check navigation links for 404s and duplicate URLs.

    Returns (broken_links, duplicate_links, all_checked).
    broken_links: [{href, text, status_code}]
    duplicate_links: [{href, text, occurrences}]
    all_checked: [{href, text, status_code, ok}]
    """
    if not nav_links:
        return [], [], []

    # Deduplicate for HTTP checks; keep first occurrence
    seen_hrefs: set[str] = set()
    unique_links = []
    for link in nav_links:
        href = (link.get("href") or "").strip()
        href = urldefrag(href)[0] if href else href
        if href and href not in seen_hrefs:
            seen_hrefs.add(href)
            unique_links.append({**link, "href": href})

    statuses = await _check_url_statuses(
        [l["href"] for l in unique_links], base_url=base_url
    )

    broken: list[dict] = []
    all_checked: list[dict] = []
    for l in unique_links:
        sc = statuses.get(l["href"], 0)
        ok = not _is_broken(sc)
        all_checked.append({"href": l["href"], "text": l.get("text", ""), "status_code": sc, "ok": ok})
        if not ok:
            broken.append({"href": l["href"], "text": l.get("text", ""), "status_code": sc})

    duplicates = [
        {"href": l.get("href"), "text": l.get("text", ""), "occurrences": l.get("occurrences", 1)}
        for l in nav_links
        if (l.get("occurrences") or 1) > 1
    ]

    return broken, duplicates, all_checked


# --------------------------------------------------------------------------- #
#  10. Header logo — links to homepage                                         #
# --------------------------------------------------------------------------- #

def check_header_logo(logo_info: dict, base_url: str) -> dict:
    """Evaluate header logo check result.

    Returns enriched dict with pass/fail status.
    """
    if not logo_info:
        return {"has_link": False, "href": None, "links_to_homepage": False, "status": "fail", "note": "No logo link detected"}

    has_link = logo_info.get("has_link", False)
    links_home = logo_info.get("links_to_homepage", False)

    if not has_link:
        status = "fail"
        note = "Header logo has no clickable link"
    elif not links_home:
        status = "fail"
        note = f"Logo links to {logo_info.get('href', 'unknown')} — not the homepage"
    else:
        status = "pass"
        note = "Header logo links to homepage"

    return {
        "has_link": has_link,
        "href": logo_info.get("href"),
        "links_to_homepage": links_home,
        "status": status,
        "note": note,
    }


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
