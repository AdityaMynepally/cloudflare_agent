"""PowerPoint generation for site audit screenshots."""

import io
import logging
from pathlib import Path
from typing import Optional

from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN

logger = logging.getLogger(__name__)

# Slide dimensions: widescreen 16:9
SLIDE_WIDTH = Inches(13.33)
SLIDE_HEIGHT = Inches(7.5)

# Brand colors
BRAND_BLUE = RGBColor(0x1A, 0x56, 0xDB)
BRAND_DARK = RGBColor(0x11, 0x18, 0x27)
BRAND_ORANGE = RGBColor(0xE6, 0x7E, 0x22)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
LIGHT_GRAY = RGBColor(0xF3, 0xF4, 0xF6)
MID_GRAY = RGBColor(0x6B, 0x72, 0x80)


def _add_title_slide(prs: Presentation, target_url: str, pages_count: int) -> None:
    """Add a branded title slide."""
    slide_layout = prs.slide_layouts[6]  # blank layout
    slide = prs.slides.add_slide(slide_layout)

    # Background rectangle
    bg = slide.shapes.add_shape(
        1,  # MSO_SHAPE_TYPE.RECTANGLE
        0, 0, SLIDE_WIDTH, SLIDE_HEIGHT,
    )
    bg.fill.solid()
    bg.fill.fore_color.rgb = BRAND_DARK
    bg.line.fill.background()

    # Accent bar (left edge)
    accent = slide.shapes.add_shape(
        1, 0, 0, Inches(0.25), SLIDE_HEIGHT,
    )
    accent.fill.solid()
    accent.fill.fore_color.rgb = BRAND_ORANGE
    accent.line.fill.background()

    # Title text
    txb = slide.shapes.add_textbox(Inches(1), Inches(1.8), Inches(11), Inches(1.5))
    tf = txb.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.LEFT
    run = p.add_run()
    run.text = "Site Audit Report"
    run.font.size = Pt(44)
    run.font.bold = True
    run.font.color.rgb = WHITE

    # URL subtitle
    txb2 = slide.shapes.add_textbox(Inches(1), Inches(3.4), Inches(11), Inches(0.8))
    tf2 = txb2.text_frame
    p2 = tf2.paragraphs[0]
    run2 = p2.add_run()
    run2.text = target_url
    run2.font.size = Pt(22)
    run2.font.color.rgb = BRAND_ORANGE

    # Stats line
    txb3 = slide.shapes.add_textbox(Inches(1), Inches(4.4), Inches(11), Inches(0.6))
    tf3 = txb3.text_frame
    p3 = tf3.paragraphs[0]
    run3 = p3.add_run()
    run3.text = f"{pages_count} page{'s' if pages_count != 1 else ''} captured"
    run3.font.size = Pt(16)
    run3.font.color.rgb = LIGHT_GRAY


def _add_section_divider(prs: Presentation, title: str, subtitle: str = "") -> None:
    """Add a section divider slide with a colored header."""
    slide_layout = prs.slide_layouts[6]
    slide = prs.slides.add_slide(slide_layout)

    bg = slide.shapes.add_shape(1, 0, 0, SLIDE_WIDTH, SLIDE_HEIGHT)
    bg.fill.solid()
    bg.fill.fore_color.rgb = BRAND_BLUE
    bg.line.fill.background()

    txb = slide.shapes.add_textbox(Inches(1.5), Inches(2.5), Inches(10), Inches(1.5))
    tf = txb.text_frame
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run = p.add_run()
    run.text = title
    run.font.size = Pt(40)
    run.font.bold = True
    run.font.color.rgb = WHITE

    if subtitle:
        txb2 = slide.shapes.add_textbox(Inches(1.5), Inches(4.2), Inches(10), Inches(0.8))
        tf2 = txb2.text_frame
        p2 = tf2.paragraphs[0]
        p2.alignment = PP_ALIGN.CENTER
        run2 = p2.add_run()
        run2.text = subtitle
        run2.font.size = Pt(20)
        run2.font.color.rgb = RGBColor(0xBF, 0xDB, 0xFF)


