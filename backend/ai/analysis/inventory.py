"""Inventory page checks: categorization, broken links/images, history reports, model years."""

import asyncio
import re
from datetime import date
from typing import Optional
from urllib.parse import urlparse

import httpx

TIMEOUT = 8.0
CONCURRENT = 10

NEW_URL_PATTERNS = [
    '/new-vehicles', '/new-cars', '/new-car', '/new-inventory',
    '/shop-new', '/new-models', '/new-trucks', '/new-suvs',
    'searchnew.aspx',  # DealerOn platform
]
USED_URL_PATTERNS = [
    '/used-vehicles', '/used-cars', '/used-car', '/used-inventory',
    '/pre-owned', '/preowned', '/shop-used',
    'searchused.aspx',  # DealerOn platform
]
CPO_URL_PATTERNS = [
    '/certified-pre-owned', '/certified', '/cpo',
]

HISTORY_PROVIDERS = [
    # Patterns matched against both href and link text.
    # Includes internal redirect paths like /dealer-inspire-inventory/autocheck/
    {'platform': 'Carfax',          'patterns': ['carfax.com', 'carfax.', '/carfax/']},
    {'platform': 'AutoCheck',       'patterns': ['autocheck.com', '/autocheck/']},
    {'platform': 'NMVTIS',          'patterns': ['nmvtis.gov', '/nmvtis/']},
    {'platform': 'Vehicle History', 'patterns': ['vehicle-history', '/history-report/']},
]

HISTORY_TEXT_KEYWORDS = [
    'carfax', 'autocheck', 'nmvtis', 'vehicle history', 'history report', 'accident report',
]


# Many dealer platforms give VDPs a URL with a condition + year baked into the
# slug — the SRP-level patterns above never match these, so VDPs fall through
# to "unknown" without this. Two conventions seen in the wild:
#   Dealer.com: /inventory/used-2022-toyota-highlander-hybrid-xle-awd-...-vin/
#   DealerOn:   /new-Bloomington-2025-Honda-Prologue-Elite-3GPKHZRJ2SS527873
# (condition, then an optional 1-2 word city/location segment, then the year)
VDP_CONDITION_RE = re.compile(r'/(new|used|certified|cpo)-(?:[a-z]+-){0,2}(?:19|20)\d{2}-')

# Bare inventory hub URLs (e.g. /inventory/, /vehicles/) list both new and used
# stock with no condition in the path — treat as mixed rather than unknown.
AGGREGATOR_SEGMENTS = {'inventory', 'vehicles'}


def categorize_inventory_url(url: str) -> str:
    """Classify a URL as new / used / cpo / mixed / unknown."""
    url_lower = url.lower()
    has_cpo  = any(p in url_lower for p in CPO_URL_PATTERNS)
    has_new  = any(p in url_lower for p in NEW_URL_PATTERNS)
    has_used = any(p in url_lower for p in USED_URL_PATTERNS)

    if not (has_cpo or has_new or has_used):
        m = VDP_CONDITION_RE.search(url_lower)
        if m:
            condition = m.group(1)
            has_cpo  = condition in ('certified', 'cpo')
            has_new  = condition == 'new'
            has_used = condition == 'used'

    if has_cpo:
        return 'cpo'
    if has_new and not has_used:
        return 'new'
    if has_used and not has_new:
        return 'used'
    if has_new and has_used:
        return 'mixed'

    # Fallback: check exact path segments (e.g. /inventory/new/...)
    path_segments = urlparse(url).path.lower().strip('/').split('/')
    if 'new' in path_segments:
        return 'new'
    if 'used' in path_segments:
        return 'used'

    # Bare aggregator listing (no condition segment) covers both new & used.
    if path_segments and path_segments[-1] in AGGREGATOR_SEGMENTS:
        return 'mixed'

    return 'unknown'


