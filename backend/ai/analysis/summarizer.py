"""LLM-powered summarization for audit results."""

import logging
from typing import Optional

from ai.agent.prompts import PAGE_SUMMARY_PROMPT, TOP_ACTIONS_PROMPT
from ai.agent.state import AuditSession, PageAuditResult, IssueCategory, IssueSeverity
from ai.llm.base import LLMMessage, LLMProvider
from ai.utils import parse_llm_json

logger = logging.getLogger(__name__)


async def summarize_page(page: PageAuditResult, llm: LLMProvider) -> str:
    """Generate a 2-3 sentence summary for a single page."""
    top_issues = "\n".join(
        f"- [{i.severity.value}] {i.title}"
        for i in sorted(page.issues, key=lambda x: ["critical", "serious", "moderate", "minor"].index(x.severity.value))[:5]
    ) or "No significant issues found."

    prompt = PAGE_SUMMARY_PROMPT.format(
        url=page.url,
        title=page.title,
        page_type=page.page_type,
        a11y_score=page.scores.get("accessibility", 0),
        a11y_issues=sum(1 for i in page.issues if i.category == IssueCategory.ACCESSIBILITY),
        seo_score=page.scores.get("seo", 0),
        seo_issues=sum(1 for i in page.issues if i.category == IssueCategory.SEO),
        perf_score=page.scores.get("performance", 0),
        broken_link_count=len(page.broken_links),
        top_issues=top_issues,
    )

    try:
        response = await llm.chat([LLMMessage.user(prompt)], temperature=0.3, max_tokens=300)
        return response.content.strip()
    except Exception as e:
        logger.error(f"Page summary failed: {e}")
        return ""


_IMPACT_RANK = {"high": 0, "medium": 1, "low": 2}


# Plain-language effect of fixing each finding — used as the fallback
# `estimated_impact`, keyed by the finding's title prefix.
_EFFECTS = {
    "Fix ": "Shoppers stop hitting dead ends, and search engines stop losing crawl budget on them.",
    "Align website hours": "Customers stop showing up when the store is closed, and Google shows the right hours.",
    "Fix the address mismatch": "Maps and search send customers to the right location.",
    "Unblock AI crawlers": "ChatGPT, Claude and other AI assistants can read and recommend the dealership.",
    "Speed up the mobile homepage": "Faster first impression for mobile shoppers, who leave slow pages before they load.",
    "Fix third-party scripts": "Restores whatever those tags do (tracking, chat, leads) and removes wasted requests.",
    "Add async/defer": "The page paints sooner without changing what the vendors do.",
    "Identify or remove unknown": "Less unaccounted code running on the site — lighter pages and fewer privacy surprises.",
    "Keep consent and call-tracking": "Keeps compliance and call attribution intact while trimming their weight.",
    "Fix JavaScript errors": "Broken widgets, forms or tracking start working again.",
    "Replace ": "Pages look complete and trustworthy instead of showing broken image icons.",
    "Compress oversized images": "Pages load faster, especially on mobile data.",
    "Restore vehicle history report": "Used-car shoppers get the Carfax/AutoCheck proof they look for before calling.",
    "Remove inventory filters": "Shoppers stop hitting \"0 vehicles found\" and leaving.",
    "Remove expired offers": "No more advertising deals the store can no longer honor.",
    "Add missing lead-capture": "More ways for shoppers to become leads.",
    "Fix contradicting business schema": "Google and AI assistants get one consistent set of business facts.",
    "Complete vehicle structured data": "Vehicles show up correctly in Google vehicle listings and AI answers.",
    "Make key content visible": "AI assistants and crawlers see the real content, not an empty shell.",
    "Fix critical accessibility": "Usable for shoppers with disabilities and lower ADA legal risk.",
    "Review outdated model years": "Inventory looks current instead of stale.",
    "Add an llms.txt": "AI assistants get a clean, accurate summary of the dealership.",
}


def _finding(impact: str, text: str, title: str, step: str) -> dict:
    effect = next((v for k, v in sorted(_EFFECTS.items(), key=lambda kv: -len(kv[0])) if title.startswith(k)), "")
    return {"impact": impact, "text": text, "title": title, "step": step, "effect": effect}


