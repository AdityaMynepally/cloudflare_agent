"""Deterministic audit pipeline orchestrator.

Drives the audit through a fixed pipeline:
1. DISCOVERING:  Send discover_links to extension -> get page list
2. INVENTORY:    (Dealership sites) Navigate to inventory page, click top vehicle,
                 screenshot the Vehicle Detail Page (VDP).
3. AUDITING:     For each page, send audit_page (desktop + mobile) -> run analysis modules.
4. SUMMARIZING:  LLM generates summaries and recommendations.
5. COMPLETED:    Calculate scores, emit final results with QA card.

The LLM is used ONLY for summarization, not for navigation decisions.
Self-healing: the bridge retries failed commands up to 3 times with exponential backoff.
"""

import asyncio
import base64
import logging
import random
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional
from urllib.parse import urlparse

from ai.agent.state import (
    AuditIssue,
    AuditSession,
    AuditStatus,
    PageAuditResult,
    ViewportResult,
    ViewportType,
)
from ai.agent.ws_bridge import ExtensionBridge, HealingError
from ai.agent.qa_card_builder import build_qa_card
from ai.analysis.accessibility import analyze_accessibility
from ai.analysis.seo import analyze_seo
from ai.analysis.performance import analyze_performance
from ai.analysis.links import check_links
from ai.analysis.images import check_images
from ai.analysis.gbp import fetch_gbp_data, compare_address, compare_hours
from ai.analysis.inventory import (
    categorize_inventory_url,
    check_inventory_graphic_links,
    VDP_CONDITION_RE,
    extract_history_reports,
    verify_history_report_links,
    build_history_reports_check,
    aggregate_history_reports_check,
    extract_model_years,
)
from ai.analysis.homepage import (
    check_carousel_links,
    assess_carousel_relevance,
    check_cta_links,
    check_homepage_content_images,
    analyze_slide_dimensions,
    check_nav_links,
    check_header_logo,
    check_social_media_links,
)
from ai.analysis.summarizer import summarize_page, summarize_site, generate_recommendations
from scoring.calculator import calculate_page_scores, calculate_site_score, score_to_grade

logger = logging.getLogger(__name__)

# Type alias for the SSE progress callback
ProgressCallback = Callable[[str, str, dict], Coroutine[Any, Any, None]]

# Mobile viewport configuration
MOBILE_VIEWPORT = {
    "width": 375,
    "height": 812,
    "deviceScaleFactor": 3,
    "mobile": True,
}

# URL path segments that reliably indicate a dealership inventory/SRP page.
# Intentionally specific — avoid generic segments like /new or /used that
# can match unrelated pages (service appointments, blog posts, etc.)
INVENTORY_URL_PATTERNS = [
    "/inventory", "/new-vehicles", "/used-vehicles",
    "/new-cars", "/used-cars", "/new-car", "/used-car",
    "/srp", "/vehicle-search", "/search-results",
    "/certified-pre-owned", "/cpo",
]

# Number of distinct pre-owned VDPs to check for history-report links.
HISTORY_REPORT_VEHICLE_TARGET = 4
# Stop scanning candidate links after this many attempts, even if the target
# hasn't been reached (avoids a pathological loop on sparsely-linked SRPs).
HISTORY_REPORT_CANDIDATE_ATTEMPTS = 8

# URL path segments that indicate a VDP (vehicle detail page). These alone are
# NOT sufficient — "/new/", "/cars/" etc. also match body-style/category pages
# like /new-vehicles/cars/ (all cars) or /new-vehicles/corolla/ (model landing
# page), which are SRPs, not a specific vehicle. Always pair with
# _is_likely_vdp_path()'s slug check below.
VDP_PATH_HINTS = [
    "/inventory/", "/vehicle/", "/vdp/", "/listing/",
    "/new/", "/used/", "/certified/", "/cars/", "/trucks/", "/suvs/",
    "/detail/", "/details/",
]
# Segments that mean it is NOT a VDP (navigation, filters, etc.)
NON_VDP_PATH_SEGMENTS = [
    "/search", "/filter", "/sort", "/compare", "/wishlist",
    "/contact", "/service", "/about", "/finance", "/blog",
    "/specials", "/directions", "/hours", "/careers",
]


# Some dealer CMS templates (seen on DeLand Kia, Daytona Toyota) encode the VDP
# as separate path SEGMENTS rather than one hyphen-joined slug, e.g.
# "/vehicle/New/2026/Toyota/RAV4-Plug-in-Hybrid/JTM7ERAV2TJ014477/" — the last
# segment is a bare 17-char VIN with zero hyphens, so the hyphen-count slug
# check below never matches it.
_VIN_RE = re.compile(r"^[a-z0-9]{17}$")


def _is_likely_vdp_path(path: str) -> bool:
    """True if `path` looks like an individual vehicle listing, not a category
    /body-style/model landing page.

    A real VDP slug is either a compound of make/model/trim/VIN joined by
    hyphens (e.g. "new-2026-toyota-camry-xle-vin" or
    "new-bloomington-2025-honda-prologue-elite-vin") — at least 3 words — or,
    for CMS templates using one segment per token, a path containing a
    17-character VIN segment. A category page like "/new-vehicles/cars/" or
    "/new-vehicles/corolla/" has neither and would otherwise false-match
    VDP_PATH_HINTS (e.g. "/cars/").
    """
    segments = [s for s in path.split("/") if s]
    last_segment = segments[-1] if segments else ""
    slug_like = last_segment.count("-") >= 2
    has_hint = any(hint in path for hint in VDP_PATH_HINTS)
    has_condition = bool(VDP_CONDITION_RE.search(path))
    has_vin_segment = any(_VIN_RE.match(s) for s in segments)
    return (slug_like and (has_hint or has_condition)) or (has_hint and has_vin_segment)


import os
from pathlib import Path
from dotenv import load_dotenv

# Load from backend/.env (the canonical env for this server process)
_ENV_FILE = Path(__file__).parent.parent.parent / ".env"   # backend/ai/agent -> backend/
load_dotenv(_ENV_FILE, override=False)
GBP_API_KEY = os.getenv("SERPAPI_KEY", "")