async def check_inventory_graphic_links(
    links: list,
    graphic_links: list,
    base_url: str,
) -> tuple[list, list]:
    """Check inventory graphic / banner links for 404 errors.

    graphic_links: from content script getInventoryGraphicLinks()
    links: all page links (fallback if graphic_links empty)
    Returns (broken, checked).
    """
    base_host = urlparse(base_url).netloc

    # Prefer explicit graphic links; fall back to all same-domain links
    candidates = graphic_links if graphic_links else []
    if not candidates:
        for link in links:
            href = link.get('href', '') or ''
            if not href or href.startswith('#') or href.startswith('javascript:'):
                continue
            try:
                parsed = urlparse(href)
            except Exception:
                continue
            if parsed.netloc and parsed.netloc != base_host:
                continue
            path = parsed.path.lower()
            if any(p in path for p in [
                '/specials', '/offer', '/promo', '/deal', '/inventory/',
                '/vehicle/', '/vdp/', '/new/', '/used/', '/certified/',
            ]):
                candidates.append(link)

    if not candidates:
        return [], []

    seen = set()
    deduped = []
    for c in candidates:
        href = c.get('href', '')
        if href and href not in seen:
            seen.add(href)
            deduped.append(c)

    sem = asyncio.Semaphore(CONCURRENT)

    # Only 404 and 410 are truly "broken" — 403 means our bot is blocked but
    # the page exists; 5xx means server error, not a missing page.
    BROKEN_STATUSES = {404, 410}

    async def _check(link: dict):
        href = link.get('href', '') or ''
        text = (link.get('text', '') or '')[:80]
        async with sem:
            try:
                async with httpx.AsyncClient(
                    timeout=TIMEOUT,
                    follow_redirects=True,
                    headers={'User-Agent': 'Mozilla/5.0 (compatible; WebSentinelBot/1.0)'},
                ) as client:
                    r = await client.head(href)
                    if r.status_code in (403, 404, 405):
                        # Some servers (esp. ASP.NET/.aspx pages) 404 on HEAD but
                        # 200 on GET — confirm before calling it broken.
                        r = await client.get(href)
                    ok = r.status_code not in BROKEN_STATUSES
                    return {'href': href, 'text': text, 'status_code': r.status_code, 'ok': ok}
            except Exception:
                return {'href': href, 'text': text, 'status_code': None, 'ok': False}

    results = await asyncio.gather(*[_check(l) for l in deduped[:30]])
    broken = [r for r in results if not r['ok']]
    return broken, list(results)


def extract_history_reports(links: list) -> dict:
    """Scan page links for vehicle history report links (Carfax, AutoCheck, NMVTIS).

    Matches against href (domain OR internal redirect path like /autocheck/) and
    link text / aria-label keywords so internal redirect URLs are caught.

    Returns {found, count, links: [{platform, href, text, opens_new_tab}]}
    """
    reports = []
    seen: set = set()

    def _detect_platform(href_lower: str, text_lower: str) -> Optional[str]:
        for provider in HISTORY_PROVIDERS:
            if any(p in href_lower for p in provider['patterns']):
                return provider['platform']
        # Fall back to text keywords
        for kw in ['carfax', 'autocheck', 'nmvtis', 'vehicle history', 'history report', 'accident report']:
            if kw in text_lower:
                if 'carfax' == kw:           return 'Carfax'
                if 'autocheck' == kw:         return 'AutoCheck'
                if 'nmvtis' == kw:            return 'NMVTIS'
                return 'Vehicle History'
        return None

    for link in links:
        original_href = link.get('href', '') or ''
        href_lower    = original_href.lower()
        text_lower    = (link.get('text', '') or '').lower()

        if original_href in seen:
            continue

        platform = _detect_platform(href_lower, text_lower)
        if platform:
            seen.add(original_href)
            reports.append({
                'platform': platform,
                'href': original_href,
                'text': (link.get('text', '') or '')[:80],
                'opens_new_tab': link.get('opens_new_tab', False),
            })

    return {'found': bool(reports), 'count': len(reports), 'links': reports}


async def verify_history_report_links(reports: list) -> list:
    """HTTP-verify that history report links are reachable (404/410 = broken)."""
    if not reports:
        return []

    BROKEN_STATUSES = {404, 410}
    sem = asyncio.Semaphore(5)

    async def _verify(report: dict):
        href = report.get('href', '') or ''
        if not href:
            return {**report, 'status_code': None, 'ok': False}
        async with sem:
            try:
                async with httpx.AsyncClient(
                    timeout=TIMEOUT,
                    follow_redirects=True,
                    headers={'User-Agent': 'Mozilla/5.0 (compatible; WebSentinelBot/1.0)'},
                ) as client:
                    r = await client.head(href)
                    if r.status_code in (403, 404, 405):
                        r = await client.get(href)
                    ok = r.status_code not in BROKEN_STATUSES
                    return {**report, 'status_code': r.status_code, 'ok': ok}
            except Exception as e:
                return {**report, 'status_code': None, 'ok': False, 'error': str(e)[:100]}

    return list(await asyncio.gather(*[_verify(r) for r in reports]))