def _examples(items: list, key: str, n: int = 3) -> str:
    vals = [str(i.get(key)) for i in items[:n] if i.get(key)]
    return f" (e.g. {', '.join(vals)})" if vals else ""


def collect_findings(session: AuditSession) -> list[dict]:
    """Concrete audit findings, ranked most-severe first.

    Built from the session's measured results — the input the top-actions
    prompt ranks and phrases, and the deterministic fallback when no LLM
    is available or its output can't be parsed.
    """
    f: list[dict] = []

    broken_links = (session.broken_links_all + session.inventory_broken_links
                    + session.vdp_broken_links + session.preowned_inventory_broken_links)
    if broken_links:
        f.append(_finding("high",
            f"{len(broken_links)} broken links (404/410){_examples(broken_links, 'url')}",
            f"Fix {len(broken_links)} broken links", "Update or redirect each 404 URL listed in the audit."))

    if session.hours_discrepancies:
        days = ", ".join(d.get("day", "") for d in session.hours_discrepancies[:4])
        f.append(_finding("high",
            f"Website hours disagree with Google Business Profile on {len(session.hours_discrepancies)} day(s): {days}",
            "Align website hours with Google Business Profile", "Confirm correct hours, then update the site and GBP to match."))
    if session.address_comparison and session.address_comparison.get("match") is False:
        f.append(_finding("high", "Website address does not match Google Business Profile",
            "Fix the address mismatch with Google Business Profile", "Correct whichever listing is wrong so both match."))

    robots = session.robots_txt_check or {}
    if robots.get("ai_crawlers_fully_blocked"):
        bots = ", ".join(robots["ai_crawlers_fully_blocked"][:5])
        f.append(_finding("high", f"robots.txt fully blocks AI crawlers: {bots}",
            "Unblock AI crawlers in robots.txt", "Remove the Disallow: / rules for these AI user-agents."))

    lh = session.lighthouse_score or {}
    mobile = lh.get("mobile") or {}
    if mobile.get("median") is not None and mobile.get("band") != "excellent":
        metrics = mobile.get("metrics") or {}
        poor = [k for k, m in metrics.items() if isinstance(m, dict) and m.get("band") == "poor"]
        f.append(_finding("high" if mobile.get("band") == "subpar" else "medium",
            f"Mobile Lighthouse performance {mobile['median']}/100 ({mobile.get('band')})"
            + (f"; poor metrics: {', '.join(poor)}" if poor else ""),
            "Speed up the mobile homepage", "Defer non-critical third-party scripts and compress large images."))

    vr = session.vendor_cost_report or {}
    vendors = vr.get("vendors") or []
    blocked = (vr.get("blocked_count") or 0) + (vr.get("failed_count") or 0)
    if blocked:
        names = ", ".join(v["hostname"] if v.get("unidentified") else v["owner"]
                          for v in vendors if v.get("blocked_count") or v.get("failed_count"))
        f.append(_finding("high", f"{blocked} third-party script(s) never loaded or failed: {names}",
            "Fix third-party scripts that fail to load", "Check each vendor's tag is current; remove dead tags."))
    never_defer_blocking = [v["owner"] for v in vendors
                            if (v.get("policy") or {}).get("defer") is False and v.get("render_blocking_count")]
    deferrable_blocking = [v["hostname"] if v.get("unidentified") else v["owner"] for v in vendors
                           if (v.get("policy") or {}).get("defer") is True and v.get("render_blocking_count")]
    if deferrable_blocking:
        f.append(_finding("medium",
            f"Deferrable vendor scripts block page rendering: {', '.join(deferrable_blocking[:5])}",
            "Add async/defer to render-blocking vendor scripts", "Load these vendor tags with async or defer."))
    unidentified = [v["hostname"] for v in vendors if v.get("unidentified")]
    if unidentified:
        f.append(_finding("medium" if len(unidentified) >= 3 else "low",
            f"{len(unidentified)} unidentified third-party script host(s): {', '.join(unidentified[:5])}",
            "Identify or remove unknown third-party scripts", "Confirm who owns each host; remove any that are unused."))
    if never_defer_blocking:
        f.append(_finding("low",
            f"Render-blocking but must load first (consent/call tracking): {', '.join(never_defer_blocking)}",
            "Keep consent and call-tracking tags first, but lean", "Leave load order; ask vendors for lighter tags."))

    if session.js_errors_all:
        f.append(_finding("high" if len(session.js_errors_all) > 5 else "medium",
            f"{len(session.js_errors_all)} JavaScript errors{_examples(session.js_errors_all, 'message', 2)}",
            "Fix JavaScript errors", "Have the web vendor resolve each console error listed."))

    broken_images = session.broken_images_all + session.inventory_broken_images + session.vdp_broken_images
    if broken_images:
        f.append(_finding("medium", f"{len(broken_images)} broken images{_examples(broken_images, 'source_page', 2)}",
            f"Replace {len(broken_images)} broken images", "Re-upload or remove each broken image."))
    if session.oversized_images_all:
        total_kb = sum(i.get("file_size_kb") or 0 for i in session.oversized_images_all)
        f.append(_finding("medium", f"{len(session.oversized_images_all)} images over 500KB ({round(total_kb / 1024, 1)} MB total)",
            "Compress oversized images", "Convert to WebP/AVIF and resize to display dimensions."))

    hist = session.history_reports_check or {}
    if hist.get("is_preowned") and (not hist.get("found") or hist.get("all_work") is False):
        f.append(_finding("medium", "Pre-owned VDP is missing working Carfax/AutoCheck history reports",
            "Restore vehicle history report links", "Re-enable the Carfax/AutoCheck integration on used VDPs."))
    if session.srp_filter_zero_results:
        f.append(_finding("medium", f"{len(session.srp_filter_zero_results)} inventory filter option(s) return zero vehicles",
            "Remove inventory filters that show zero results", "Hide empty filter options in the SRP settings."))

    expired = session.nav_expired_dates + session.inventory_expired_dates
    if expired:
        f.append(_finding("medium", f"{len(expired)} expired offers/dates still shown{_examples(expired, 'date_str', 3)}",
            "Remove expired offers and dates", "Update or take down the expired specials."))

    lead_tools = {"contact_form": "contact form", "live_chat": "live chat", "trade_in_tool": "trade-in tool",
                  "service_scheduling": "service scheduling", "finance_form": "finance application"}
    detected = {k for k, v in session.dealership_features.items() if (v or {}).get("detected")}
    missing = [label for k, label in lead_tools.items() if k not in detected]
    # Nothing detected at all means detection didn't run, not that every tool is missing.
    if detected and missing:
        f.append(_finding("medium", f"Lead tools not detected: {', '.join(missing)}",
            "Add missing lead-capture tools", "Confirm these tools exist and are reachable from main pages."))

    biz = session.structured_data_business_check or {}
    if biz.get("contradictions"):
        fields = ", ".join(c.get("field", "") for c in biz["contradictions"][:4])
        f.append(_finding("medium", f"Business structured data contradicts itself on: {fields}",
            "Fix contradicting business schema data", "Make every JSON-LD business record use the same values."))
    veh = session.vehicle_structured_data_check or {}
    if veh.get("status") == "measured" and not veh.get("complete"):
        f.append(_finding("medium", f"VDP vehicle schema missing: {', '.join(veh.get('missing_fields') or [])}",
            "Complete vehicle structured data on VDPs", "Add the missing fields to the VDP schema template."))
    vis = session.crawler_visibility_check or {}
    if vis.get("band") in ("poor", "partial"):
        share = vis.get("crawler_visible_share")
        f.append(_finding("high" if vis["band"] == "poor" else "medium",
            f"Only {round(share * 100)}% of homepage content is visible before JavaScript runs" if share is not None
            else "Much of the homepage content needs JavaScript to appear",
            "Make key content visible without JavaScript", "Server-render headings, inventory and contact info."))

    a11y_critical = sum(1 for p in session.pages for i in p.issues
                        if i.category == IssueCategory.ACCESSIBILITY and i.severity in (IssueSeverity.CRITICAL, IssueSeverity.SERIOUS))
    if a11y_critical:
        f.append(_finding("medium", f"{a11y_critical} critical/serious accessibility issues",
            "Fix critical accessibility issues", "Start with missing labels, alt text and contrast."))

    my = session.model_year_check or {}
    if my.get("has_outdated"):
        f.append(_finding("low", f"Listings show outdated model years: {', '.join(map(str, (my.get('outdated') or [])[:5]))}",
            "Review outdated model years in inventory", "Check the inventory feed for stale listings."))
    if session.social_media_broken:
        f.append(_finding("low", f"{len(session.social_media_broken)} broken social media link(s)",
            "Fix broken social media links", "Update the footer social links."))
    llms = session.llms_txt_check or {}
    if llms.get("status") == "absent":
        f.append(_finding("low", "No llms.txt file", "Add an llms.txt file", "Publish a short llms.txt summarizing the dealership."))

    f.sort(key=lambda x: _IMPACT_RANK[x["impact"]])
    return f


