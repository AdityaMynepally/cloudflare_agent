"""Named third-party vendor identification and cost attribution.

Maps script hostnames to an owner/category, aggregates per-vendor network
cost from Resource Timing data already captured per page, and reports an
explicit policy (never-defer / deferrable / unknown) per category — an
unrecognized host must be reported as unidentified, never silently
grouped with a known-safe one.

Vendor ranking here is a *comparative* signal (bytes/ms as actually
observed in this one browser session), not an absolute claim about a
third party's infrastructure elsewhere.
"""

import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Registrable domain (eTLD+1) -> {owner, category}.
VENDORS: dict[str, dict] = {
    # --- Consent / privacy management — must never be deferred: it has
    # to run before the tags it gates, or tracking can fire pre-consent. ---
    "cookiebot.com":         {"owner": "Cybot (Cookiebot)", "category": "consent"},
    "onetrust.com":          {"owner": "OneTrust", "category": "consent"},
    "trustarc.com":          {"owner": "TrustArc", "category": "consent"},
    "cookielaw.org":         {"owner": "OneTrust", "category": "consent"},

    # --- Tag managers / analytics ---
    "googletagmanager.com":  {"owner": "Google Tag Manager", "category": "tagmanager"},
    "google-analytics.com":  {"owner": "Google Analytics", "category": "analytics"},
    "googlesyndication.com": {"owner": "Google Ads", "category": "analytics"},
    "doubleclick.net":       {"owner": "Google Ads / DoubleClick", "category": "analytics"},
    "facebook.net":          {"owner": "Meta Pixel", "category": "analytics"},
    "clarity.ms":            {"owner": "Microsoft Clarity", "category": "analytics"},
    "hotjar.com":            {"owner": "Hotjar", "category": "analytics"},
    "hotjar.io":             {"owner": "Hotjar", "category": "analytics"},
    "luckyorange.com":       {"owner": "Lucky Orange", "category": "analytics"},
    "segment.com":           {"owner": "Segment", "category": "analytics"},
    "segment.io":            {"owner": "Segment", "category": "analytics"},
    "adobedtm.com":          {"owner": "Adobe Launch/DTM", "category": "tagmanager"},
    "demdex.net":            {"owner": "Adobe Audience Manager", "category": "analytics"},
    "linkedin.com":          {"owner": "LinkedIn Insight Tag", "category": "analytics"},
    "snapchat.com":          {"owner": "Snap Pixel", "category": "analytics"},
    "tiktok.com":            {"owner": "TikTok Pixel", "category": "analytics"},
    "ads-twitter.com":       {"owner": "X (Twitter) Ads", "category": "analytics"},
    "fullstory.com":         {"owner": "FullStory", "category": "analytics"},
    "mouseflow.com":         {"owner": "Mouseflow", "category": "analytics"},

    # --- Dynamic number insertion / call tracking — must never be
    # deferred: breaks call-tracking attribution, which is data the
    # dealer pays for. ---
    "callrail.com":          {"owner": "CallRail", "category": "dni"},
    "invoca.net":            {"owner": "Invoca", "category": "dni"},
    "marchex.com":           {"owner": "Marchex", "category": "dni"},
    "mongoosemetrics.com":   {"owner": "Mongoose Metrics", "category": "dni"},
    "whatconverts.com":      {"owner": "WhatConverts", "category": "dni"},
    "ifbyphone.com":         {"owner": "Ifbyphone", "category": "dni"},

    # --- Live chat widgets ---
    "gubagoo.io":            {"owner": "Gubagoo", "category": "chat"},
    "gubagoo.com":           {"owner": "Gubagoo", "category": "chat"},
    "liveperson.net":        {"owner": "LivePerson", "category": "chat"},
    "carnow.com":            {"owner": "CarNow", "category": "chat"},
    "activengage.com":       {"owner": "ActivEngage", "category": "chat"},
    "podium.com":            {"owner": "Podium", "category": "chat"},
    "intercom.io":           {"owner": "Intercom", "category": "chat"},
    "drift.com":             {"owner": "Drift", "category": "chat"},
    "zendesk.com":           {"owner": "Zendesk", "category": "chat"},
    "zdassets.com":          {"owner": "Zendesk", "category": "chat"},
    "tidio.co":              {"owner": "Tidio", "category": "chat"},

    # --- Trade-in valuation widgets ---
    "kbb.com":               {"owner": "Kelley Blue Book", "category": "widget"},
    "edmunds.com":           {"owner": "Edmunds", "category": "widget"},
    "tradepending.com":      {"owner": "TradePending", "category": "widget"},
    "accu-trade.com":        {"owner": "AccuTrade", "category": "widget"},
    "vauto.com":             {"owner": "vAuto", "category": "widget"},

    # --- Service scheduling ---
    "xtime.com":             {"owner": "Xtime", "category": "widget"},
    "dealer-fx.com":         {"owner": "Dealer-FX", "category": "widget"},
    "cdk.com":               {"owner": "CDK Global", "category": "widget"},
    "cdkglobal.com":         {"owner": "CDK Global", "category": "widget"},
    "autovitals.com":        {"owner": "AutoVitals", "category": "widget"},
    "snap27.com":            {"owner": "Snap27", "category": "widget"},

    # --- Accessibility overlays — deferrable, but the product itself is
    # questionable: loading late is worse for the visitors who need it. ---
    "accessibe.com":         {"owner": "AccessiBe", "category": "a11y"},
    "userway.org":           {"owner": "UserWay", "category": "a11y"},
    "equalweb.com":          {"owner": "EqualWeb", "category": "a11y"},
    "audioeye.com":          {"owner": "AudioEye", "category": "a11y"},

    # --- CDN / infra — not a vendor-removal candidate ---
    "cloudflare.com":        {"owner": "Cloudflare", "category": "cdn"},
    "jsdelivr.net":          {"owner": "jsDelivr CDN", "category": "cdn"},
    "jquery.com":            {"owner": "jQuery CDN", "category": "cdn"},
    "cdnjs.cloudflare.com":  {"owner": "cdnjs", "category": "cdn"},
}