def _add_screenshot_slide(
    prs: Presentation,
    screenshot_path: str,
    page_title: str,
    page_url: str,
    tag: str = "",
) -> None:
    """Add a slide with a screenshot, title, URL, and optional tag badge."""
    slide_layout = prs.slide_layouts[6]
    slide = prs.slides.add_slide(slide_layout)

    # Light background
    bg = slide.shapes.add_shape(1, 0, 0, SLIDE_WIDTH, SLIDE_HEIGHT)
    bg.fill.solid()
    bg.fill.fore_color.rgb = LIGHT_GRAY
    bg.line.fill.background()

    # Header bar
    header = slide.shapes.add_shape(1, 0, 0, SLIDE_WIDTH, Inches(0.9))
    header.fill.solid()
    header.fill.fore_color.rgb = BRAND_DARK
    header.line.fill.background()

    # Page title in header
    txb = slide.shapes.add_textbox(Inches(0.2), Inches(0.05), Inches(9), Inches(0.8))
    tf = txb.text_frame
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.LEFT
    run = p.add_run()
    run.text = page_title or "Page"
    run.font.size = Pt(18)
    run.font.bold = True
    run.font.color.rgb = WHITE

    # Tag badge (e.g. "VDP", "Inventory", "Homepage")
    if tag:
        badge_x = SLIDE_WIDTH - Inches(2.2)
        badge = slide.shapes.add_shape(1, badge_x, Inches(0.15), Inches(2.0), Inches(0.6))
        badge.fill.solid()
        badge.fill.fore_color.rgb = BRAND_ORANGE
        badge.line.fill.background()
        txb_tag = slide.shapes.add_textbox(badge_x, Inches(0.15), Inches(2.0), Inches(0.6))
        tf_tag = txb_tag.text_frame
        p_tag = tf_tag.paragraphs[0]
        p_tag.alignment = PP_ALIGN.CENTER
        run_tag = p_tag.add_run()
        run_tag.text = tag
        run_tag.font.size = Pt(13)
        run_tag.font.bold = True
        run_tag.font.color.rgb = WHITE

    # Screenshot image — fill the remaining slide area
    img_top = Inches(0.9)
    img_height = SLIDE_HEIGHT - img_top - Inches(0.55)
    img_width = SLIDE_WIDTH - Inches(0.2)

    try:
        slide.shapes.add_picture(
            screenshot_path,
            Inches(0.1),
            img_top,
            width=img_width,
            height=img_height,
        )
    except Exception as e:
        logger.warning(f"Could not add screenshot {screenshot_path}: {e}")
        placeholder = slide.shapes.add_textbox(
            Inches(2), Inches(2.5), Inches(9), Inches(2),
        )
        ph_tf = placeholder.text_frame
        ph_p = ph_tf.paragraphs[0]
        ph_p.alignment = PP_ALIGN.CENTER
        ph_run = ph_p.add_run()
        ph_run.text = f"Screenshot unavailable\n{screenshot_path}"
        ph_run.font.size = Pt(14)
        ph_run.font.color.rgb = MID_GRAY

    # Footer with URL
    footer = slide.shapes.add_textbox(
        Inches(0.2), SLIDE_HEIGHT - Inches(0.5), Inches(13), Inches(0.45),
    )
    ft = footer.text_frame
    fp = ft.paragraphs[0]
    fp.alignment = PP_ALIGN.LEFT
    fr = fp.add_run()
    fr.text = page_url
    fr.font.size = Pt(9)
    fr.font.color.rgb = MID_GRAY


