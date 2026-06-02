"""Build the 4-section QA audit card from desktop and mobile viewport results."""

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from ai.agent.state import (
    CheckItem,
    CheckStatus,
    QAAuditCard,
    QASection,
    QASectionResult,
    ViewportResult,
    ViewportType,
)
from ai.agent.qa_checks import SECTION_DEFINITIONS, get_fresh_checks

if TYPE_CHECKING:
    from ai.agent.state import AuditSession

logger = logging.getLogger(__name__)


def _has_issues_of_type(results: list[ViewportResult], keywords: list[str]) -> bool:
    """Check if any result has issues with titles/descriptions matching keywords."""
    for r in results:
        for issue in r.issues:
            title = (issue.title if hasattr(issue, 'title') else issue.get("title", "")).lower()
            desc = (issue.description if hasattr(issue, 'description') else issue.get("description", "")).lower()
            combined = title + " " + desc
            if any(kw in combined for kw in keywords):
                return True
    return False


def _has_broken_links(results: list[ViewportResult]) -> bool:
    """Check if any result has broken links."""
    return any(len(r.broken_links) > 0 for r in results)


def _has_performance_issues(results: list[ViewportResult]) -> bool:
    """Check if any result has performance-related issues."""
    return _has_issues_of_type(results, ["image", "performance", "load time", "page weight", "resource"])


def _has_form_data(results: list[ViewportResult]) -> bool:
    """Check if forms were detected."""
    return any(len(r.forms) > 0 for r in results)


def _page_loads_ok(results: list[ViewportResult]) -> bool:
    """Check that pages loaded without errors."""
    return all(r.error is None for r in results)


def _collect_issue_text(results: list[ViewportResult]) -> str:
    """Collect all issue descriptions from results into a text summary."""
    lines = []
    for r in results:
        for issue in r.issues:
            title = issue.title if hasattr(issue, 'title') else issue.get("title", "")
            desc = issue.description if hasattr(issue, 'description') else issue.get("description", "")
            severity = (issue.severity.value if hasattr(issue, 'severity') and hasattr(issue.severity, 'value')
                        else issue.get("severity", ""))
            if title:
                lines.append(f"[{severity.upper()}] {title}: {desc}")
    return "\n".join(lines[:10])  # Limit to 10 most relevant


def _classify_page(url: str, page_type: str) -> list[QASection]:
    """Map a page to one or more QA sections based on URL and type."""
    url_lower = url.lower()
    sections = []

    if page_type == "homepage" or url_lower.rstrip("/").count("/") <= 3:
        # Root domain or near-root = likely homepage
        if page_type == "homepage":
            sections.append(QASection.HOMEPAGE)

    # Inventory-related pages
    inv_keywords = ["inventory", "vehicle", "car", "truck", "suv", "vdp", "srp",
                     "new-", "used-", "/new/", "/used/", "stock", "listing"]
    if any(kw in url_lower for kw in inv_keywords) or page_type == "products":
        sections.append(QASection.INVENTORY_PAGES)

    # Lead forms and widgets
    form_keywords = ["finance", "credit", "trade", "service", "schedule", "parts",
                      "contact", "form", "apply", "appointment", "chat"]
    if any(kw in url_lower for kw in form_keywords) or page_type == "contact":
        sections.append(QASection.LEAD_FORMS_WIDGETS)

    # General content catches everything else
    if not sections or page_type in ["about", "blog", "faq", "privacy", "careers", "other"]:
        sections.append(QASection.GENERAL_CONTENT)

    return sections