class AuditOrchestrator:
    """Drives the deterministic audit pipeline."""

    def __init__(self, llm, max_pages: int = 8):
        self.llm = llm
        self.max_pages = max_pages

    async def run_audit(
        self,
        bridge: ExtensionBridge,
        target_url: str,
        session: AuditSession,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> AuditSession:
        """Run the full audit pipeline.

        Args:
            bridge: ExtensionBridge connected to the Chrome extension.
            target_url: The URL to audit.
            session: AuditSession to populate.
            progress_callback: async fn(event_type, message, data) for SSE updates.
        """
        async def emit(event_type: str, message: str, data: Optional[dict] = None):
            if progress_callback:
                await progress_callback(event_type, message, data or {})

        try:
            session.start()

            # ---- Phase 1: DISCOVERING ----
            session.status = AuditStatus.DISCOVERING
            await emit("progress", f"Discovering pages on {target_url}...", {
                "status": "discovering",
            })

            discover_result = await bridge.send_and_wait({
                "type": "discover_links",
                "url": target_url,
            })

            if discover_result.get("error"):
                session.fail(f"Discovery failed: {discover_result['error']}")
                await emit("error", session.error)
                return session

            # Build page list: homepage + categorized links
            urls_to_audit = [target_url]
            categorized = discover_result.get("categorized_links", {})
            for page_type, url in categorized.items():
                if url not in urls_to_audit:
                    urls_to_audit.append(url)

            urls_to_audit = urls_to_audit[: self.max_pages]
            session.discovered_urls = urls_to_audit
            total = len(urls_to_audit)

            await emit("progress", f"Found {total} pages to audit (desktop + mobile)", {
                "status": "discovering",
                "totalPages": total,
            })

            # ---- Phase 1.5: INVENTORY + VDP (Dealership sites) ----
            internal_links = discover_result.get("internal_links", [])
            inventory_url = self._find_inventory_url(categorized, internal_links, target_url)
            preowned_url   = self._find_preowned_url(internal_links, target_url)
            if inventory_url:
                await self._run_dealership_phase(
                    bridge, inventory_url, session, emit,
                    preowned_url=preowned_url,
                )

            # ---- Phase 2: AUDITING (Desktop + Mobile per page) ----
            session.status = AuditStatus.AUDITING

            # Process homepage from discovery result (desktop)
            homepage_desktop = await self._process_capture_for_viewport(
                target_url, discover_result, session, bridge, ViewportType.DESKTOP
            )
            session.desktop_results.append(homepage_desktop)

            # Also build a legacy PageAuditResult for backward compat
            homepage_page = self._viewport_to_page_result(homepage_desktop)
            session.add_page(homepage_page)

            await emit("page_result", f"Audited: {homepage_desktop.title or target_url} (desktop)", {
                "currentPage": 1,
                "totalPages": total,
                "viewport": "desktop",
                **homepage_page.to_dict(),
            })

            # Homepage mobile pass
            await emit("progress", f"Auditing page 1/{total} (mobile)...", {
                "status": "auditing",
                "currentPage": 1,
                "totalPages": total,
                "viewport": "mobile",
                "pageUrl": target_url,
            })

            try:
                mobile_capture = await bridge.send_and_wait({
                    "type": "audit_page",
                    "url": target_url,
                    "viewport": MOBILE_VIEWPORT,
                })

                if mobile_capture.get("error"):
                    homepage_mobile = ViewportResult(
                        url=target_url, viewport=ViewportType.MOBILE,
                        error=mobile_capture["error"]
                    )
                else:
                    homepage_mobile = await self._process_capture_for_viewport(
                        target_url, mobile_capture, session, bridge, ViewportType.MOBILE
                    )
            except (asyncio.TimeoutError, Exception) as e:
                homepage_mobile = ViewportResult(
                    url=target_url, viewport=ViewportType.MOBILE, error=str(e)
                )

            session.mobile_results.append(homepage_mobile)
            self._aggregate_sprint_metrics(session, homepage_desktop, homepage_mobile, target_url)

            await emit("progress", f"Audited: {homepage_mobile.title or target_url} (mobile)", {
                "currentPage": 1,
                "totalPages": total,
                "viewport": "mobile",
            })

            # ---- Phase 2.1: HOMEPAGE-SPECIFIC CHECKS (Sprint 5) ----
            _mobile_cap_for_hp = mobile_capture if not mobile_capture.get("error") else {}
            await self._run_homepage_checks(session, discover_result, _mobile_cap_for_hp, emit)

            # Remaining pages: desktop + mobile for each
            for idx, page_url in enumerate(urls_to_audit[1:], start=2):
                # Desktop pass
                await emit("progress", f"Auditing page {idx}/{total} (desktop)...", {
                    "status": "auditing",
                    "currentPage": idx,
                    "totalPages": total,
                    "viewport": "desktop",
                    "pageUrl": page_url,
                })

                try:
                    desktop_capture = await bridge.send_and_wait({
                        "type": "audit_page",
                        "url": page_url,
                    })

                    if desktop_capture.get("error"):
                        desktop_result = ViewportResult(
                            url=page_url, viewport=ViewportType.DESKTOP,
                            error=desktop_capture["error"]
                        )
                    else:
                        desktop_result = await self._process_capture_for_viewport(
                            page_url, desktop_capture, session, bridge, ViewportType.DESKTOP
                        )
                except asyncio.TimeoutError:
                    desktop_result = ViewportResult(
                        url=page_url, viewport=ViewportType.DESKTOP, error="Timeout"
                    )
                except Exception as e:
                    desktop_result = ViewportResult(
                        url=page_url, viewport=ViewportType.DESKTOP, error=str(e)
                    )

                session.desktop_results.append(desktop_result)

                # Legacy page result from desktop
                page_result = self._viewport_to_page_result(desktop_result)
                session.add_page(page_result)

                await emit("page_result", f"Audited: {desktop_result.title or page_url} (desktop)", {
                    "currentPage": idx,
                    "totalPages": total,
                    "viewport": "desktop",
                    **page_result.to_dict(),
                })

                # Mobile pass
                await emit("progress", f"Auditing page {idx}/{total} (mobile)...", {
                    "status": "auditing",
                    "currentPage": idx,
                    "totalPages": total,
                    "viewport": "mobile",
                    "pageUrl": page_url,
                })

                try:
                    mobile_capture = await bridge.send_and_wait({
                        "type": "audit_page",
                        "url": page_url,
                        "viewport": MOBILE_VIEWPORT,
                    })

                    if mobile_capture.get("error"):
                        mobile_result = ViewportResult(
                            url=page_url, viewport=ViewportType.MOBILE,
                            error=mobile_capture["error"]
                        )
                    else:
                        mobile_result = await self._process_capture_for_viewport(
                            page_url, mobile_capture, session, bridge, ViewportType.MOBILE
                        )
                except asyncio.TimeoutError:
                    mobile_result = ViewportResult(
                        url=page_url, viewport=ViewportType.MOBILE, error="Timeout"
                    )
                except Exception as e:
                    mobile_result = ViewportResult(
                        url=page_url, viewport=ViewportType.MOBILE, error=str(e)
                    )

                session.mobile_results.append(mobile_result)
                self._aggregate_sprint_metrics(session, desktop_result, mobile_result, page_url)

                await emit("progress", f"Audited: {mobile_result.title or page_url} (mobile)", {
                    "currentPage": idx,
                    "totalPages": total,
                    "viewport": "mobile",
                })

            # ---- Phase 2.5: CONTACT FORM FILL ----
            contact_url = categorized.get("contact")
            if contact_url:
                await self._run_contact_form_phase(bridge, contact_url, session, emit)

            # ---- Phase 2.55: TRADE VALUE MODAL SCREENSHOT ----
            await self._run_trade_value_phase(bridge, session, emit)

            # ---- Phase 2.56: SERVICE / PARTS MODAL SCREENSHOTS ----
            await self._run_feature_click_phase(bridge, session, emit, "service_scheduling", "Schedule Service")
            await self._run_feature_click_phase(bridge, session, emit, "parts_form", "Order Parts")

            # ---- Phase 2.6: GOOGLE BUSINESS PROFILE VERIFICATION ----
            await self._run_gbp_phase(session, emit)

            # ---- Phase 3: SUMMARIZING ----
            session.status = AuditStatus.SUMMARIZING
            await emit("progress", "Building QA audit card...", {
                "status": "summarizing",
            })

            # Calculate scores (from legacy page results)
            for page in session.pages:
                if not page.error:
                    page.scores = calculate_page_scores(page)

            session.overall_score = calculate_site_score(session.pages)
            session.overall_grade = score_to_grade(session.overall_score)

            # Build QA card
            session.qa_card = build_qa_card(
                target_url=target_url,
                desktop_results=session.desktop_results,
                mobile_results=session.mobile_results,
                llm=self.llm,
                session=session,
            )

            # LLM summaries
            if self.llm:
                await emit("progress", "Generating AI summaries...", {
                    "status": "summarizing",
                })
                for page in session.pages:
                    if not page.error and not page.llm_summary:
                        page.llm_summary = await summarize_page(page, self.llm)

                session.site_summary = await summarize_site(session, self.llm)
                session.recommendations = await generate_recommendations(session, self.llm)

            # ---- Phase 4: COMPLETED ----
            session.complete()
            logger.info("Audit pipeline complete, emitting results...")

            # Send a lightweight complete event (NOT the entire session)
            # The frontend will fetch full results via REST
            qa_card_dict = session.qa_card.to_dict() if session.qa_card else None
            complete_payload = {
                "message": "Audit complete!",
                "session_id": session.session_id,
                "target_url": session.target_url,
                "status": "completed",
                "overall_score": session.overall_score,
                "overall_grade": session.overall_grade,
                "site_summary": session.site_summary,
                "recommendations": session.recommendations[:5],
                "pages_audited": session.pages_audited,
                "total_issues": session.total_issues,
                "qa_card": qa_card_dict,
                "started_at": session.started_at,
                "completed_at": session.completed_at,
                "has_vdp": session.vdp_screenshot is not None,
                "vdp_url": (session.vdp_screenshot or {}).get("url"),
                "phone_numbers_count": len(session.phone_numbers_all),
                "broken_links_count": len(session.broken_links_all),
                "broken_images_count": len(session.broken_images_all),
                "oversized_images_count": len(session.oversized_images_all),
                "js_errors_count": len(session.js_errors_all),
                "dealership_features": session.dealership_features,
                # Sprint 5 counts
                # Sprint 6 counts
                "inventory_page_type": session.inventory_page_type,
                "vdp_page_type": session.vdp_page_type,
                "inventory_broken_links_count": len(session.inventory_broken_links),
                "vdp_broken_links_count": len(session.vdp_broken_links),
                "inventory_broken_images_count": len(session.inventory_broken_images),
                "vdp_broken_images_count": len(session.vdp_broken_images),
                "srp_filter_zero_results_count": len(session.srp_filter_zero_results),
                "srp_filters_checked": session.srp_filters_checked,
                "history_reports_check": session.history_reports_check,
                "model_year_check": session.model_year_check,
                "inventory_expired_dates_count": len(session.inventory_expired_dates),
                # Sprint 5 counts
                "carousel_broken_links_count": len(session.carousel_broken_links),
                "cta_broken_links_count": len(session.cta_broken_links),
                "nav_broken_links_count": len(session.nav_broken_links),
                "nav_duplicate_links_count": len(session.nav_duplicate_links),
                "social_media_broken_count": len(session.social_media_broken),
                "social_media_no_new_tab_count": len(session.social_media_no_new_tab),
                "header_logo_check": session.header_logo_check,
                "carousel_dimension_issues_desktop": len(session.carousel_dimension_issues_desktop),
                "carousel_dimension_issues_mobile": len(session.carousel_dimension_issues_mobile),
                "nav_expired_dates_count": len(session.nav_expired_dates),
                "homepage_broken_content_images_count": len(session.homepage_broken_content_images),
                "avg_load_time_ms": (
                    round(sum(t["load_time_ms"] for t in session.page_load_times if t.get("viewport") == "desktop") /
                          max(len([t for t in session.page_load_times if t.get("viewport") == "desktop"]), 1), 0)
                    if session.page_load_times else 0
                ),
            }

            try:
                await emit("complete", "Audit complete!", complete_payload)
                logger.info("Complete event emitted successfully")
            except Exception as emit_err:
                logger.error(f"Failed to emit complete event: {emit_err}", exc_info=True)

            return session

        except Exception as e:
            logger.error(f"Audit failed: {e}", exc_info=True)
            session.fail(str(e))
            try:
                await emit("error", f"Audit failed: {e}")
            except Exception:
                logger.error("Failed to emit error event", exc_info=True)
            return session

    # ------------------------------------------------------------------ #
    #  Dealership helpers                                                  #
    # ------------------------------------------------------------------ #

    def _find_inventory_url(
        self, categorized: dict, internal_links: list, target_url: str
    ) -> Optional[str]:
        """Return the best inventory/SRP URL from discovered links.

        Collects every plausible inventory candidate (explicit 'inventory'
        category, URL pattern match, text keyword match) and PREFERS a
        NEW-inventory candidate over used/CPO — a dealership audit should
        default to auditing new stock. Without this preference, whichever
        inventory-like link happens to appear first in the page's link order
        wins even when it's the Certified/Used section — e.g. because the
        "New" nav item is a dropdown toggle with a bare '#' href, so its
        submenu links (the actual new-inventory URL) get scanned later than
        a direct "Certified Pre-Owned" link elsewhere on the page.
        """
        candidates: list[str] = []

        # 1. Explicit 'inventory' category from page-patterns.js
        if categorized.get("inventory"):
            candidates.append(categorized["inventory"])

        # Keywords that appear in link text/aria-label for inventory nav items
        INVENTORY_TEXT_KEYWORDS = [
            "new inventory", "used inventory", "all inventory",
            "new vehicles", "used vehicles", "new cars", "used cars",
            "shop new", "shop used", "search inventory", "search vehicles",
            "browse inventory", "view inventory", "our inventory",
            "new models", "vehicle inventory",
        ]

        # 2. URL path match against known inventory patterns
        url_matched = None
        text_matched = None

        for link in internal_links:
            href = link.get("href", "")
            text = (link.get("text", "") or "").strip().lower()
            pathname = (link.get("pathname", "") or "").lower()

            if not href:
                continue

            # Skip pages that are clearly NOT inventory (service dept, contact, etc.)
            NON_INVENTORY = [
                "/service", "/body-shop", "/parts", "/contact", "/about",
                "/finance", "/financing", "/careers", "/blog", "/news",
                "/specials", "/reviews", "/directions", "/hours",
            ]
            if any(x in pathname for x in NON_INVENTORY):
                continue

            # URL pattern match (higher priority)
            if url_matched is None and any(p in pathname for p in INVENTORY_URL_PATTERNS):
                url_matched = href

            # Link text match (lower priority)
            if text_matched is None and any(kw in text for kw in INVENTORY_TEXT_KEYWORDS):
                text_matched = href

        if url_matched:
            candidates.append(url_matched)
        if text_matched:
            candidates.append(text_matched)

        if not candidates:
            return None

        for c in candidates:
            if categorize_inventory_url(c) == "new":
                return c
        return candidates[0]

    def _find_preowned_url(self, internal_links: list, target_url: str) -> Optional[str]:
        """Find a used / CPO inventory URL from the site's navigation links.

        Used specifically to discover a pre-owned VDP for history-report and
        model-year checks, even when the primary inventory URL points to new cars.
        """
        PREOWNED_PATH_PATTERNS = [
            "/used-vehicles", "/used-cars", "/used-car", "/used-inventory",
            "/pre-owned", "/preowned", "/certified-pre-owned", "/cpo",
            "/shop-used", "/buy-used",
        ]
        PREOWNED_TEXT_KEYWORDS = [
            "used inventory", "used vehicles", "used cars", "pre-owned",
            "preowned", "certified pre-owned", "shop used", "browse used",
            "view used", "our used", "used & certified",
        ]

        base_host = urlparse(target_url).netloc

        url_matched  = None
        text_matched = None

        for link in internal_links:
            href     = link.get("href", "") or ""
            text     = (link.get("text", "") or "").strip().lower()
            pathname = (link.get("pathname", "") or "").lower()

            if not href:
                continue
            try:
                if urlparse(href).netloc != base_host:
                    continue
            except Exception:
                continue

            if url_matched is None and any(p in pathname for p in PREOWNED_PATH_PATTERNS):
                url_matched = href

            if text_matched is None and any(kw in text for kw in PREOWNED_TEXT_KEYWORDS):
                text_matched = href

        return url_matched or text_matched

    def _extract_vehicle_link(
        self, links: list, base_url: str
    ) -> tuple:
        """Scan links captured from an inventory/SRP page and return (url, title) for the
        first vehicle detail page link found.

        Works entirely on data already returned by the audit_page capture —
        no additional extension round-trip required.
        """
        from urllib.parse import urlparse

        base_host = urlparse(base_url).netloc

        best_url = None
        best_title = ""

        for link in links:
            href = link.get("href", "") or ""
            text = (link.get("text", "") or link.get("ariaLabel", "") or "").strip()

            if not href or href.startswith("#") or href.startswith("javascript:"):
                continue

            try:
                parsed = urlparse(href)
            except Exception:
                continue

            # Same-domain only
            if parsed.netloc and parsed.netloc != base_host:
                continue

            path = parsed.path.lower()

            if any(x in path for x in NON_VDP_PATH_SEGMENTS):
                continue

            if _is_likely_vdp_path(path):
                best_url = href
                best_title = text[:120]
                break  # take the first match

        return best_url, best_title or "Vehicle"

    async def _check_vdp_history_reports(
        self, bridge, capture: dict, vdp_url: str, vdp_title: str, page_type: str
    ) -> dict:
        """Extract + verify history-report links for a single VDP capture.

        page_type: result of categorize_inventory_url for this VDP (pass the
        already-resolved session.vdp_page_type for the primary VDP so its SRP
        inheritance fallback is respected; computed fresh for extra candidates).
        """
        links = capture.get("links", [])
        is_preowned = page_type in ("used", "cpo", "mixed")

        widget_reports = (capture.get("inventory_data") or {}).get("history_reports", [])
        raw_reports = extract_history_reports(links)
        if widget_reports and not raw_reports["found"]:
            raw_reports = {"found": True, "count": len(widget_reports), "links": widget_reports}

        verified: list = []
        if is_preowned and raw_reports["found"]:
            eligible = [r for r in raw_reports["links"] if r.get("href")]
            sample = random.sample(eligible, min(4, len(eligible)))
            verified = await verify_history_report_links(sample)

            # Some report links/widgets have no normal navigable href (a
            # "#"/javascript: trigger, or a class/img-based widget with no
            # anchor at all) — verify_history_report_links() unconditionally
            # marks those "not ok" since there's nothing to HTTP-check. Many
            # of these actually work fine as an in-page modal, so before
            # calling them broken, interactively click and check for a
            # modal — a report that opens correctly that way counts as
            # present, not broken. Capped at 2 per vehicle to bound audit time.
            verified_hrefs = {r.get("href") for r in verified if r.get("href")}
            needs_modal_check = [
                r for r in raw_reports["links"]
                if not r.get("href") or r.get("href") not in verified_hrefs
            ]
            for report in needs_modal_check[:2]:
                try:
                    result = await bridge.capture_history_report_check(
                        vdp_url, href=report.get("href"), platform=report.get("platform", ""),
                    )
                except Exception as e:
                    logger.warning(f"History report modal check failed (non-fatal): {e}")
                    continue
                if result.get("found") and result.get("modal_detected"):
                    verified.append({
                        **report,
                        "status_code": None,
                        "ok": True,
                        "opens_new_tab": True,  # opens correctly (as a modal) — counts as present
                        "modal_detected": True,
                    })

        return build_history_reports_check(
            links, verified, is_preowned, vdp_url=vdp_url, vdp_title=vdp_title,
        )

    def _find_preowned_vehicle_links(
        self, links: list, base_url: str, exclude: set, limit: int,
        assume_preowned: bool = False,
    ) -> list:
        """Scan SRP links for additional pre-owned (used/CPO) VDP candidates.

        By default filters by URL alone (categorize_inventory_url) so we only
        spend a page navigation on vehicles that are actually likely to be
        pre-owned — used for sampling multiple VDPs for the history-report
        check. Returns up to `limit` (url, title) tuples, excluding anything
        in `exclude`.

        assume_preowned: when True, skips the categorize_inventory_url(href)
        filter entirely. Use this only when `links` are already known — by
        page context, not URL guessing — to come from a confirmed used/CPO
        SRP (i.e. base_url is that SRP's own URL). Many dealer platforms use
        VIN-only VDP paths with no condition keyword anywhere in the URL
        (e.g. "/vehicle/2019-toyota-camry/JTNB1..."), which makes
        categorize_inventory_url return 'unknown' for every legitimate
        candidate and silently reject all of them — even though the SRP they
        were found on already tells us their condition.
        """
        base_host = urlparse(base_url).netloc
        seen = set(exclude)
        found = []

        for link in links:
            if len(found) >= limit:
                break

            href = link.get("href", "") or ""
            text = (link.get("text", "") or link.get("ariaLabel", "") or "").strip()

            if not href or href.startswith("#") or href.startswith("javascript:"):
                continue
            if href in seen:
                continue

            try:
                parsed = urlparse(href)
            except Exception:
                continue

            if parsed.netloc and parsed.netloc != base_host:
                continue

            path = parsed.path.lower()
            if any(x in path for x in NON_VDP_PATH_SEGMENTS):
                continue
            if not _is_likely_vdp_path(path):
                continue

            if not assume_preowned and categorize_inventory_url(href) not in ("used", "cpo", "mixed"):
                continue

            seen.add(href)
            found.append((href, text[:120] or "Vehicle"))

        return found

    async def _run_dealership_phase(
        self,
        bridge: ExtensionBridge,
        inventory_url: str,
        session: AuditSession,
        emit,
        preowned_url: Optional[str] = None,
    ) -> None:
        """Navigate to inventory page, run inventory checks, then click top vehicle -> VDP.

        preowned_url: if set and different from inventory_url, navigate to it specifically
        to find a used/CPO VDP for history-report and model-year checks.
        """
        try:
            await emit("progress", f"Navigating to inventory page: {inventory_url}", {
                "status": "inventory",
                "inventoryUrl": inventory_url,
            })

            # --- Step 1: Navigate to SRP / inventory page ---
            try:
                inv_capture = await bridge.send_with_retry({
                    "type": "audit_page",
                    "url": inventory_url,
                })
            except HealingError as e:
                logger.warning(f"Inventory page navigation failed: {e}")
                return

            if inv_capture.get("error"):
                logger.warning(f"Inventory page error: {inv_capture['error']}")
                return

            # Save inventory screenshot
            inv_screenshot_b64 = inv_capture.get("screenshot_base64")
            if not inv_screenshot_b64 and inv_capture.get("screenshot_error"):
                logger.warning(f"Screenshot capture failed for inventory SRP {inventory_url}: {inv_capture['screenshot_error']}")
            inv_screenshot_path = None
            if inv_screenshot_b64:
                inv_screenshot_path = self._save_screenshot(
                    inventory_url, inv_screenshot_b64, ViewportType.DESKTOP
                )

            inv_title = (
                inv_capture.get("metadata", {}).get("title")
                or inv_capture.get("seo_data", {}).get("title")
                or "Inventory"
            )

            session.inventory_screenshot = {
                "screenshot_path": inv_screenshot_path,
                "screenshot_url": f"/screenshots/{Path(inv_screenshot_path).name}" if inv_screenshot_path else None,
                "url": inventory_url,
                "title": inv_title,
            }

            # --- Step 2: SRP categorization & analysis ---
            session.inventory_page_type = categorize_inventory_url(inventory_url)
            await emit("progress", f"Inventory page type: {session.inventory_page_type}", {
                "status": "inventory",
                "inventoryTitle": inv_title,
                "inventoryType": session.inventory_page_type,
            })

            inv_links         = inv_capture.get("links", [])
            inv_graphic_links = inv_capture.get("inventory_graphic_links") or []
            inv_images        = inv_capture.get("images", [])
            inv_img_resources = (inv_capture.get("performance_data") or {}).get("imageResources", [])
            inv_data          = inv_capture.get("inventory_data") or {}

            # Run SRP link + image checks concurrently (non-blocking)
            srp_link_task = asyncio.create_task(
                check_inventory_graphic_links(inv_links, inv_graphic_links, inventory_url)
            )
            srp_img_task = asyncio.create_task(
                check_images(inv_images, inventory_url, inv_img_resources)
            )

            # --- Step 3: SRP filter check (extension still on inventory page) ---
            await emit("progress", "Checking SRP filter controls for zero-result combos...", {
                "status": "inventory",
            })
            try:
                # Interactively tests up to 3 filters x 4 options with a 2.5s
                # settle each — ~30s worst case, right at (and in practice
                # observed consistently exceeding) the bridge's 30s default,
                # so this has been silently timing out and failing on every
                # single audit run regardless of what site was being tested.
                filter_result = await bridge.send_and_wait({"type": "check_srp_filters"}, timeout=45)
                session.srp_filter_zero_results = filter_result.get("zero_results", [])
                session.srp_filters_checked     = filter_result.get("filters_checked", 0)
                session.srp_filter_type         = filter_result.get("filter_type", "none")
                session.srp_filter_note         = filter_result.get("note", "")
            except Exception as fe:
                logger.warning(f"SRP filter check error (non-fatal): {fe}")
                await emit("progress", f"SRP filter check failed (non-fatal): {fe}", {
                    "status": "inventory",
                })

            # Collect SRP link/image results
            inv_broken_links, inv_checked_links = await srp_link_task
            inv_broken_imgs, _inv_over, _inv_img_issues = await srp_img_task
            session.inventory_broken_links  = inv_broken_links
            session.inventory_checked_links = inv_checked_links
            session.inventory_broken_images = inv_broken_imgs

            # Expired dates on SRP
            for date_entry in (inv_capture.get("page_dates") or []):
                session.inventory_expired_dates.append({
                    **date_entry, "source_page": inventory_url, "page_kind": "srp",
                })

            # Collect model years from SRP inventory data
            srp_years = inv_data.get("vehicle_years", [])

            # --- Step 4: Find VDP link ---
            # If the main SRP is new inventory but a pre-owned URL was found separately,
            # navigate to the pre-owned page first to find a used VDP.
            vdp_search_links = inv_links
            vdp_search_base  = inventory_url
            preowned_years: list = []

            # The discovery-phase link list is capped to 50 and may have missed the
            # used/pre-owned nav item — re-search using the SRP's own (uncapped) links.
            if not preowned_url or preowned_url == inventory_url:
                preowned_url = self._find_preowned_url(inv_links, inventory_url) or preowned_url

            needs_preowned_nav = (
                preowned_url
                and preowned_url != inventory_url
                and session.inventory_page_type in ("new", "unknown", "mixed")
            )

            if needs_preowned_nav:
                await emit("progress", f"Checking pre-owned inventory for VDP: {preowned_url}", {
                    "status": "inventory",
                    "preownedUrl": preowned_url,
                })
                try:
                    po_capture = await bridge.send_with_retry({
                        "type": "audit_page",
                        "url": preowned_url,
                    })
                    await emit("progress",
                        f"[diag] pre-owned SRP raw response — keys: {sorted(po_capture.keys())}, "
                        f"type field: {po_capture.get('type', '(none)')}",
                        {"status": "inventory"},
                    )
                    if po_capture.get("error"):
                        await emit("progress", f"Pre-owned SRP capture returned an error: {po_capture['error']}", {
                            "status": "inventory",
                        })
                    else:
                        vdp_search_links = po_capture.get("links", [])
                        vdp_search_base  = preowned_url
                        po_meta = po_capture.get("metadata") or {}
                        await emit("progress",
                            f"Pre-owned SRP captured — {len(vdp_search_links)} link(s) found on page "
                            f"(landed on: {po_meta.get('url', '?')}, title: \"{po_meta.get('title', '?')}\", "
                            f"HTML size: {po_meta.get('contentLength', '?')} chars)",
                            {"status": "inventory"},
                        )
                        preowned_years   = (po_capture.get("inventory_data") or {}).get("vehicle_years", [])
                        # Collect expired dates from pre-owned SRP
                        for date_entry in (po_capture.get("page_dates") or []):
                            session.inventory_expired_dates.append({
                                **date_entry, "source_page": preowned_url, "page_kind": "srp_preowned",
                            })

                        # Surface the pre-owned SRP as its own section (screenshot +
                        # broken link/image checks) — previously this page was only
                        # visited to find a used VDP and its own results were discarded.
                        session.preowned_inventory_page_type = categorize_inventory_url(preowned_url)

                        po_screenshot_b64 = po_capture.get("screenshot_base64")
                        po_screenshot_path = None
                        if po_screenshot_b64:
                            po_screenshot_path = self._save_screenshot(
                                preowned_url, po_screenshot_b64, ViewportType.DESKTOP
                            )
                        po_title = (
                            po_capture.get("metadata", {}).get("title")
                            or po_capture.get("seo_data", {}).get("title")
                            or "Pre-Owned Inventory"
                        )
                        session.preowned_inventory_screenshot = {
                            "screenshot_path": po_screenshot_path,
                            "screenshot_url": f"/screenshots/{Path(po_screenshot_path).name}" if po_screenshot_path else None,
                            "url": preowned_url,
                            "title": po_title,
                        }

                        po_graphic_links = po_capture.get("inventory_graphic_links") or []
                        po_images        = po_capture.get("images", [])
                        po_img_resources = (po_capture.get("performance_data") or {}).get("imageResources", [])

                        po_broken_links, po_checked_links = await check_inventory_graphic_links(
                            vdp_search_links, po_graphic_links, preowned_url
                        )
                        po_broken_imgs, _po_over, _po_img_issues = await check_images(
                            po_images, preowned_url, po_img_resources
                        )
                        session.preowned_inventory_broken_links  = po_broken_links
                        session.preowned_inventory_checked_links = po_checked_links
                        session.preowned_inventory_broken_images = po_broken_imgs
                except Exception as po_err:
                    logger.warning(f"Pre-owned SRP navigation failed (non-fatal): {po_err}")
                    await emit("progress", f"Pre-owned SRP navigation failed (non-fatal): {po_err}", {
                        "status": "inventory",
                    })

            await emit("progress", "Looking for top vehicle on inventory page...", {
                "status": "inventory",
            })

            # The PRIMARY VDP (shown as "VDP Type" alongside "SRP Type" in the
            # Inventory Discovery card) must come from the SRP actually being
            # audited — always try inv_links/inventory_url first. Previously
            # this searched vdp_search_links/vdp_search_base first, which
            # points at preowned_url whenever needs_preowned_nav fired (SRP is
            # new/mixed/unknown + a separate used/CPO page was found) — so a
            # "SRP Type: New Inventory" audit could end up showing "VDP Type:
            # Certified Pre-Owned", a vehicle-type mismatch between the two
            # cards. The pre-owned SRP is still visited (above) and still
            # searched for EXTRA vehicles below — just no longer for this
            # primary one.
            vehicle_url, vehicle_title = self._extract_vehicle_link(inv_links, inventory_url)
            vehicle_source_base = inventory_url

            # Fallback: only if the primary SRP itself has no findable vehicle
            # link at all, fall back to whatever the pre-owned SRP found —
            # better to show some VDP than none.
            if not vehicle_url and vdp_search_base != inventory_url:
                vehicle_url, vehicle_title = self._extract_vehicle_link(vdp_search_links, vdp_search_base)
                vehicle_source_base = vdp_search_base

            def _store_model_years_if_new(years):
                if session.inventory_page_type in ("new", "mixed", "unknown") and years:
                    session.model_year_check = extract_model_years(
                        years, srp_url=inventory_url
                    )

            if not vehicle_url:
                logger.info("No vehicle link found in inventory page links — skipping VDP phase")
                _store_model_years_if_new(srp_years)  # only new-SRP years
                return

            logger.info(f"Found vehicle link: {vehicle_url} ({vehicle_title})")
            await emit("progress", f"Found vehicle: {vehicle_title}. Opening VDP...", {
                "status": "vdp",
                "vehicleTitle": vehicle_title,
                "vehicleUrl": vehicle_url,
            })

            # --- Step 5: Navigate to VDP ---
            try:
                vdp_capture = await bridge.send_with_retry({
                    "type": "audit_page",
                    "url": vehicle_url,
                })
            except HealingError as e:
                logger.warning(f"VDP navigation failed: {e}")
                _store_model_years_if_new(srp_years)
                return

            if vdp_capture.get("error"):
                logger.warning(f"VDP capture error: {vdp_capture['error']}")
                _store_model_years_if_new(srp_years)
                return

            vdp_screenshot_b64 = vdp_capture.get("screenshot_base64")
            if not vdp_screenshot_b64 and vdp_capture.get("screenshot_error"):
                logger.warning(f"Screenshot capture failed for VDP {vehicle_url}: {vdp_capture['screenshot_error']}")
            vdp_screenshot_path = None
            if vdp_screenshot_b64:
                vdp_screenshot_path = self._save_screenshot(
                    vehicle_url, vdp_screenshot_b64, ViewportType.DESKTOP
                )

            vdp_title = (
                vdp_capture.get("metadata", {}).get("title")
                or vdp_capture.get("seo_data", {}).get("title")
                or vehicle_title
            )

            session.vdp_screenshot = {
                "screenshot_path": vdp_screenshot_path,
                "screenshot_url": f"/screenshots/{Path(vdp_screenshot_path).name}" if vdp_screenshot_path else None,
                "url": vehicle_url,
                "title": vdp_title,
            }

            await emit("progress", f"VDP captured: {vdp_title}", {
                "status": "vdp",
                "vdpTitle": vdp_title,
                "vdpUrl": vehicle_url,
                "vdpScreenshotUrl": session.vdp_screenshot.get("screenshot_url"),
            })

            # --- Step 6: VDP categorization & analysis ---
            session.vdp_page_type = categorize_inventory_url(vehicle_url)

            # If VDP URL is ambiguous, infer from the SRP we actually found
            # this vehicle on (vehicle_source_base — see above), not just
            # whichever SRP happened to be visited last.
            if session.vdp_page_type == 'unknown':
                if vehicle_source_base == preowned_url and preowned_url:
                    # We found this VDP from the pre-owned SRP — it's pre-owned
                    session.vdp_page_type = categorize_inventory_url(preowned_url) or 'used'
                else:
                    # Found from the primary SRP itself — inherit its condition,
                    # keeping "SRP Type" and "VDP Type" consistent with each other.
                    session.vdp_page_type = session.inventory_page_type

            vdp_links         = vdp_capture.get("links", [])
            vdp_graphic_links = vdp_capture.get("inventory_graphic_links") or []
            vdp_images        = vdp_capture.get("images", [])
            vdp_img_resources = (vdp_capture.get("performance_data") or {}).get("imageResources", [])
            vdp_data          = vdp_capture.get("inventory_data") or {}

            # VDP link + image checks concurrently
            vdp_link_task = asyncio.create_task(
                check_inventory_graphic_links(vdp_links, vdp_graphic_links, vehicle_url)
            )
            vdp_img_task = asyncio.create_task(
                check_images(vdp_images, vehicle_url, vdp_img_resources)
            )

            # History reports — checked across up to HISTORY_REPORT_VEHICLE_TARGET
            # distinct pre-owned VDPs, not just the primary one, so a single
            # vehicle missing/broken links doesn't skew the whole check.
            is_preowned = session.vdp_page_type in ("used", "cpo", "mixed")

            vehicle_checks = [await self._check_vdp_history_reports(
                bridge, vdp_capture, vehicle_url, vdp_title, session.vdp_page_type,
            )]
            visited_vdp_urls = {vehicle_url}

            remaining = HISTORY_REPORT_VEHICLE_TARGET - 1
            if remaining > 0:
                # Two pools, searched separately because they carry different
                # confidence about condition: vdp_search_links (only when it
                # came from a confirmed pre-owned SRP — see needs_preowned_nav
                # above) can be trusted by page context even when individual
                # VDP URLs don't self-identify as used/CPO; inv_links (the
                # primary/new SRP) can't, so those still need each URL to
                # verify its own condition.
                candidates: list[tuple[str, str, bool]] = []
                confirmed_preowned_pool = vdp_search_base != inventory_url
                if confirmed_preowned_pool:
                    po_candidates = self._find_preowned_vehicle_links(
                        vdp_search_links, vdp_search_base,
                        exclude=visited_vdp_urls,
                        limit=HISTORY_REPORT_CANDIDATE_ATTEMPTS,
                        assume_preowned=True,
                    )
                    candidates.extend((u, t, True) for u, t in po_candidates)

                if len(candidates) < HISTORY_REPORT_CANDIDATE_ATTEMPTS:
                    exclude_now = visited_vdp_urls | {u for u, _, _ in candidates}
                    extra_candidates = self._find_preowned_vehicle_links(
                        inv_links, inventory_url,
                        exclude=exclude_now,
                        limit=HISTORY_REPORT_CANDIDATE_ATTEMPTS - len(candidates),
                    )
                    candidates.extend((u, t, False) for u, t in extra_candidates)

                await emit("progress",
                    f"Found {len(candidates)} extra pre-owned VDP candidate(s) "
                    f"(pool sizes: pre-owned SRP={len(vdp_search_links)}, primary SRP={len(inv_links)})",
                    {"status": "inventory"},
                )

                for cand_url, cand_title, from_confirmed_preowned in candidates:
                    if remaining <= 0:
                        break
                    if cand_url in visited_vdp_urls:
                        continue
                    visited_vdp_urls.add(cand_url)
                    try:
                        cand_capture = await bridge.send_with_retry({
                            "type": "audit_page", "url": cand_url,
                        })
                    except HealingError as ce:
                        logger.warning(f"Extra pre-owned VDP navigation failed (non-fatal): {ce}")
                        continue
                    if cand_capture.get("error"):
                        continue

                    cand_title_final = (
                        cand_capture.get("metadata", {}).get("title")
                        or cand_capture.get("seo_data", {}).get("title")
                        or cand_title
                    )
                    cand_page_type = categorize_inventory_url(cand_url)
                    if cand_page_type == "unknown" and from_confirmed_preowned:
                        # URL itself gave no signal — trust the SRP it was
                        # found on instead of defaulting to "not preowned".
                        cand_page_type = categorize_inventory_url(vdp_search_base) or "used"
                    vehicle_checks.append(await self._check_vdp_history_reports(
                        bridge, cand_capture, cand_url, cand_title_final, cand_page_type,
                    ))
                    remaining -= 1

            session.history_reports_check = aggregate_history_reports_check(vehicle_checks)

            # Expired dates on VDP
            for date_entry in (vdp_capture.get("page_dates") or []):
                session.inventory_expired_dates.append({
                    **date_entry, "source_page": vehicle_url, "page_kind": "vdp",
                })

            # Model years — only relevant for new inventory (used lots naturally have old years).
            # Use only srp_years from the primary new-inventory SRP; exclude preowned_years
            # (used-vehicle SRP) and VDP years when the VDP itself is a pre-owned vehicle.
            if session.inventory_page_type in ("new", "mixed", "unknown"):
                new_vdp_years = [] if is_preowned else vdp_data.get("vehicle_years", [])
                session.model_year_check = extract_model_years(
                    srp_years + new_vdp_years,
                    srp_url=inventory_url,
                )

            vdp_broken_links, vdp_checked_links = await vdp_link_task
            vdp_broken_imgs, _vdp_over, _vdp_img_issues = await vdp_img_task
            session.vdp_broken_links  = vdp_broken_links
            session.vdp_checked_links = vdp_checked_links
            session.vdp_broken_images = vdp_broken_imgs

            inv_broken_count = len(session.inventory_broken_links)
            vdp_broken_count = len(session.vdp_broken_links)
            hist_found = (session.history_reports_check or {}).get("found", False)
            outdated_count = len((session.model_year_check or {}).get("outdated", []))
            await emit("progress",
                f"Inventory checks complete — "
                f"{inv_broken_count} SRP broken links, "
                f"{vdp_broken_count} VDP broken links, "
                f"history reports {'found' if hist_found else 'not found'}, "
                f"{outdated_count} outdated model year(s)",
                {"status": "inventory"},
            )

        except Exception as e:
            logger.warning(f"Dealership phase error (non-fatal): {e}", exc_info=True)

    async def _run_contact_form_phase(
        self,
        bridge: ExtensionBridge,
        contact_url: str,
        session: AuditSession,
        emit,
    ) -> None:
        """Navigate to contact page, fill dummy data (no submit), screenshot."""
        try:
            await emit("progress", f"Navigating to contact page: {contact_url}", {
                "status": "contact_form",
                "contactUrl": contact_url,
            })

            try:
                contact_capture = await bridge.send_with_retry({
                    "type": "audit_page",
                    "url": contact_url,
                })
            except HealingError as e:
                logger.warning(f"Contact page navigation failed: {e}")
                return

            if contact_capture.get("error"):
                logger.warning(f"Contact page error: {contact_capture['error']}")
                return

            await emit("progress", "Filling contact form with test data...", {
                "status": "contact_form",
            })

            try:
                fill_result = await bridge.send_with_retry({"type": "fill_contact_form"})
            except HealingError as e:
                logger.warning(f"Contact form fill failed: {e}")
                return

            screenshot_b64 = fill_result.get("screenshot_base64")
            screenshot_path = None
            if screenshot_b64:
                screenshot_path = self._save_screenshot(contact_url, screenshot_b64, ViewportType.DESKTOP)

            fields_filled = fill_result.get("filled", [])
            session.contact_form_screenshot = {
                "screenshot_path": screenshot_path,
                "screenshot_url": f"/screenshots/{Path(screenshot_path).name}" if screenshot_path else None,
                "url": contact_url,
                "title": "Contact Form (filled with test data)",
                "fields_filled": fields_filled,
                "fields_count": fill_result.get("count", 0),
            }

            await emit("progress", f"Contact form filled ({len(fields_filled)} fields)", {
                "status": "contact_form",
                "fieldsFilled": len(fields_filled),
                "contactScreenshotUrl": session.contact_form_screenshot.get("screenshot_url"),
            })

        except Exception as e:
            logger.warning(f"Contact form phase error (non-fatal): {e}", exc_info=True)

    async def _run_trade_value_phase(
        self,
        bridge,
        session: AuditSession,
        emit,
    ) -> None:
        """If a trade-in tool was detected, interact with it and screenshot the
        result. Many dealer trade-in widgets (KBB/TradePending/AccuTrade/Edmunds-
        style) reveal the vehicle's value in a modal only after a vehicle is
        entered — often rendered via a cross-origin iframe overlay. This enters
        a generic real vehicle and captures whatever appears (modal or page)."""
        feature = session.dealership_features.get("trade_in_tool", {})
        if not feature.get("detected"):
            return

        # Prefer feature_url (the actual trade-in link/page) over source_page
        # (whichever page the feature was first detected on — often just the
        # homepage when detection came from a sitewide script/iframe match).
        source_page = feature.get("feature_url") or feature.get("source_page") or session.target_url
        try:
            await emit("progress", "Checking trade-value tool for a popup...", {
                "status": "trade_value",
            })
            result = await bridge.capture_trade_value(source_page)
        except HealingError as e:
            logger.warning(f"Trade value capture failed: {e}")
            return

        if not result.get("found"):
            return

        screenshot_b64 = result.get("screenshot_base64")
        screenshot_path = None
        if screenshot_b64:
            screenshot_path = self._save_screenshot(source_page, screenshot_b64, ViewportType.DESKTOP)

        session.trade_value_screenshot = {
            "screenshot_path": screenshot_path,
            "screenshot_url": f"/screenshots/{Path(screenshot_path).name}" if screenshot_path else None,
            "url": source_page,
            "provider": feature.get("provider", ""),
            "modal_detected": result.get("modal_detected", False),
        }

        modal_note = " (modal detected)" if result.get("modal_detected") else ""
        await emit("progress", f"Trade-value screenshot captured{modal_note}", {
            "status": "trade_value",
            "tradeValueScreenshotUrl": session.trade_value_screenshot.get("screenshot_url"),
        })

    async def _run_feature_click_phase(
        self,
        bridge,
        session: AuditSession,
        emit,
        feature_key: str,
        label: str,
    ) -> None:
        """For features whose only trigger is a javascript:-href or plain
        <button> (no navigable feature_url — e.g. "Schedule Service" / "Order
        Parts"), click it in place and screenshot whatever modal appears,
        rather than reporting the feature as merely 'not detected'."""
        feature = session.dealership_features.get(feature_key, {})
        click_text = feature.get("modal_trigger_text")
        if not feature.get("detected") or not click_text:
            return

        source_page = feature.get("source_page") or session.target_url
        try:
            await emit("progress", f"Checking {label} for a popup...", {"status": feature_key})
            result = await bridge.capture_feature_click(click_text, source_page)
        except HealingError as e:
            logger.warning(f"{label} click capture failed: {e}")
            return

        if not result.get("found"):
            return

        screenshot_b64 = result.get("screenshot_base64")
        screenshot_path = None
        if screenshot_b64:
            screenshot_path = self._save_screenshot(source_page, screenshot_b64, ViewportType.DESKTOP)

        session.feature_modal_screenshots[feature_key] = {
            "screenshot_path": screenshot_path,
            "screenshot_url": f"/screenshots/{Path(screenshot_path).name}" if screenshot_path else None,
            "url": source_page,
            "label": label,
            "modal_detected": result.get("modal_detected", False),
        }

        modal_note = " (modal detected)" if result.get("modal_detected") else ""
        await emit("progress", f"{label} screenshot captured{modal_note}", {
            "status": feature_key,
            "screenshotUrl": session.feature_modal_screenshots[feature_key].get("screenshot_url"),
        })

    async def _run_homepage_checks(
        self,
        session: AuditSession,
        desktop_capture: dict,
        mobile_capture: dict,
        emit,
    ) -> None:
        """Sprint 5 — run all homepage-specific checks after homepage captures."""
        try:
            await emit("progress", "Running homepage-specific checks...", {"status": "homepage_checks"})

            base_url = session.target_url
            carousel_desktop = desktop_capture.get("carousel_data") or []
            carousel_mobile = mobile_capture.get("carousel_data") or []
            cta_links = desktop_capture.get("cta_links") or []
            nav_links = desktop_capture.get("nav_menu_data") or []
            logo_info = desktop_capture.get("header_logo_info") or {}
            social_links = desktop_capture.get("social_links") or []
            images = desktop_capture.get("images") or []
            image_resources = (desktop_capture.get("performance_data") or {}).get("imageResources") or []

            # Run all async checks concurrently
            (
                carousel_result,
                cta_result,
                homepage_img_broken,
                nav_result,
                social_result,
            ) = await asyncio.gather(
                check_carousel_links(carousel_desktop, base_url),
                check_cta_links(cta_links, base_url),
                check_homepage_content_images(images, base_url, image_resources),
                check_nav_links(nav_links, base_url),
                check_social_media_links(social_links),
            )

            carousel_broken, carousel_checked = carousel_result
            cta_broken, cta_checked = cta_result
            nav_broken, nav_dupes, nav_checked = nav_result
            social_broken, social_no_new_tab, social_checked = social_result

            # Synchronous checks
            session.carousel_broken_links = carousel_broken
            session.carousel_checked_links = carousel_checked
            session.carousel_link_relevance = assess_carousel_relevance(carousel_desktop)
            session.cta_broken_links = cta_broken
            session.cta_checked_links = cta_checked
            session.homepage_broken_content_images = homepage_img_broken
            session.carousel_dimension_issues_desktop = analyze_slide_dimensions(carousel_desktop)
            session.carousel_dimension_issues_mobile = analyze_slide_dimensions(carousel_mobile)
            session.nav_broken_links = nav_broken
            session.nav_checked_links = nav_checked
            session.nav_duplicate_links = nav_dupes
            session.header_logo_check = check_header_logo(logo_info, base_url)
            session.social_media_broken = social_broken
            session.social_media_no_new_tab = social_no_new_tab
            session.social_media_checked = social_checked

            summary_parts = []
            if carousel_broken:
                summary_parts.append(f"{len(carousel_broken)} broken carousel link(s)")
            if cta_broken:
                summary_parts.append(f"{len(cta_broken)} broken CTA link(s)")
            if nav_broken:
                summary_parts.append(f"{len(nav_broken)} broken nav link(s)")
            if social_broken:
                summary_parts.append(f"{len(social_broken)} broken social link(s)")
            if session.carousel_dimension_issues_desktop:
                summary_parts.append("carousel dimension inconsistencies found")

            msg = "Homepage checks complete" + (f": {', '.join(summary_parts)}" if summary_parts else " — all clear")
            await emit("progress", msg, {"status": "homepage_checks"})

        except Exception as e:
            logger.warning(f"Homepage checks error (non-fatal): {e}", exc_info=True)

    async def _run_gbp_phase(self, session: AuditSession, emit) -> None:
        """Query Google Business Profile and compare address + hours."""
        try:
            binfo = session.business_info_website or {}
            # Use `or ""` so JS null values don't sneak through as None
            dealer_name   = binfo.get("name") or ""
            website_addr  = binfo.get("address") or ""
            website_hours = binfo.get("hours") or {}

            website_url = session.target_url or ""

            if not dealer_name and not website_addr and not website_url:
                logger.info("[GBP] No dealer name/address/URL found — skipping GBP phase")
                return

            await emit("progress", f"Checking Google Business Profile for: {dealer_name or website_url}", {
                "status": "gbp",
            })

            gbp = await fetch_gbp_data(dealer_name, website_addr, GBP_API_KEY, website_url=website_url)
            if not gbp:
                logger.warning("[GBP] No Google listing found")
                await emit("progress", "No matching Google Business Profile found", {"status": "gbp"})
                return

            session.gbp_data = gbp
            logger.info(f"[GBP] Found: {gbp.get('name')} — {gbp.get('formatted_address')}")

            # Address comparison
            session.address_comparison = compare_address(website_addr, gbp.get("formatted_address", ""))

            # Hours comparison
            if website_hours and gbp.get("hours"):
                session.hours_discrepancies = compare_hours(website_hours, gbp["hours"])

            disc_count = len(session.hours_discrepancies)
            addr_ok    = session.address_comparison.get("match", None)
            await emit("progress",
                f"GBP: {'✓ Address match' if addr_ok else '⚠ Address mismatch'}, "
                f"{disc_count} hours discrepanc{'ies' if disc_count != 1 else 'y'}",
                {"status": "gbp", "address_match": addr_ok, "hours_discrepancies": disc_count},
            )

        except Exception as e:
            logger.warning(f"GBP phase error (non-fatal): {e}", exc_info=True)

    async def _process_capture_for_viewport(
        self,
        url: str,
        capture: dict,
        session: AuditSession,
        bridge: ExtensionBridge,
        viewport: ViewportType,
    ) -> ViewportResult:
        """Process a single page capture for a specific viewport through analysis modules."""
        metadata = capture.get("metadata", {})
        seo_data = capture.get("seo_data", {})
        perf_data = capture.get("performance_data", {})
        axe_results = capture.get("axe_results", {})
        links = capture.get("links", [])
        images = capture.get("images", [])
        image_resources = perf_data.get("imageResources", [])
        js_errors_raw = capture.get("js_errors", [])
        forms = capture.get("forms", [])
        buttons = capture.get("buttons", [])
        page_features = capture.get("page_features") or {}
        phone_numbers = capture.get("phone_numbers") or []
        business_info = capture.get("business_info") or {}
        # Sprint 5 — per-page homepage data (passed through from content script)
        carousel_data = capture.get("carousel_data") or []
        cta_links_raw = capture.get("cta_links") or []
        nav_menu_data = capture.get("nav_menu_data") or []
        header_logo_info = capture.get("header_logo_info") or {}
        social_links = capture.get("social_links") or []
        page_dates = capture.get("page_dates") or []
        screenshot_b64 = capture.get("screenshot_base64")
        if not screenshot_b64 and capture.get("screenshot_error"):
            logger.warning(f"Screenshot capture failed for {url} ({viewport.value}): {capture['screenshot_error']}")

        # Determine page type
        title = metadata.get("title", "") or seo_data.get("title", "")
        page_type = "homepage" if url == session.target_url else "other"

        url_lower = url.lower()
        for ptype in ["about", "contact", "blog", "services", "products", "faq", "privacy", "careers"]:
            if f"/{ptype}" in url_lower:
                page_type = ptype
                break

        # Save screenshot with viewport suffix
        screenshot_path = None
        screenshot_url = None
        if screenshot_b64:
            screenshot_path = self._save_screenshot(url, screenshot_b64, viewport)
            if screenshot_path:
                screenshot_url = f"/screenshots/{Path(screenshot_path).name}"

        # Run analysis modules
        a11y_issues = analyze_accessibility(axe_results, metadata)
        seo_issues = analyze_seo(seo_data)
        perf_issues = analyze_performance(perf_data)

        broken_links, link_issues = await check_links(links, url)

        # Image checks (desktop only — same images on mobile, avoid duplicate requests)
        broken_images: list = []
        oversized_images: list = []
        img_issues: list = []
        if viewport == ViewportType.DESKTOP:
            broken_images, oversized_images, img_issues = await check_images(
                images, url, image_resources
            )

        # Normalize JS errors (add page context)
        js_errors = [
            {
                "message": e.get("message", "")[:300],
                "source": e.get("source", ""),
                "line": e.get("line", 0),
                "col": e.get("col", 0),
                "stack": e.get("stack", "")[:300],
            }
            for e in (js_errors_raw or [])
            if e.get("message")
        ]

        # Page load time from Navigation Timing
        load_time_ms: Optional[float] = None
        if perf_data.get("fullLoad"):
            load_time_ms = float(perf_data["fullLoad"])

        all_issues = a11y_issues + seo_issues + perf_issues + link_issues + img_issues

        result = ViewportResult(
            url=url,
            viewport=viewport,
            title=title,
            page_type=page_type,
            screenshot_path=screenshot_path,
            screenshot_url=screenshot_url,
            issues=all_issues,
            axe_results=axe_results,
            seo_data=seo_data,
            performance_data=perf_data,
            broken_links=broken_links,
            broken_images=broken_images,
            oversized_images=oversized_images,
            js_errors=js_errors,
            load_time_ms=load_time_ms,
            links=links,
            forms=forms,
            buttons=buttons,
            page_features=page_features,
            phone_numbers=phone_numbers,
            business_info=business_info,
            carousel_data=carousel_data,
            cta_links=cta_links_raw,
            nav_menu_data=nav_menu_data,
            header_logo_info=header_logo_info,
            social_links=social_links,
            page_dates=page_dates,
        )

        return result

    def _viewport_to_page_result(self, vr: ViewportResult) -> PageAuditResult:
        """Convert a ViewportResult to a legacy PageAuditResult for backward compat."""
        return PageAuditResult(
            url=vr.url,
            title=vr.title,
            page_type=vr.page_type,
            screenshot_path=vr.screenshot_path,
            screenshot_url=vr.screenshot_url,
            issues=vr.issues,
            axe_results=vr.axe_results,
            seo_data=vr.seo_data,
            performance_data=vr.performance_data,
            broken_links=vr.broken_links,
        )

    def _aggregate_sprint_metrics(
        self,
        session: AuditSession,
        desktop_result: ViewportResult,
        mobile_result: Optional[ViewportResult],
        page_url: str,
    ) -> None:
        """Aggregate per-page sprint metrics into session-level deduplicated collections."""
        if desktop_result and not desktop_result.error:
            # Broken links — deduplicate by URL
            known_link_urls = {l.get("url") for l in session.broken_links_all}
            for link in desktop_result.broken_links:
                if link.get("url") and link["url"] not in known_link_urls:
                    session.broken_links_all.append({**link, "source_page": page_url})
                    known_link_urls.add(link["url"])

            # Broken images — deduplicate by src
            known_img_srcs = {i.get("src") for i in session.broken_images_all}
            for img in desktop_result.broken_images:
                if img.get("src") and img["src"] not in known_img_srcs:
                    session.broken_images_all.append({**img, "source_page": page_url})
                    known_img_srcs.add(img["src"])

            # Oversized images — deduplicate by src
            known_over_srcs = {i.get("src") for i in session.oversized_images_all}
            for img in desktop_result.oversized_images:
                if img.get("src") and img["src"] not in known_over_srcs:
                    session.oversized_images_all.append({**img, "source_page": page_url})
                    known_over_srcs.add(img["src"])

            # JS errors from desktop
            for err in desktop_result.js_errors:
                session.js_errors_all.append({**err, "source_page": page_url, "viewport": "desktop"})

            # Desktop load time
            if desktop_result.load_time_ms:
                session.page_load_times.append({
                    "url": page_url,
                    "title": desktop_result.title or page_url,
                    "load_time_ms": desktop_result.load_time_ms,
                    "viewport": "desktop",
                })

            # Sprint 2: aggregate dealership feature detections (first page that detects wins)
            features = desktop_result.page_features or {}
            self._merge_dealership_features(session, features, page_url)

            # Sprint 4: aggregate business_info (first page with useful data wins)
            # Guard: JS null → Python None, so use `or` to treat null as falsy
            binfo = desktop_result.business_info or {}
            bi_name = binfo.get("name") or ""
            bi_addr = binfo.get("address") or ""
            bi_hours = binfo.get("hours") or {}
            if bi_name or bi_addr or bi_hours:
                if session.business_info_website is None:
                    session.business_info_website = {
                        "name": bi_name, "address": bi_addr, "hours": bi_hours,
                        "source_page": page_url,
                    }
                else:
                    existing = session.business_info_website
                    if not existing.get("name") and bi_name:
                        existing["name"] = bi_name
                    if not existing.get("address") and bi_addr:
                        existing["address"] = bi_addr
                    if not existing.get("hours") and bi_hours:
                        existing["hours"] = bi_hours

            # Sprint 3: aggregate phone numbers (deduplicate by normalized 10-digit key)
            known_phones = {self._normalize_phone(p["number"]) for p in session.phone_numbers_all}
            for ph in desktop_result.phone_numbers:
                norm = self._normalize_phone(ph.get("number", ""))
                if norm and norm not in known_phones:
                    known_phones.add(norm)
                    session.phone_numbers_all.append({**ph, "source_page": page_url})

            # Sprint 5: aggregate expired dates (req 7 — scan nav pages for expired content)
            for date_entry in desktop_result.page_dates:
                session.nav_expired_dates.append({**date_entry, "source_page": page_url})

        if mobile_result and not mobile_result.error:
            # JS errors from mobile
            for err in mobile_result.js_errors:
                session.js_errors_all.append({**err, "source_page": page_url, "viewport": "mobile"})

            # Mobile load time
            if mobile_result.load_time_ms:
                session.page_load_times.append({
                    "url": page_url,
                    "title": mobile_result.title or page_url,
                    "load_time_ms": mobile_result.load_time_ms,
                    "viewport": "mobile",
                })

    @staticmethod
    def _normalize_phone(raw: str) -> str:
        """Strip non-digits, return last 10 digits for deduplication."""
        import re
        digits = re.sub(r"\D", "", raw or "")
        return digits[-10:] if len(digits) >= 10 else digits

    def _merge_dealership_features(
        self,
        session: AuditSession,
        page_features: dict,
        page_url: str,
    ) -> None:
        """Merge per-page dealership feature signals into session-level dict.

        First page that detects a feature wins — subsequent pages do not
        overwrite an already-detected entry.
        """
        for feature_key in ("contact_form", "finance_form", "trade_in_tool",
                             "service_scheduling", "parts_form", "live_chat"):
            detected_in_session = session.dealership_features.get(feature_key, {})
            if detected_in_session.get("detected"):
                continue  # already found on a prior page

            page_signal = page_features.get(feature_key, {})
            if page_signal.get("detected"):
                updated = {**page_signal, "source_page": page_url}
                session.dealership_features[feature_key] = updated

    def _save_screenshot(self, url: str, b64_data: str, viewport: ViewportType = ViewportType.DESKTOP) -> Optional[str]:
        """Decode and save a base64 screenshot with viewport suffix."""
        try:
            screenshots_dir = Path("screenshots")
            screenshots_dir.mkdir(exist_ok=True)

            domain = urlparse(url).netloc.replace(".", "_")
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{domain}_{viewport.value}_{ts}.png"
            filepath = screenshots_dir / filename

            img_bytes = base64.b64decode(b64_data)
            filepath.write_bytes(img_bytes)
            return str(filepath)
        except Exception as e:
            logger.error(f"Failed to save screenshot: {e}")
            return None
