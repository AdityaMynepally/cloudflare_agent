"""Sitemap-based URL discovery, template classification and spread sampling.

On-page link crawling alone misses most of a dealership site's actual
catalog depth (deep inventory, department/specials pages a few clicks
from the nav). The sitemap(s) declared in robots.txt carry the fuller
URL inventory; this module classifies that inventory into page templates
and samples a few extra pages SPREAD ACROSS each template — not just the
first N — so a handful of stale pages at the head of one template group
can't dominate the sample.
"""

import logging
import re
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

# Ordered rules — first match wins (TR-22). Keep more specific patterns
# before more generic ones (e.g. "vdp"/"vehicle" before a generic
# "inventory" catch-all).
_TEMPLATE_RULES: list[tuple[str, re.Pattern]] = [
    ("vdp",       re.compile(r"/(vdp|vehicle|detail)s?/", re.IGNORECASE)),
    ("srp",       re.compile(r"/(srp|inventory|new-vehicles|used-vehicles|new-cars|used-cars|certified-pre-owned|cpo)/?", re.IGNORECASE)),
    ("specials",  re.compile(r"/(specials?|offers?|incentives?|deals?)/", re.IGNORECASE)),
    ("service",   re.compile(r"/(service|maintenance|repair|oil-change)/", re.IGNORECASE)),
    ("parts",     re.compile(r"/parts/", re.IGNORECASE)),
    ("finance",   re.compile(r"/(finance|financing|credit-app|apply)/", re.IGNORECASE)),
    ("trade-in",  re.compile(r"/(trade-in|value-your-trade|sell-your-car)/", re.IGNORECASE)),
    ("contact",   re.compile(r"/(contact|directions|hours)/", re.IGNORECASE)),
    ("about",     re.compile(r"/about/", re.IGNORECASE)),
    ("blog",      re.compile(r"/(blog|news|articles?)/", re.IGNORECASE)),
]

# XML namespace used by the sitemap protocol — ElementTree needs it
# explicitly since sitemap.xml declares a default (unprefixed) namespace.
_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def classify_template(url: str) -> str:
    """First-match-wins template classification (TR-22)."""
    for kind, pattern in _TEMPLATE_RULES:
        if pattern.search(url):
            return kind
    try:
        path = urlparse(url).path
    except Exception:
        path = url
    path_parts = [p for p in path.split("/") if p]
    if not path_parts:
        return "homepage"
    return "other"


def parse_sitemap_xml(xml_text: str) -> dict:
    """Parse one sitemap document — either a <urlset> (page URLs) or a
    <sitemapindex> (pointers to sub-sitemaps, one level of which is
    followed by the caller per FR-25).

    Malformed XML is swallowed, not raised — one bad sitemap must never
    take down discovery for the rest of the site.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {"urls": [], "sub_sitemaps": []}

    tag = root.tag.rsplit("}", 1)[-1].lower()

    if tag == "sitemapindex":
        sub_sitemaps = [
            loc.text.strip()
            for loc in root.findall("sm:sitemap/sm:loc", _SITEMAP_NS)
            if loc.text
        ]
        return {"urls": [], "sub_sitemaps": sub_sitemaps}

    urls = [
        loc.text.strip()
        for loc in root.findall("sm:url/sm:loc", _SITEMAP_NS)
        if loc.text
    ]
    return {"urls": urls, "sub_sitemaps": []}


def group_by_template(urls: list[str]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for url in urls:
        groups.setdefault(classify_template(url), []).append(url)
    return groups


def sample_by_template(
    groups: dict[str, list[str]],
    exclude: set[str],
    per_group: int = 2,
    max_total: int = 8,
) -> list[dict]:
    """Pick up to `per_group` URLs per template, SPREAD across the group
    by even stride rather than taking the first N (FR-27 — sitemaps emit
    in creation order, so the first N of one template often share one
    stale variant).

    Returns [{url, template}], already deduplicated against `exclude`
    (URLs already queued for audit from on-page link discovery), capped
    at `max_total` overall so sitemap sampling can't blow out run time.
    """
    sampled: list[dict] = []
    for kind, urls in groups.items():
        if kind in ("homepage",):
            continue  # already audited separately
        candidates = [u for u in urls if u not in exclude]
        if not candidates:
            continue

        n = min(per_group, len(candidates))
        stride = max(len(candidates) // n, 1)
        picked = candidates[::stride][:n]

        for u in picked:
            sampled.append({"url": u, "template": kind})
            if len(sampled) >= max_total:
                return sampled
    return sampled