def build_history_reports_check(
    links: list,
    verified_reports: list,
    is_preowned: bool,
    vdp_url: str = '',
    vdp_title: str = '',
) -> dict:
    """Combine raw and verified history report info into a summary dict for one VDP."""
    raw = extract_history_reports(links)
    broken_links = [r for r in verified_reports if not r.get('ok')]
    return {
        'vdp_url': vdp_url,
        'vdp_title': vdp_title,
        'is_preowned': is_preowned,
        'found': raw['found'],
        'count': raw['count'],
        'links': verified_reports if verified_reports else raw['links'],
        'all_work': all(r.get('ok') for r in verified_reports) if verified_reports else None,
        'all_new_tab': all(r.get('opens_new_tab') for r in raw['links']) if raw['links'] else None,
        'missing_new_tab': [r for r in raw['links'] if not r.get('opens_new_tab')],
        'broken_links': broken_links,
    }


def aggregate_history_reports_check(vehicle_checks: list) -> dict:
    """Combine per-VDP history-report checks (from build_history_reports_check)
    across multiple pre-owned vehicles into one summary dict.

    vehicle_checks: one entry per VDP visited, in visit order.
    """
    if not vehicle_checks:
        return {
            'is_preowned': False, 'found': False, 'count': 0, 'links': [],
            'all_work': None, 'all_new_tab': None, 'missing_new_tab': [],
            'broken_links': [], 'vehicles': [], 'vehicles_checked': 0,
        }

    preowned = [v for v in vehicle_checks if v['is_preowned']]

    all_links       = [l for v in preowned for l in v['links']]
    all_broken      = [l for v in preowned for l in v['broken_links']]
    all_missing_tab = [l for v in preowned for l in v['missing_new_tab']]
    work_flags      = [v['all_work'] for v in preowned if v['all_work'] is not None]
    tab_flags       = [v['all_new_tab'] for v in preowned if v['all_new_tab'] is not None]

    return {
        'is_preowned': bool(preowned),
        'found': any(v['found'] for v in preowned),
        'count': sum(v['count'] for v in preowned),
        'links': all_links,
        'all_work': all(work_flags) if work_flags else None,
        'all_new_tab': all(tab_flags) if tab_flags else None,
        'missing_new_tab': all_missing_tab,
        'broken_links': all_broken,
        'vehicles': vehicle_checks,
        'vehicles_checked': len(vehicle_checks),
    }


def extract_model_years(
    vehicle_years: list,
    current_year: Optional[int] = None,
    srp_url: Optional[str] = None,
) -> dict:
    """Parse and classify model years extracted from inventory pages.

    srp_url: the SRP page URL, stored so the UI can construct ?year=XXXX filter links.
    Returns {years, outdated, ok, cutoff_year, current_year, srp_url}
    """
    if current_year is None:
        current_year = date.today().year

    cutoff_year = current_year - 3  # year <= cutoff is outdated

    year_counts: dict[int, int] = {}
    for y in vehicle_years:
        try:
            yr = int(y)
            if 1990 <= yr <= current_year + 2:  # sanity range
                year_counts[yr] = year_counts.get(yr, 0) + 1
        except (ValueError, TypeError):
            pass

    # Extraction scrapes years from page headings and can pick up unrelated
    # 4-digit numbers (a "Since 2000" trust badge, a stray date elsewhere) when
    # a site's markup doesn't match the expected vehicle-card selectors. Real
    # inventory years repeat once per listed vehicle; a scraping artifact shows
    # up as an isolated singleton. If the data has a genuine cluster (some year
    # appearing more than once), drop singleton years as unreliable noise —
    # but only then, since on a small lot every year may legitimately be a
    # singleton and we have no signal to distinguish that case from noise.
    if any(cnt > 1 for cnt in year_counts.values()):
        year_counts = {yr: cnt for yr, cnt in year_counts.items() if cnt > 1}

    all_years = [
        {'year': yr, 'count': cnt}
        for yr, cnt in sorted(year_counts.items(), reverse=True)
    ]
    outdated = [y for y in all_years if y['year'] <= cutoff_year]
    ok_years  = [y for y in all_years if y['year'] >  cutoff_year]

    return {
        'years': all_years,
        'outdated': outdated,
        'ok': ok_years,
        'current_year': current_year,
        'cutoff_year': cutoff_year,
        'has_outdated': bool(outdated),
        'srp_url': srp_url or '',
    }