CATEGORY_POLICY: dict[str, dict] = {
    "consent":    {"defer": False, "reason": "Must run before the tags it gates — deferring risks firing tracking pre-consent."},
    "dni":        {"defer": False, "reason": "Breaks call-tracking attribution — the dealer pays for that data."},
    "tagmanager": {"defer": "consolidate", "reason": "Only merging duplicated containers removes duplicated payload; reordering alone does not."},
    "a11y":       {"defer": True, "reason": "Deferrable — but a late-loading accessibility widget is worse for the visitors who need it most."},
    "chat":       {"defer": True, "reason": "Safe to defer until after first paint."},
    "widget":     {"defer": True, "reason": "Safe to defer until after first paint."},
    "analytics":  {"defer": True, "reason": "Safe to defer — does not gate functionality."},
    "cdn":        {"defer": True, "reason": "Static asset delivery — not a vendor-removal candidate."},
    "unknown":    {"defer": "unknown", "reason": "Unrecognized host — never assume safe to defer."},
}


def _registrable_domain(hostname: str) -> str:
    """Best-effort eTLD+1 — good enough for this table's own keys, which
    are all two-label registrable domains, not full hostnames."""
    parts = hostname.lower().split(".")
    if len(parts) <= 2:
        return hostname.lower()
    return ".".join(parts[-2:])


def identify_vendor(url: str, first_party_host: str = "") -> dict:
    """Identify the vendor owning `url`'s host.

    Returns {owner, category, hostname, is_first_party}. An unrecognized
    host is reported as `category: "unknown"`, never silently grouped
    with a known-safe one (FR-12).
    """
    try:
        hostname = urlparse(url).hostname or ""
    except Exception:
        hostname = ""

    registrable = _registrable_domain(hostname) if hostname else ""
    first_party_registrable = _registrable_domain(first_party_host) if first_party_host else ""
    is_first_party = bool(registrable) and registrable == first_party_registrable

    if is_first_party:
        return {"owner": "First-party", "category": "first-party", "hostname": hostname, "is_first_party": True}

    vendor = VENDORS.get(registrable)
    if vendor:
        return {**vendor, "hostname": hostname, "is_first_party": False}

    return {"owner": "Unidentified", "category": "unknown", "hostname": hostname or "unknown", "is_first_party": False}


