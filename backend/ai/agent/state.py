"""Data models for website quality audit sessions."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional
from datetime import datetime


class AuditStatus(str, Enum):
    PENDING = "pending"
    DISCOVERING = "discovering"
    AUDITING = "auditing"
    ANALYZING = "analyzing"
    SUMMARIZING = "summarizing"
    COMPLETED = "completed"
    FAILED = "failed"


class IssueSeverity(str, Enum):
    CRITICAL = "critical"
    SERIOUS = "serious"
    MODERATE = "moderate"
    MINOR = "minor"


class IssueCategory(str, Enum):
    ACCESSIBILITY = "accessibility"
    SEO = "seo"
    PERFORMANCE = "performance"
    LINKS = "links"
    MOBILE = "mobile"


class ViewportType(str, Enum):
    DESKTOP = "desktop"
    MOBILE = "mobile"


class CheckStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NA = "na"


class QASection(str, Enum):
    HOMEPAGE = "homepage"
    INVENTORY_PAGES = "inventory_pages"
    GENERAL_CONTENT = "general_content"
    LEAD_FORMS_WIDGETS = "lead_forms_widgets"


@dataclass
class ViewportResult:
    """Result of auditing a single page at a specific viewport."""
    url: str
    viewport: ViewportType
    title: str = ""
    page_type: str = "unknown"
    status_code: Optional[int] = None
    screenshot_path: Optional[str] = None
    screenshot_url: Optional[str] = None
    issues: list = field(default_factory=list)
    scores: dict = field(default_factory=dict)
    axe_results: Optional[dict] = None
    seo_data: Optional[dict] = None
    performance_data: Optional[dict] = None
    meta_tags: Optional[dict] = None
    broken_links: list = field(default_factory=list)
    broken_images: list = field(default_factory=list)
    oversized_images: list = field(default_factory=list)
    js_errors: list = field(default_factory=list)
    load_time_ms: Optional[float] = None
    links: list = field(default_factory=list)
    forms: list = field(default_factory=list)
    buttons: list = field(default_factory=list)
    page_features: Optional[dict] = None
    phone_numbers: list = field(default_factory=list)
    business_info: Optional[dict] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "viewport": self.viewport.value,
            "title": self.title,
            "page_type": self.page_type,
            "screenshot_path": self.screenshot_path,
            "screenshot_url": self.screenshot_url,
            "issues": [
                {
                    "category": i.category.value if hasattr(i, 'category') else i.get("category", ""),
                    "severity": i.severity.value if hasattr(i, 'severity') else i.get("severity", ""),
                    "title": i.title if hasattr(i, 'title') else i.get("title", ""),
                    "description": i.description if hasattr(i, 'description') else i.get("description", ""),
                }
                for i in self.issues
            ],
            "scores": self.scores,
            "broken_links": self.broken_links,
            "error": self.error,
        }


@dataclass
class CheckItem:
    """A single checklist item in a QA section."""
    id: str
    label: str
    has_mobile_column: bool = True
    desktop_status: CheckStatus = CheckStatus.NA
    mobile_status: CheckStatus = CheckStatus.NA
    notes: str = ""

    def to_dict(self) -> dict:
        result = {
            "id": self.id,
            "label": self.label,
            "has_mobile_column": self.has_mobile_column,
            "desktop_status": self.desktop_status.value,
            "notes": self.notes,
        }
        if self.has_mobile_column:
            result["mobile_status"] = self.mobile_status.value
        return result


@dataclass
class QASectionResult:
    """Results for one of the 4 QA sections."""
    section: QASection
    title: str
    checks: list[CheckItem] = field(default_factory=list)
    stream_issues: str = ""
    support_issues: str = ""

    def to_dict(self) -> dict:
        return {
            "section": self.section.value,
            "title": self.title,
            "checks": [c.to_dict() for c in self.checks],
            "stream_issues": self.stream_issues,
            "support_issues": self.support_issues,
        }


@dataclass
class QAAuditCard:
    """The full QA audit card with all 4 sections."""
    website_url: str
    sections: list[QASectionResult] = field(default_factory=list)
    auditor_name: str = "Web Sentinel AI"
    audit_date: str = ""
    slide_count: str = ""

    def to_dict(self) -> dict:
        return {
            "website_url": self.website_url,
            "sections": [s.to_dict() for s in self.sections],
            "auditor_name": self.auditor_name,
            "audit_date": self.audit_date or datetime.now().strftime("%m/%d/%Y"),
            "slide_count": self.slide_count,
        }


@dataclass
class AuditIssue:
    category: IssueCategory
    severity: IssueSeverity
    title: str
    description: str
    wcag_criterion: Optional[str] = None
    element: Optional[str] = None
    recommendation: Optional[str] = None
    help_url: Optional[str] = None


@dataclass
class PageAuditResult:
    url: str
    title: str = ""
    page_type: str = "unknown"
    status_code: Optional[int] = None
    screenshot_path: Optional[str] = None
    screenshot_url: Optional[str] = None
    issues: list[AuditIssue] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)  # category -> 0-100
    axe_results: Optional[dict] = None
    seo_data: Optional[dict] = None
    performance_data: Optional[dict] = None
    meta_tags: Optional[dict] = None
    broken_links: list[dict] = field(default_factory=list)
    llm_summary: str = ""
    recommendations: list[str] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def issue_count(self) -> int:
        return len(self.issues)

    @property
    def critical_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == IssueSeverity.CRITICAL)

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "title": self.title,
            "page_type": self.page_type,
            "screenshot_url": self.screenshot_url,
            "issues": [
                {
                    "category": i.category.value,
                    "severity": i.severity.value,
                    "title": i.title,
                    "description": i.description,
                    "wcag_criterion": i.wcag_criterion,
                    "element": i.element,
                    "recommendation": i.recommendation,
                    "help_url": i.help_url,
                }
                for i in self.issues
            ],
            "scores": self.scores,
            "broken_links": self.broken_links,
            "llm_summary": self.llm_summary,
            "recommendations": self.recommendations,
            "performance_data": self.performance_data,
            "error": self.error,
        }


@dataclass
class AuditSession:
    session_id: str
    chat_session_id: str
    target_url: str
    status: AuditStatus = AuditStatus.PENDING
    discovered_urls: list[str] = field(default_factory=list)
    pages: list[PageAuditResult] = field(default_factory=list)
    desktop_results: list[ViewportResult] = field(default_factory=list)
    mobile_results: list[ViewportResult] = field(default_factory=list)
    qa_card: Optional[QAAuditCard] = None
    overall_score: float = 0.0
    overall_grade: str = ""
    site_summary: str = ""
    recommendations: list[str] = field(default_factory=list)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None
    # Dealership-specific: inventory search results and VDP screenshots
    inventory_screenshot: Optional[dict] = None       # {screenshot_path, url, title}
    vdp_screenshot: Optional[dict] = None             # {screenshot_path, url, title}
    contact_form_screenshot: Optional[dict] = None    # {screenshot_path, url, title, fields_filled}
    # Sprint 1 — aggregated technical audit metrics (deduplicated across all pages)
    broken_links_all: list = field(default_factory=list)
    broken_images_all: list = field(default_factory=list)
    oversized_images_all: list = field(default_factory=list)
    js_errors_all: list = field(default_factory=list)
    page_load_times: list = field(default_factory=list)
    # Sprint 4 — Google Business Profile verification
    business_info_website: Optional[dict] = None   # {name, address, hours} from site
    gbp_data: Optional[dict] = None                # raw GBP Places API result
    address_comparison: Optional[dict] = None      # {match, website, gbp, note}
    hours_discrepancies: list = field(default_factory=list)  # [{day, website, gbp}]
    # Sprint 3 — phone numbers aggregated across all pages (deduplicated)
    # Each entry: {number, display, department, source_page}
    phone_numbers_all: list = field(default_factory=list)
    # Sprint 2 — dealership feature detection (aggregated across all pages)
    # Each entry: {detected: bool, provider?: str, evidence: str, source_page: str}
    dealership_features: dict = field(default_factory=lambda: {
        "contact_form":       {"detected": False, "evidence": "", "source_page": ""},
        "finance_form":       {"detected": False, "evidence": "", "source_page": ""},
        "trade_in_tool":      {"detected": False, "provider": "", "evidence": "", "source_page": ""},
        "service_scheduling": {"detected": False, "provider": "", "evidence": "", "source_page": ""},
        "parts_form":         {"detected": False, "evidence": "", "source_page": ""},
        "live_chat":          {"detected": False, "provider": "", "evidence": "", "source_page": ""},
    })

    def start(self):
        self.started_at = datetime.now().isoformat()
        self.status = AuditStatus.DISCOVERING

    def complete(self):
        self.completed_at = datetime.now().isoformat()
        self.status = AuditStatus.COMPLETED

    def fail(self, error: str):
        self.completed_at = datetime.now().isoformat()
        self.status = AuditStatus.FAILED
        self.error = error

    def add_page(self, page: PageAuditResult):
        self.pages.append(page)

    @property
    def pages_audited(self) -> int:
        return len(self.pages)

    @property
    def total_issues(self) -> int:
        return sum(p.issue_count for p in self.pages)

    def to_dict(self) -> dict:
        result = {
            "session_id": self.session_id,
            "target_url": self.target_url,
            "status": self.status.value,
            "overall_score": self.overall_score,
            "overall_grade": self.overall_grade,
            "site_summary": self.site_summary,
            "recommendations": self.recommendations,
            "pages_audited": self.pages_audited,
            "total_issues": self.total_issues,
            "pages": [p.to_dict() for p in self.pages],
            "desktop_results": [r.to_dict() for r in self.desktop_results],
            "mobile_results": [r.to_dict() for r in self.mobile_results],
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "inventory_screenshot": self.inventory_screenshot,
            "vdp_screenshot": self.vdp_screenshot,
            "contact_form_screenshot": self.contact_form_screenshot,
            "business_info_website": self.business_info_website,
            "gbp_data": self.gbp_data,
            "address_comparison": self.address_comparison,
            "hours_discrepancies": self.hours_discrepancies,
            "phone_numbers_all": self.phone_numbers_all,
            "broken_links_all": self.broken_links_all,
            "broken_images_all": self.broken_images_all,
            "oversized_images_all": self.oversized_images_all,
            "js_errors_all": self.js_errors_all,
            "page_load_times": self.page_load_times,
            "dealership_features": self.dealership_features,
        }
        if self.qa_card:
            result["qa_card"] = self.qa_card.to_dict()
        return result
