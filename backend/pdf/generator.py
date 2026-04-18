"""PDF generation for QA audit cards using fpdf2 (pure Python, zero system deps)."""

import logging
from fpdf import FPDF

logger = logging.getLogger(__name__)

# Color constants
ORANGE = (230, 126, 34)
DARK_BLUE = (44, 62, 80)
WHITE = (255, 255, 255)
LIGHT_GRAY = (241, 243, 245)
MID_GRAY = (189, 195, 199)
GREEN = (39, 174, 96)
RED = (231, 76, 60)
GRAY_NA = (224, 224, 224)
BLUE = (41, 128, 185)
PURPLE = (142, 68, 173)
TEXT_DARK = (26, 26, 26)
TEXT_GRAY = (73, 80, 87)

SECTION_COLORS = [ORANGE, BLUE, GREEN, PURPLE]


class AuditCardPDF(FPDF):
    def __init__(self):
        super().__init__(orientation="L", unit="mm", format="A3")
        self.set_auto_page_break(auto=False)

    def _draw_status_circle(self, x, y, status):
        if status == "pass":
            self.set_fill_color(*GREEN)
        elif status == "fail":
            self.set_fill_color(*RED)
        else:
            self.set_fill_color(*GRAY_NA)
        self.ellipse(x, y, 3.5, 3.5, style="F")


