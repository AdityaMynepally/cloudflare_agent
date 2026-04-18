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
            if inventory_url:
                await self._run_dealership_phase(
                    bridge, inventory_url, session, emit
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
                "broken_links_count": len(session.broken_links_all),
                "broken_images_count": len(session.broken_images_all),
                "oversized_images_count": len(session.oversized_images_all),
                "js_errors_count": len(session.js_errors_all),
                "dealership_features": session.dealership_features,
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

        Priority order:
        1. Explicit 'inventory' category from page-patterns.js
        2. URL path matches a known inventory pattern
        3. Link text contains inventory-related keywords
        """
        # 1. Only use the 'inventory' category — not 'services' or 'products'
        if categorized.get("inventory"):
            return categorized["inventory"]

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

        # URL path segments that indicate a VDP (vehicle detail page)
        VDP_PATH_HINTS = [
            "/inventory/", "/vehicle/", "/vdp/", "/listing/",
            "/new/", "/used/", "/certified/", "/cars/", "/trucks/", "/suvs/",
            "/detail/", "/details/",
        ]
        # Segments that mean it is NOT a VDP (navigation, filters, etc.)
        NON_VDP = [
            "/search", "/filter", "/sort", "/compare", "/wishlist",
            "/contact", "/service", "/about", "/finance", "/blog",
            "/specials", "/directions", "/hours", "/careers",
        ]

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

            if any(x in path for x in NON_VDP):
                continue

            if any(hint in path for hint in VDP_PATH_HINTS):
                best_url = href
                best_title = text[:120]
                break  # take the first match

        return best_url, best_title or "Vehicle"

    async def _run_dealership_phase(
        self,
        bridge: ExtensionBridge,
        inventory_url: str,
        session: AuditSession,
        emit,
    ) -> None:
        """Navigate to inventory page, screenshot it, then click top vehicle -> VDP screenshot."""
        try:
            await emit("progress", f"Navigating to inventory page: {inventory_url}", {
                "status": "inventory",
                "inventoryUrl": inventory_url,
            })

            # Navigate to inventory / SRP
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

            await emit("progress", "Found inventory page, looking for top vehicle...", {
                "status": "inventory",
                "inventoryTitle": inv_title,
            })

            # Parse vehicle links directly from the captured page data —
            # no extra round-trip to the extension needed.
            vehicle_url, vehicle_title = self._extract_vehicle_link(
                inv_capture.get("links", []), inventory_url
            )
            if not vehicle_url:
                logger.info("No vehicle link found in inventory page links — skipping VDP phase")
                return

            logger.info(f"Found vehicle link: {vehicle_url} ({vehicle_title})")
            await emit("progress", f"Found vehicle: {vehicle_title}. Opening VDP...", {
                "status": "vdp",
                "vehicleTitle": vehicle_title,
                "vehicleUrl": vehicle_url,
            })

            # Navigate to VDP and capture screenshot
            try:
                vdp_capture = await bridge.send_with_retry({
                    "type": "audit_page",
                    "url": vehicle_url,
                })
            except HealingError as e:
                logger.warning(f"VDP navigation failed: {e}")
                return

            if vdp_capture.get("error"):
                logger.warning(f"VDP capture error: {vdp_capture['error']}")
                return

            vdp_screenshot_b64 = vdp_capture.get("screenshot_base64")
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
        screenshot_b64 = capture.get("screenshot_base64")

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