def _add_summary_slide(
    prs: Presentation,
    target_url: str,
    overall_score: float,
    overall_grade: str,
    site_summary: str,
    recommendations: list,
) -> None:
    """Add an executive summary slide with score, narrative, and recommendations."""
    slide_layout = prs.slide_layouts[6]
    slide = prs.slides.add_slide(slide_layout)

    # Background
    bg = slide.shapes.add_shape(1, 0, 0, SLIDE_WIDTH, SLIDE_HEIGHT)
    bg.fill.solid()
    bg.fill.fore_color.rgb = RGBColor(0xF9, 0xFA, 0xFB)
    bg.line.fill.background()

    # Header bar
    hdr = slide.shapes.add_shape(1, 0, 0, SLIDE_WIDTH, Inches(0.75))
    hdr.fill.solid()
    hdr.fill.fore_color.rgb = BRAND_DARK
    hdr.line.fill.background()

    txb_hdr = slide.shapes.add_textbox(Inches(0.3), Inches(0.1), Inches(10), Inches(0.55))
    tf = txb_hdr.text_frame
    p = tf.paragraphs[0]
    run = p.add_run()
    run.text = "Executive Summary"
    run.font.size = Pt(20)
    run.font.bold = True
    run.font.color.rgb = WHITE

    # Score badge (top-right)
    grade_label = f"{overall_grade}  {int(overall_score)}/100"
    badge = slide.shapes.add_shape(1, SLIDE_WIDTH - Inches(2.8), Inches(0.08), Inches(2.6), Inches(0.6))
    badge.fill.solid()
    badge.fill.fore_color.rgb = BRAND_ORANGE
    badge.line.fill.background()
    txb_badge = slide.shapes.add_textbox(SLIDE_WIDTH - Inches(2.8), Inches(0.08), Inches(2.6), Inches(0.6))
    tf_b = txb_badge.text_frame
    p_b = tf_b.paragraphs[0]
    p_b.alignment = PP_ALIGN.CENTER
    run_b = p_b.add_run()
    run_b.text = grade_label
    run_b.font.size = Pt(16)
    run_b.font.bold = True
    run_b.font.color.rgb = WHITE

    # URL
    txb_url = slide.shapes.add_textbox(Inches(0.3), Inches(0.85), Inches(12), Inches(0.4))
    tf_url = txb_url.text_frame
    p_url = tf_url.paragraphs[0]
    run_url = p_url.add_run()
    run_url.text = target_url
    run_url.font.size = Pt(11)
    run_url.font.color.rgb = MID_GRAY

    # Site summary text
    if site_summary:
        txb_sum = slide.shapes.add_textbox(Inches(0.3), Inches(1.35), Inches(8.5), Inches(2.4))
        tf_sum = txb_sum.text_frame
        tf_sum.word_wrap = True
        p_sum = tf_sum.paragraphs[0]
        run_sum = p_sum.add_run()
        run_sum.text = site_summary
        run_sum.font.size = Pt(13)
        run_sum.font.color.rgb = BRAND_DARK

    # Recommendations column
    if recommendations:
        txb_rec_hdr = slide.shapes.add_textbox(Inches(9.1), Inches(0.85), Inches(4.0), Inches(0.4))
        tf_rh = txb_rec_hdr.text_frame
        p_rh = tf_rh.paragraphs[0]
        run_rh = p_rh.add_run()
        run_rh.text = "Key Recommendations"
        run_rh.font.size = Pt(12)
        run_rh.font.bold = True
        run_rh.font.color.rgb = BRAND_DARK

        for i, rec in enumerate(recommendations[:6]):
            y = Inches(1.35) + i * Inches(0.88)
            rec_box = slide.shapes.add_shape(1, Inches(9.0), y, Inches(4.1), Inches(0.78))
            rec_box.fill.solid()
            rec_box.fill.fore_color.rgb = RGBColor(0xE8, 0xF0, 0xFF)
            rec_box.line.fill.background()
            txb_rec = slide.shapes.add_textbox(Inches(9.1), y + Emu(50000), Inches(4.0), Inches(0.72))
            tf_rec = txb_rec.text_frame
            tf_rec.word_wrap = True
            p_rec = tf_rec.paragraphs[0]
            run_rec = p_rec.add_run()
            run_rec.text = f"{i+1}. {rec}"
            run_rec.font.size = Pt(9)
            run_rec.font.color.rgb = BRAND_DARK