def _fallback_actions(findings: list[dict]) -> list[dict]:
    return [{
        "title": x["title"],
        "impact": x["impact"],
        "estimated_impact": f"{x['effect']} Found: {x['text']}." if x["effect"] else x["text"],
        "next_steps": [x["step"]],
    } for x in findings[:5]]


def _fallback_summary(session: AuditSession, findings: list[dict]) -> str:
    high = sum(1 for x in findings if x["impact"] == "high")
    first = f"The site scored {round(session.overall_score)}/100 (grade {session.overall_grade}) across {len(session.pages)} pages."
    if not findings:
        return f"{first} No major issues were found."
    lead = f"{high} high-impact issue(s) found" if high else f"{len(findings)} issue(s) found"
    return f"{first} {lead} — start with: {findings[0]['title'].lower()}."


def _clean_actions(raw) -> list[dict]:
    actions = []
    for a in raw if isinstance(raw, list) else []:
        if not isinstance(a, dict) or not a.get("title"):
            continue
        impact = str(a.get("impact", "medium")).lower()
        steps = a.get("next_steps") or []
        if isinstance(steps, str):
            steps = [steps]
        actions.append({
            "title": str(a["title"]).strip(),
            "impact": impact if impact in _IMPACT_RANK else "medium",
            "estimated_impact": str(a.get("estimated_impact", "")).strip(),
            "next_steps": [str(x).strip() for x in steps if str(x).strip()][:3],
        })
    return actions[:5]