def _evaluate_check(
    check_id: str,
    desktop_results: list[ViewportResult],
    mobile_results: list[ViewportResult],
    all_desktop: list[ViewportResult],
    all_mobile: list[ViewportResult],
    session: Optional["AuditSession"] = None,
) -> tuple[CheckStatus, CheckStatus, str]:
    """Evaluate a single check item, returning (desktop_status, mobile_status, notes)."""

    # Default: pass if we have data, NA if we don't
    d_status = CheckStatus.PASS if desktop_results else CheckStatus.NA
    m_status = CheckStatus.PASS if mobile_results else CheckStatus.NA
    notes = ""

    # ---- HOMEPAGE checks ----
    if check_id == "hp_template_loads":
        if not _page_loads_ok(desktop_results):
            d_status = CheckStatus.FAIL
            notes = "Desktop page load errors detected"
        if not _page_loads_ok(mobile_results):
            m_status = CheckStatus.FAIL
            notes += " Mobile page load errors detected"

    elif check_id == "hp_search_tools":
        d_status = CheckStatus.PASS if desktop_results else CheckStatus.NA
        m_status = CheckStatus.PASS if mobile_results else CheckStatus.NA

    elif check_id == "hp_cta_links":
        # Sprint 5: use dedicated CTA broken link check first; fall back to general broken links
        if session and session.cta_broken_links:
            d_status = CheckStatus.FAIL
            m_status = CheckStatus.FAIL
            notes = f"{len(session.cta_broken_links)} CTA link(s) return 404"
        elif _has_broken_links(desktop_results):
            d_status = CheckStatus.FAIL
            notes = "Broken CTA links found"
        if not session or not session.cta_broken_links:
            if _has_broken_links(mobile_results):
                m_status = CheckStatus.FAIL

    elif check_id == "hp_slides_same_size":
        # Sprint 5: check dimension issues on desktop and mobile
        if session:
            if session.carousel_dimension_issues_desktop:
                d_status = CheckStatus.FAIL
                notes = f"{len(session.carousel_dimension_issues_desktop)} carousel(s) have inconsistent slide sizes (desktop)"
            if session.carousel_dimension_issues_mobile:
                m_status = CheckStatus.FAIL
                notes += f" {len(session.carousel_dimension_issues_mobile)} carousel(s) have inconsistent slide sizes (mobile)"
        else:
            d_status = CheckStatus.PASS if desktop_results else CheckStatus.NA
            m_status = CheckStatus.PASS if mobile_results else CheckStatus.NA

    elif check_id == "hp_slides_linked":
        # Sprint 5: relevance check
        if session:
            irrelevant = [r for r in session.carousel_link_relevance if r.get("relevant") is False]
            if irrelevant:
                d_status = CheckStatus.FAIL
                notes = f"{len(irrelevant)} slide link(s) may point to incorrect pages"
            elif session.carousel_link_relevance:
                d_status = CheckStatus.PASS
        else:
            d_status = CheckStatus.PASS if desktop_results else CheckStatus.NA

    elif check_id == "hp_carousel_no_broken":
        if session:
            if session.carousel_broken_links:
                d_status = CheckStatus.FAIL
                notes = f"{len(session.carousel_broken_links)} carousel slide link(s) return 404"
            else:
                d_status = CheckStatus.PASS if desktop_results else CheckStatus.NA
        else:
            d_status = CheckStatus.PASS if desktop_results else CheckStatus.NA

    elif check_id == "hp_content_images":
        if session:
            if session.homepage_broken_content_images:
                d_status = CheckStatus.FAIL
                notes = f"{len(session.homepage_broken_content_images)} non-carousel homepage image(s) broken"
            else:
                d_status = CheckStatus.PASS if desktop_results else CheckStatus.NA
        else:
            d_status = CheckStatus.PASS if desktop_results else CheckStatus.NA

    elif check_id == "hp_slide_count":
        d_status = CheckStatus.PASS if desktop_results else CheckStatus.NA

    elif check_id == "hp_floating_widgets":
        d_status = CheckStatus.PASS if desktop_results else CheckStatus.NA
        m_status = CheckStatus.PASS if mobile_results else CheckStatus.NA

    elif check_id == "hp_no_expired":
        if session and session.nav_expired_dates:
            hp_expired = [d for d in session.nav_expired_dates if d.get("source_page") == (session.target_url if session else "")]
            if hp_expired:
                d_status = CheckStatus.FAIL
                notes = f"{len(hp_expired)} expired date(s) found on homepage"
        else:
            d_status = CheckStatus.PASS if desktop_results else CheckStatus.NA

    # ---- INVENTORY checks ----
    elif check_id == "inv_vdp_loads":
        if not _page_loads_ok(desktop_results):
            d_status = CheckStatus.FAIL
        if not _page_loads_ok(mobile_results):
            m_status = CheckStatus.FAIL

    elif check_id == "inv_srp_loads":
        if not _page_loads_ok(desktop_results):
            d_status = CheckStatus.FAIL
        if not _page_loads_ok(mobile_results):
            m_status = CheckStatus.FAIL

    elif check_id == "inv_cta_buttons":
        if _has_broken_links(desktop_results):
            d_status = CheckStatus.FAIL
        if _has_broken_links(mobile_results):
            m_status = CheckStatus.FAIL

    elif check_id == "inv_sort_filter":
        d_status = CheckStatus.PASS if desktop_results else CheckStatus.NA
        m_status = CheckStatus.PASS if mobile_results else CheckStatus.NA

    elif check_id in ("inv_history_reports", "inv_model_years"):
        d_status = CheckStatus.PASS if desktop_results else CheckStatus.NA

    # ---- GENERAL CONTENT checks ----
    elif check_id == "gc_header_icons":
        if _has_broken_links(all_desktop):
            d_status = CheckStatus.FAIL
        if _has_broken_links(all_mobile):
            m_status = CheckStatus.FAIL

    elif check_id == "gc_logo_link":
        if session and session.header_logo_check:
            logo = session.header_logo_check
            if logo.get("status") == "pass":
                d_status = CheckStatus.PASS
            else:
                d_status = CheckStatus.FAIL
                notes = logo.get("note", "Logo link issue detected")
        else:
            d_status = CheckStatus.PASS if all_desktop else CheckStatus.NA

    elif check_id == "gc_images_optimized":
        if _has_performance_issues(all_desktop):
            d_status = CheckStatus.FAIL
            notes = "Unoptimized images detected"

    elif check_id == "gc_phone_numbers":
        d_status = CheckStatus.PASS if all_desktop else CheckStatus.NA

    elif check_id == "gc_map_location":
        d_status = CheckStatus.PASS if all_desktop else CheckStatus.NA

    elif check_id == "gc_nav_links":
        if session and session.nav_broken_links:
            d_status = CheckStatus.FAIL
            m_status = CheckStatus.FAIL
            notes = f"{len(session.nav_broken_links)} nav link(s) return 404"
        elif _has_broken_links(all_desktop):
            d_status = CheckStatus.FAIL
        elif _has_broken_links(all_mobile):
            m_status = CheckStatus.FAIL

    elif check_id == "gc_nav_no_dupes":
        if session and session.nav_duplicate_links:
            d_status = CheckStatus.FAIL
            notes = f"{len(session.nav_duplicate_links)} duplicate URL(s) in navigation"
        else:
            d_status = CheckStatus.PASS if all_desktop else CheckStatus.NA

    elif check_id == "gc_social_links_live":
        if session:
            if session.social_media_broken:
                d_status = CheckStatus.FAIL
                platforms = ", ".join({l.get("platform", "") for l in session.social_media_broken})
                notes = f"Broken social links: {platforms}"
            elif session.social_media_no_new_tab or session.social_media_broken is not None:
                d_status = CheckStatus.PASS if all_desktop else CheckStatus.NA
        else:
            d_status = CheckStatus.PASS if all_desktop else CheckStatus.NA

    elif check_id == "gc_social_new_tab":
        if session and session.social_media_no_new_tab:
            d_status = CheckStatus.FAIL
            platforms = ", ".join({l.get("platform", "") for l in session.social_media_no_new_tab})
            notes = f"Social links missing target=_blank: {platforms}"
        elif session is not None:
            d_status = CheckStatus.PASS if all_desktop else CheckStatus.NA
        else:
            d_status = CheckStatus.PASS if all_desktop else CheckStatus.NA

    elif check_id == "gc_links_new_tab":
        d_status = CheckStatus.PASS if all_desktop else CheckStatus.NA

    elif check_id == "gc_expired_content":
        if session and session.nav_expired_dates:
            d_status = CheckStatus.FAIL
            notes = f"{len(session.nav_expired_dates)} expired date(s) found across audited pages"
        else:
            d_status = CheckStatus.PASS if all_desktop else CheckStatus.NA

    # ---- LEAD FORMS & WIDGETS checks ----
    elif check_id == "lf_native_forms":
        has_forms_d = any(len(r.forms) > 0 for r in desktop_results) if desktop_results else False
        has_forms_m = any(len(r.forms) > 0 for r in mobile_results) if mobile_results else False
        d_status = CheckStatus.PASS if has_forms_d else CheckStatus.NA
        m_status = CheckStatus.PASS if has_forms_m else CheckStatus.NA

    elif check_id in ("lf_finance_app", "lf_trade_in", "lf_service_scheduler", "lf_order_parts", "lf_chat_tool"):
        d_status = CheckStatus.PASS if desktop_results else CheckStatus.NA
        m_status = CheckStatus.PASS if mobile_results else CheckStatus.NA

    elif check_id == "lf_no_expired":
        d_status = CheckStatus.PASS if desktop_results else CheckStatus.NA

    return d_status, m_status, notes.strip()


