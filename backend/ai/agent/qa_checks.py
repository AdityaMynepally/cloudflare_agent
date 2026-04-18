"""QA checklist definitions for the 4-section audit card.

Each section defines its check items with:
- id: unique identifier
- label: display text
- has_mobile_column: whether to show Desktop+Mobile columns or just "Working as Expected?"
"""

from ai.agent.state import CheckItem, CheckStatus, QASection


# ---- HOMEPAGE checks ----

HOMEPAGE_CHECKS = [
    CheckItem(id="hp_template_loads", label="Template/Layout Loads & Displays Properly"),
    CheckItem(id="hp_search_tools", label="Inventory Search Tools Function Properly"),
    CheckItem(id="hp_cta_links", label="All CTA Graphics & Buttons Link to Proper Pages"),
    CheckItem(id="hp_slides_same_size", label="All Slides are the Same Size", has_mobile_column=False),
    CheckItem(id="hp_slides_linked", label="All Slides are Linked to Proper Page", has_mobile_column=False),
    CheckItem(id="hp_floating_widgets", label="Floating Widgets Do Not Interrupt User Experience"),
    CheckItem(id="hp_no_expired", label="No Expired Content", has_mobile_column=False),
    CheckItem(id="hp_slide_count", label="Slide Count", has_mobile_column=False),
]

# ---- INVENTORY PAGES checks ----

INVENTORY_CHECKS = [
    CheckItem(id="inv_vdp_loads", label="New/Used VDP Loads & Displays Properly"),
    CheckItem(id="inv_srp_loads", label="New/Used SRP Loads & Displays Properly"),
    CheckItem(id="inv_cta_buttons", label="All CTA Graphics & Buttons Function as Expected"),
    CheckItem(id="inv_sort_filter", label="Sort & Filter Options Function as Expected"),
    CheckItem(id="inv_history_reports", label="History Reports Link Properly", has_mobile_column=False),
    CheckItem(id="inv_model_years", label="New Inventory Model Years are Current", has_mobile_column=False),
]

# ---- GENERAL CONTENT checks ----

GENERAL_CONTENT_CHECKS = [
    CheckItem(id="gc_header_icons", label="Header Icons are Linked Properly"),
    CheckItem(id="gc_images_optimized", label="Images are Web Optimized", has_mobile_column=False),
    CheckItem(id="gc_phone_numbers", label="All Phone Numbers Ring to Dealership", has_mobile_column=False),
    CheckItem(id="gc_map_location", label="Map on Directions Page Reflects Proper Location", has_mobile_column=False),
    CheckItem(id="gc_nav_links", label="Main Navigation Menu Links All Route to Proper Pages"),
    CheckItem(id="gc_links_new_tab", label="Links Open in New Tab Where Necessary", has_mobile_column=False),
    CheckItem(id="gc_expired_content", label="Interior Pages Swept for Expired Content", has_mobile_column=False),
]

# ---- LEAD FORMS & WIDGETS checks ----

LEAD_FORMS_CHECKS = [
    CheckItem(id="lf_native_forms", label="Native Website Forms"),
    CheckItem(id="lf_finance_app", label="Finance Application"),
    CheckItem(id="lf_trade_in", label="Trade-In Tool"),
    CheckItem(id="lf_service_scheduler", label="Service Scheduler"),
    CheckItem(id="lf_order_parts", label="Order Parts"),
    CheckItem(id="lf_chat_tool", label="Chat Tool"),
    CheckItem(id="lf_no_expired", label="No Expired Content on Lead Conversion Form Pages", has_mobile_column=False),
]


# Section definitions mapping
SECTION_DEFINITIONS = {
    QASection.HOMEPAGE: {
        "title": "Homepage",
        "checks": HOMEPAGE_CHECKS,
    },
    QASection.INVENTORY_PAGES: {
        "title": "Inventory Pages",
        "checks": INVENTORY_CHECKS,
    },
    QASection.GENERAL_CONTENT: {
        "title": "General Content",
        "checks": GENERAL_CONTENT_CHECKS,
    },
    QASection.LEAD_FORMS_WIDGETS: {
        "title": "Lead Forms & Widgets",
        "checks": LEAD_FORMS_CHECKS,
    },
}


def get_fresh_checks(section: QASection) -> list[CheckItem]:
    """Return a fresh copy of check items for a section (with default NA statuses)."""
    definition = SECTION_DEFINITIONS[section]
    return [
        CheckItem(
            id=c.id,
            label=c.label,
            has_mobile_column=c.has_mobile_column,
            desktop_status=CheckStatus.NA,
            mobile_status=CheckStatus.NA,
        )
        for c in definition["checks"]
    ]