async def generate_top_actions(session: AuditSession, llm: Optional[LLMProvider]) -> tuple[str, list[dict]]:
    """2-sentence summary + 3-5 top actions, each {title, impact,
    estimated_impact, next_steps}.

    The LLM only ranks and phrases `collect_findings()` output; when it is
    unavailable or returns unusable JSON, the ranked findings are used
    directly so the report never comes back empty.
    """
    findings = collect_findings(session)
    fallback = (_fallback_summary(session, findings), _fallback_actions(findings))
    if not llm or not findings:
        return fallback

    prompt = TOP_ACTIONS_PROMPT.format(
        domain=session.target_url,
        page_count=len(session.pages),
        grade=session.overall_grade,
        overall_score=round(session.overall_score),
        findings="\n".join(f"- [{x['impact']}] {x['text']}" for x in findings[:15]),
    )
    try:
        response = await llm.chat([LLMMessage.user(prompt)], temperature=0.2, max_tokens=900)
        parsed = parse_llm_json(response.content, {})
        summary = str(parsed.get("summary", "")).strip() if isinstance(parsed, dict) else ""
        actions = _clean_actions(parsed.get("actions") if isinstance(parsed, dict) else None)
        if len(actions) < min(3, len(findings)):
            return fallback
        actions.sort(key=lambda a: _IMPACT_RANK[a["impact"]])
        return summary or fallback[0], actions
    except Exception as e:
        logger.error(f"Top actions failed: {e}")
        return fallback