def build_qa_card(
    target_url: str,
    desktop_results: list[ViewportResult],
    mobile_results: list[ViewportResult],
    llm=None,
    session: Optional["AuditSession"] = None,
) -> QAAuditCard:
    """Build the full QA audit card from viewport results.

    Maps discovered pages to the 4 sections, evaluates each check item,
    and populates stream/support issue text.
    """
    card = QAAuditCard(
        website_url=target_url,
        audit_date=datetime.now().strftime("%m/%d/%Y"),
    )

    # Classify pages into sections
    section_desktop: dict[QASection, list[ViewportResult]] = {s: [] for s in QASection}
    section_mobile: dict[QASection, list[ViewportResult]] = {s: [] for s in QASection}

    for r in desktop_results:
        for section in _classify_page(r.url, r.page_type):
            section_desktop[section].append(r)

    for r in mobile_results:
        for section in _classify_page(r.url, r.page_type):
            section_mobile[section].append(r)

    # Ensure homepage section has homepage if we found one
    homepage_desktop = [r for r in desktop_results if r.page_type == "homepage"]
    homepage_mobile = [r for r in mobile_results if r.page_type == "homepage"]
    if homepage_desktop and not section_desktop[QASection.HOMEPAGE]:
        section_desktop[QASection.HOMEPAGE] = homepage_desktop
    if homepage_mobile and not section_mobile[QASection.HOMEPAGE]:
        section_mobile[QASection.HOMEPAGE] = homepage_mobile

    # Build each section
    for section_enum in QASection:
        definition = SECTION_DEFINITIONS[section_enum]
        checks = get_fresh_checks(section_enum)

        sec_desktop = section_desktop[section_enum]
        sec_mobile = section_mobile[section_enum]

        for check in checks:
            d_status, m_status, notes = _evaluate_check(
                check.id, sec_desktop, sec_mobile, desktop_results, mobile_results, session
            )
            check.desktop_status = d_status
            if check.has_mobile_column:
                check.mobile_status = m_status
            check.notes = notes

        # Collect issue text for stream/support boxes
        all_section_results = sec_desktop + sec_mobile
        stream_issues = _collect_issue_text(all_section_results)
        support_issues = ""

        failed_checks = [c for c in checks if c.desktop_status == CheckStatus.FAIL or
                         (c.has_mobile_column and c.mobile_status == CheckStatus.FAIL)]
        if failed_checks:
            support_issues = "Issues found in: " + ", ".join(c.label for c in failed_checks)

        section_result = QASectionResult(
            section=section_enum,
            title=definition["title"],
            checks=checks,
            stream_issues=stream_issues,
            support_issues=support_issues,
        )
        card.sections.append(section_result)

    return card