def policy_for(category: str) -> dict:
    return CATEGORY_POLICY.get(category, CATEGORY_POLICY["unknown"])


def _script_name(url: str) -> str:
    """Last path segment, without query — the human-readable script name."""
    try:
        path = urlparse(url).path or ""
    except Exception:
        path = ""
    name = path.rstrip("/").rsplit("/", 1)[-1]
    return name or (urlparse(url).hostname or url)[:80]


def aggregate_vendor_costs(script_resources: list[dict], first_party_host: str) -> dict:
    """Roll up per-script Resource Timing data to named vendors.

    `script_resources`: [{url, transferSize, duration, renderBlocking,
    httpStatus, status}] from the extension's performance capture —
    `status` is loaded | failed | not_loaded | gated (older captures omit
    it; treated as loaded).

    Known vendors are grouped by owner; unidentified hosts are grouped
    per hostname so several unknown domains are never merged into one
    "Unidentified" row. Each vendor carries its full `scripts` list for
    the UI drill-down.

    Returns {vendors: [...], first_party_bytes, first_party_count,
    render_blocking_count, unidentified_count, blocked_count,
    failed_count, gated_count}.
    """
    report = {
        "vendors": [], "first_party_bytes": 0, "first_party_count": 0,
        "render_blocking_count": 0, "unidentified_count": 0,
        "blocked_count": 0, "failed_count": 0, "gated_count": 0,
    }
    if not script_resources:
        return report

    by_vendor: dict[str, dict] = {}

    for res in script_resources:
        url = res.get("url") or ""
        size = res.get("transferSize") or 0
        duration = res.get("duration") or 0
        blocking = bool(res.get("renderBlocking"))
        status = res.get("status") or "loaded"

        info = identify_vendor(url, first_party_host)
        if blocking:
            report["render_blocking_count"] += 1

        if info["is_first_party"]:
            report["first_party_bytes"] += size
            report["first_party_count"] += 1
            continue

        if status == "not_loaded":
            report["blocked_count"] += 1
        elif status == "failed":
            report["failed_count"] += 1
        elif status == "gated":
            report["gated_count"] += 1

        unidentified = info["category"] == "unknown"
        if unidentified:
            report["unidentified_count"] += 1

        key = f"host:{info['hostname']}" if unidentified else f"owner:{info['owner']}"
        entry = by_vendor.setdefault(key, {
            "owner": info["owner"],
            "category": info["category"],
            "hostname": info["hostname"],
            "hostnames": [],
            "unidentified": unidentified,
            "script_count": 0,
            "total_bytes": 0,
            "total_duration_ms": 0,
            "render_blocking_count": 0,
            "blocked_count": 0,
            "failed_count": 0,
            "gated_count": 0,
            "scripts": [],
        })
        if info["hostname"] not in entry["hostnames"]:
            entry["hostnames"].append(info["hostname"])
        entry["script_count"] += 1
        entry["total_bytes"] += size
        entry["total_duration_ms"] += duration
        if blocking:
            entry["render_blocking_count"] += 1
        if status == "not_loaded":
            entry["blocked_count"] += 1
        elif status == "failed":
            entry["failed_count"] += 1
        elif status == "gated":
            entry["gated_count"] += 1
        entry["scripts"].append({
            "url": url,
            "name": _script_name(url),
            "hostname": info["hostname"],
            "transfer_bytes": size,
            "duration_ms": duration,
            "render_blocking": blocking,
            "status": status,
            "http_status": res.get("httpStatus") or 0,
        })

    for entry in by_vendor.values():
        entry["policy"] = policy_for(entry["category"])
        entry["scripts"].sort(key=lambda sc: sc["duration_ms"], reverse=True)

    report["vendors"] = sorted(by_vendor.values(), key=lambda v: v["total_duration_ms"], reverse=True)
    return report