def _add_bullet_list_slide(
    prs: Presentation,
    title: str,
    items: list[str],
    empty_msg: str = "No issues detected ✓",
    tag_color: Optional[RGBColor] = None,
) -> None:
    """Add a slide with a title and bullet-point list of items."""
    slide_layout = prs.slide_layouts[6]
    slide = prs.slides.add_slide(slide_layout)

    bg = slide.shapes.add_shape(1, 0, 0, SLIDE_WIDTH, SLIDE_HEIGHT)
    bg.fill.solid()
    bg.fill.fore_color.rgb = RGBColor(0xF9, 0xFA, 0xFB)
    bg.line.fill.background()

    # Header bar
    hdr = slide.shapes.add_shape(1, 0, 0, SLIDE_WIDTH, Inches(0.75))
    hdr.fill.solid()
    hdr.fill.fore_color.rgb = BRAND_DARK
    hdr.line.fill.background()

    txb_hdr = slide.shapes.add_textbox(Inches(0.3), Inches(0.1), Inches(10), Inches(0.55))
    tf = txb_hdr.text_frame
    p = tf.paragraphs[0]
    run = p.add_run()
    run.text = title
    run.font.size = Pt(20)
    run.font.bold = True
    run.font.color.rgb = WHITE

    # Count badge
    count = len(items)
    badge_color = (tag_color or BRAND_ORANGE) if count > 0 else RGBColor(0x27, 0xAE, 0x60)
    badge = slide.shapes.add_shape(1, SLIDE_WIDTH - Inches(2.8), Inches(0.08), Inches(2.6), Inches(0.6))
    badge.fill.solid()
    badge.fill.fore_color.rgb = badge_color
    badge.line.fill.background()
    txb_badge = slide.shapes.add_textbox(SLIDE_WIDTH - Inches(2.8), Inches(0.08), Inches(2.6), Inches(0.6))
    tf_b = txb_badge.text_frame
    p_b = tf_b.paragraphs[0]
    p_b.alignment = PP_ALIGN.CENTER
    run_b = p_b.add_run()
    run_b.text = f"{count} found" if count > 0 else "✓ None found"
    run_b.font.size = Pt(14)
    run_b.font.bold = True
    run_b.font.color.rgb = WHITE

    # Content area
    display = items[:18] if items else [empty_msg]
    txb_content = slide.shapes.add_textbox(Inches(0.4), Inches(0.9), Inches(12.5), Inches(6.4))
    tf_c = txb_content.text_frame
    tf_c.word_wrap = True

    for i, item in enumerate(display):
        if i == 0:
            p_c = tf_c.paragraphs[0]
        else:
            p_c = tf_c.add_paragraph()
        p_c.space_before = Pt(2)
        run_c = p_c.add_run()
        run_c.text = item
        run_c.font.size = Pt(10) if items else Pt(14)
        run_c.font.color.rgb = BRAND_DARK if items else RGBColor(0x27, 0xAE, 0x60)