def generate_audit_pdf(qa_card: dict) -> bytes:
    """Generate a PDF from a QA audit card dict."""
    pdf = AuditCardPDF()
    pdf.add_page()
    page_w = pdf.w - 10  # 5mm margin each side

    x_start = 5
    y = 5

    # ---- Header Bar ----
    pdf.set_fill_color(*ORANGE)
    pdf.rect(x_start, y, page_w, 14, style="F")
    pdf.set_xy(x_start, y + 2)
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(*WHITE)
    pdf.cell(page_w, 10, "WEB SENTINEL AI - QA AUDIT", align="C")
    y += 16

    # ---- URL Bar ----
    pdf.set_fill_color(*DARK_BLUE)
    pdf.rect(x_start, y, page_w, 8, style="F")
    pdf.set_xy(x_start + 5, y + 1)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*WHITE)
    pdf.cell(20, 6, "WEBSITE:", align="L")
    pdf.set_font("Courier", "", 9)
    pdf.cell(page_w - 30, 6, qa_card.get("website_url", ""), align="L")
    y += 10

    # ---- Legend ----
    pdf.set_fill_color(*LIGHT_GRAY)
    pdf.rect(x_start, y, page_w, 6, style="F")
    pdf.set_font("Helvetica", "", 7)
    pdf.set_text_color(*TEXT_DARK)

    lx = x_start + 5
    ly = y + 1.5

    pdf.set_fill_color(*GREEN)
    pdf.ellipse(lx, ly, 3, 3, style="F")
    pdf.set_xy(lx + 4, ly - 0.5)
    pdf.cell(30, 4, "Works as Expected")
    lx += 38

    pdf.set_fill_color(*RED)
    pdf.ellipse(lx, ly, 3, 3, style="F")
    pdf.set_xy(lx + 4, ly - 0.5)
    pdf.cell(22, 4, "Issues Found")
    lx += 30

    pdf.set_fill_color(*GRAY_NA)
    pdf.ellipse(lx, ly, 3, 3, style="F")
    pdf.set_xy(lx + 4, ly - 0.5)
    pdf.cell(20, 4, "N/A")

    y += 8

    # ---- Sections (2-column grid) ----
    sections = qa_card.get("sections", [])
    col_w = (page_w - 4) / 2  # 2mm gap between columns

    for i, section in enumerate(sections):
        col = i % 2
        if col == 0:
            row_y = y
        x = x_start + col * (col_w + 4)

        color = SECTION_COLORS[i] if i < len(SECTION_COLORS) else ORANGE

        # Section header
        pdf.set_fill_color(*color)
        pdf.rect(x, row_y, col_w, 7, style="F")
        pdf.set_xy(x + 2, row_y + 1)
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*WHITE)
        pdf.cell(col_w - 4, 5, section.get("title", "").upper(), align="L")

        cy = row_y + 8

        # Table header
        label_w = col_w * 0.6
        status_w = col_w * 0.2

        pdf.set_fill_color(*LIGHT_GRAY)
        pdf.rect(x, cy, col_w, 5, style="F")
        pdf.set_font("Helvetica", "B", 6)
        pdf.set_text_color(*TEXT_GRAY)
        pdf.set_xy(x + 2, cy + 0.5)
        pdf.cell(label_w - 2, 4, "CHECK ITEM", align="L")
        pdf.set_xy(x + label_w, cy + 0.5)
        pdf.cell(status_w, 4, "DESKTOP", align="C")
        pdf.set_xy(x + label_w + status_w, cy + 0.5)
        pdf.cell(status_w, 4, "MOBILE", align="C")
        cy += 5.5

        # Check rows
        checks = section.get("checks", [])
        pdf.set_font("Helvetica", "", 7)
        pdf.set_text_color(*TEXT_DARK)

        for ci, check in enumerate(checks):
            # Alternating row background
            if ci % 2 == 0:
                pdf.set_fill_color(250, 251, 252)
                pdf.rect(x, cy, col_w, 5, style="F")

            pdf.set_xy(x + 2, cy + 0.5)
            pdf.cell(label_w - 4, 4, check.get("label", ""), align="L")

            # Desktop circle
            d_status = check.get("desktop_status", "na")
            circle_x = x + label_w + (status_w - 3.5) / 2
            pdf._draw_status_circle(circle_x, cy + 0.8, d_status)

            # Mobile circle
            if check.get("has_mobile_column", True) and check.get("mobile_status"):
                m_status = check["mobile_status"]
            else:
                m_status = "na"
            circle_x2 = x + label_w + status_w + (status_w - 3.5) / 2
            pdf._draw_status_circle(circle_x2, cy + 0.8, m_status)

            cy += 5

        cy += 1

        # Issues boxes
        for box_title, box_key in [
            ("STREAM: ISSUES & REQUIRED UPDATES", "stream_issues"),
            ("SUPPORT: ISSUES & REQUIRED UPDATES", "support_issues"),
        ]:
            # Box header
            pdf.set_fill_color(*LIGHT_GRAY)
            pdf.rect(x + 1, cy, col_w - 2, 4.5, style="F")
            pdf.set_draw_color(*MID_GRAY)
            pdf.rect(x + 1, cy, col_w - 2, 4.5, style="D")
            pdf.set_xy(x + 3, cy + 0.5)
            pdf.set_font("Helvetica", "B", 5.5)
            pdf.set_text_color(*TEXT_GRAY)
            pdf.cell(col_w - 6, 3.5, box_title, align="L")
            cy += 4.5

            # Box content
            content = section.get(box_key, "") or "No issues found."
            pdf.rect(x + 1, cy, col_w - 2, 8, style="D")
            pdf.set_xy(x + 3, cy + 0.5)
            pdf.set_font("Helvetica", "", 5.5)
            pdf.set_text_color(*TEXT_GRAY)
            # Truncate long content
            if len(content) > 200:
                content = content[:197] + "..."
            pdf.multi_cell(col_w - 6, 2.5, content, align="L")
            cy += 9

        # Track row height
        if col == 1 or i == len(sections) - 1:
            y = max(y, cy) + 3

    # ---- Footer ----
    footer_y = y
    pdf.set_fill_color(*DARK_BLUE)
    pdf.rect(x_start, footer_y, page_w, 8, style="F")

    pdf.set_font("Helvetica", "B", 7)
    pdf.set_text_color(180, 190, 200)
    pdf.set_xy(x_start + 5, footer_y + 1)
    pdf.cell(30, 6, "AUDIT PERFORMED BY:", align="L")
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*WHITE)
    pdf.cell(60, 6, qa_card.get("auditor_name", "Web Sentinel AI"), align="L")

    pdf.set_font("Helvetica", "B", 7)
    pdf.set_text_color(180, 190, 200)
    pdf.set_xy(x_start + page_w - 60, footer_y + 1)
    pdf.cell(15, 6, "DATE:", align="L")
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*WHITE)
    pdf.cell(40, 6, qa_card.get("audit_date", ""), align="L")

    pdf_bytes = bytes(pdf.output())
    logger.info(f"Generated PDF for {qa_card.get('website_url', 'unknown')}: {len(pdf_bytes)} bytes")
    return pdf_bytes
