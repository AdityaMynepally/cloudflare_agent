"""Scoring calculator: weighted scores and grades A-F."""

from ai.agent.state import AuditIssue, IssueCategory, IssueSeverity, PageAuditResult

# Severity deduction weights
SEVERITY_WEIGHTS = {
    IssueSeverity.CRITICAL: 10,
    IssueSeverity.SERIOUS: 5,
    IssueSeverity.MODERATE: 2,
    IssueSeverity.MINOR: 1,
}

# Category weights for overall score
CATEGORY_WEIGHTS = {
    "accessibility": 0.40,
    "seo": 0.25,
    "performance": 0.20,
    "links": 0.10,
    "mobile": 0.05,
}


def calculate_category_score(issues: list[AuditIssue], category: IssueCategory) -> float:
    """Calculate score for a single category (0-100). Start at 100, deduct for issues."""
    cat_issues = [i for i in issues if i.category == category]
    deduction = sum(SEVERITY_WEIGHTS.get(i.severity, 1) for i in cat_issues)
    return max(0.0, min(100.0, 100.0 - deduction))


def calculate_page_scores(page: PageAuditResult) -> dict[str, float]:
    """Calculate per-category scores for a page."""
    scores = {}
    for cat in IssueCategory:
        scores[cat.value] = calculate_category_score(page.issues, cat)

    # Mobile score based on viewport meta presence (from SEO data)
    seo = page.seo_data or {}
    if seo.get("viewport"):
        scores["mobile"] = 100.0
    else:
        scores["mobile"] = 40.0

    return scores


def calculate_page_overall(scores: dict[str, float]) -> float:
    """Calculate weighted overall score for a page."""
    total = 0.0
    for cat, weight in CATEGORY_WEIGHTS.items():
        total += scores.get(cat, 100.0) * weight
    return round(total, 1)


def calculate_site_score(pages: list[PageAuditResult]) -> float:
    """Calculate weighted average site score across all pages."""
    if not pages:
        return 0.0

    page_scores = []
    for page in pages:
        if page.scores:
            page_scores.append(calculate_page_overall(page.scores))

    if not page_scores:
        return 0.0

    return round(sum(page_scores) / len(page_scores), 1)


def score_to_grade(score: float) -> str:
    """Convert numeric score to letter grade."""
    if score >= 90:
        return "A"
    elif score >= 80:
        return "B"
    elif score >= 70:
        return "C"
    elif score >= 60:
        return "D"
    else:
        return "F"