def generate_audit_pptx(
    target_url: str,
    desktop_results: list,
    mobile_results: list,
    vdp_screenshot: Optional[dict] = None,
    contact_form_screenshot: Optional[dict] = None,
    trade_value_screenshot: Optional[dict] = None,
    site_summary: str = "",
    overall_score: float = 0.0,
    overall_grade: str = "",
    recommendations: Optional[list] = None,
    sprint_metrics: Optional[dict] = None,
) -> bytes:
    """Generate a PPTX audit deck from screenshot data and AI summary.

    Args:
        target_url: The audited website URL.
        desktop_results: List of ViewportResult-like dicts (desktop).
        mobile_results: List of ViewportResult-like dicts (mobile).
        vdp_screenshot: Optional VDP screenshot dict.
        contact_form_screenshot: Optional contact form screenshot dict.
        site_summary: AI-generated site summary text.
        overall_score: Overall audit score 0-100.
        overall_grade: Letter grade (A-F).
        recommendations: List of recommendation strings.

    Returns:
        PPTX file contents as bytes.
    """
    prs = Presentation()
    prs.slide_width = SLIDE_WIDTH
    prs.slide_height = SLIDE_HEIGHT

    valid_desktop = [r for r in desktop_results if _get_screenshot_path(r)]
    valid_mobile  = [r for r in mobile_results  if _get_screenshot_path(r)]
    extras = sum([
        1 if vdp_screenshot and vdp_screenshot.get("screenshot_path") else 0,
        1 if contact_form_screenshot and contact_form_screenshot.get("screenshot_path") else 0,
        1 if trade_value_screenshot and trade_value_screenshot.get("screenshot_path") else 0,
    ])
    total = len(valid_desktop) + len(valid_mobile) + extras

    # Title slide
    _add_title_slide(prs, target_url, total)

    # Executive summary slide (if we have summary data)
    if site_summary or overall_score:
        _add_summary_slide(
            prs, target_url, overall_score, overall_grade,
            site_summary, recommendations or [],
        )

    # Desktop screenshots section
    if valid_desktop:
        _add_section_divider(prs, "Desktop View", f"{len(valid_desktop)} pages")
        for result in valid_desktop:
            path = _get_screenshot_path(result)
            if not path:
                continue
            title = result.get("title") or result.get("url", "")
            url   = result.get("url", "")
            _add_screenshot_slide(prs, path, title, url, _page_tag(url, target_url))

    # Mobile screenshots section
    if valid_mobile:
        _add_section_divider(prs, "Mobile View", f"{len(valid_mobile)} pages")
        for result in valid_mobile:
            path = _get_screenshot_path(result)
            if not path:
                continue
            title = result.get("title") or result.get("url", "")
            url   = result.get("url", "")
            _add_screenshot_slide(prs, path, title, url, _page_tag(url, target_url) + " (Mobile)")

    # VDP section
    if vdp_screenshot and vdp_screenshot.get("screenshot_path"):
        _add_section_divider(prs, "Vehicle Detail Page (VDP)", "Starting point for shopper journey")
        _add_screenshot_slide(
            prs,
            vdp_screenshot["screenshot_path"],
            vdp_screenshot.get("title", "Vehicle Detail Page"),
            vdp_screenshot.get("url", ""),
            "VDP",
        )

    # Contact form section
    if contact_form_screenshot and contact_form_screenshot.get("screenshot_path"):
        count = contact_form_screenshot.get("fields_count", 0)
        _add_section_divider(prs, "Contact Form", f"{count} fields filled with test data (not submitted)")
        _add_screenshot_slide(
            prs,
            contact_form_screenshot["screenshot_path"],
            contact_form_screenshot.get("title", "Contact Form"),
            contact_form_screenshot.get("url", ""),
            "Contact",
        )

    # Trade value tool section
    if trade_value_screenshot and trade_value_screenshot.get("screenshot_path"):
        modal_note = "Modal popup detected" if trade_value_screenshot.get("modal_detected") else "Page result"
        _add_section_divider(prs, "Value Your Trade", modal_note)
        _add_screenshot_slide(
            prs,
            trade_value_screenshot["screenshot_path"],
            trade_value_screenshot.get("title", "Value Your Trade"),
            trade_value_screenshot.get("url", ""),
            "Trade",
        )

    # Technical Audit Findings section (Sprint 1 metrics)
    sm = sprint_metrics or {}
    broken_links = sm.get("broken_links_all", [])
    broken_images = sm.get("broken_images_all", [])
    oversized_images = sm.get("oversized_images_all", [])
    js_errors = sm.get("js_errors_all", [])
    page_load_times = sm.get("page_load_times", [])
    lighthouse_score = sm.get("lighthouse_score")

    has_any_metrics = any([
        broken_links, broken_images, oversized_images, js_errors,
        page_load_times, lighthouse_score,
    ])
    if has_any_metrics:
        _add_section_divider(prs, "Technical Audit Findings", "Automated quality checks")

        # Lighthouse performance score slide
        if lighthouse_score:
            m = lighthouse_score.get("mobile", {})
            d = lighthouse_score.get("desktop", {})
            lh_items = [
                f"Mobile:   {m.get('median', '?')}/100 — {str(m.get('band', '?')).upper()}   "
                f"(runs: {m.get('runs', [])})",
                f"Desktop:  {d.get('median', '?')}/100 — {str(d.get('band', '?')).upper()}   "
                f"(runs: {d.get('runs', [])})",
                "",
                "3 runs per viewport, median score, banded against auto-industry norms "
                "(mobile: 0-24 subpar / 25-44 average / 45-100 excellent; "
                "desktop: 0-24 subpar / 25-74 average / 75-100 excellent).",
            ]
            _add_bullet_list_slide(prs, "Performance Score (Lighthouse)", lh_items, "Lighthouse score unavailable")

        # Broken links slide
        link_items = [
            f"[{l.get('status_code', '?')}] {l.get('url', '')[:80]}  ← {l.get('source_page', '')[:40]}"
            for l in broken_links[:18]
        ]
        _add_bullet_list_slide(prs, "Broken Links", link_items, "No broken links detected ✓")

        # Broken images slide
        img_items = [
            f"[{i.get('status_code', '?')}] {i.get('src', '')[:80]}  ← {i.get('source_page', '')[:40]}"
            for i in broken_images[:18]
        ]
        _add_bullet_list_slide(prs, "Broken Images", img_items, "No broken images detected ✓")

        # Oversized images slide
        over_items = [
            f"{i.get('file_size_kb', '?')} KB — {i.get('src', '')[:70]}  ← {i.get('source_page', '')[:40]}"
            for i in oversized_images[:18]
        ]
        _add_bullet_list_slide(prs, "Oversized Images (>500 KB)", over_items, "No oversized images detected ✓")

        # JS errors slide
        err_items = [
            f"{e.get('source_page', '?')[:30]} | {e.get('message', '')[:80]} (line {e.get('line', 0)})"
            for e in js_errors[:18]
        ]
        _add_bullet_list_slide(prs, "JavaScript Errors", err_items, "No JavaScript errors detected ✓")

        # Page load times slide
        desktop_times = [t for t in page_load_times if t.get("viewport") == "desktop"]
        mobile_times  = [t for t in page_load_times if t.get("viewport") == "mobile"]
        avg_desktop = round(sum(t["load_time_ms"] for t in desktop_times) / max(len(desktop_times), 1))
        avg_mobile  = round(sum(t["load_time_ms"] for t in mobile_times)  / max(len(mobile_times),  1))
        time_items = [f"Avg Desktop: {avg_desktop} ms   |   Avg Mobile: {avg_mobile} ms", ""]
        for t in desktop_times[:8]:
            ms = int(t.get("load_time_ms", 0))
            flag = " ⚠" if ms > 3000 else ""
            time_items.append(f"Desktop  {ms:>6} ms{flag}  —  {t.get('title', t.get('url', ''))[:60]}")
        for t in mobile_times[:8]:
            ms = int(t.get("load_time_ms", 0))
            flag = " ⚠" if ms > 4000 else ""
            time_items.append(f"Mobile   {ms:>6} ms{flag}  —  {t.get('title', t.get('url', ''))[:60]}")
        _add_bullet_list_slide(prs, "Page Load Times", time_items, "No load time data available")

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def _get_screenshot_path(result: dict) -> Optional[str]:
    """Extract and validate screenshot path from a result dict."""
    path = result.get("screenshot_path")
    if not path:
        return None
    p = Path(path)
    if p.exists():
        return str(p)
    return None


def _page_tag(url: str, target_url: str) -> str:
    """Derive a short tag label from a page URL."""
    if url == target_url or url.rstrip("/") == target_url.rstrip("/"):
        return "Homepage"
    url_lower = url.lower()
    for label, patterns in [
        ("Inventory", ["/inventory", "/vehicles", "/new-vehicles", "/used-vehicles", "/srp", "/search"]),
        ("VDP", ["/vdp", "/vehicle-details", "/vehicle/", "/cars/", "/listing"]),
        ("About", ["/about"]),
        ("Contact", ["/contact"]),
        ("Blog", ["/blog", "/news"]),
        ("Privacy", ["/privacy", "/terms"]),
    ]:
        if any(p in url_lower for p in patterns):
            return label
    return "Page"
