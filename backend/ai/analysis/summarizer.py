"""LLM-powered summarization for audit results."""

import logging
from typing import Optional

from ai.agent.prompts import PAGE_SUMMARY_PROMPT, SITE_SUMMARY_PROMPT, RECOMMENDATION_PROMPT
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


async def summarize_site(session: AuditSession, llm: LLMProvider) -> str:
    """Generate an executive summary for the entire audit."""
    # Calculate averages
    def avg_score(cat: str) -> int:
        scores = [p.scores.get(cat, 0) for p in session.pages]
        return round(sum(scores) / len(scores)) if scores else 0

    # Count most common issue types
    issue_counts: dict[str, int] = {}
    for page in session.pages:
        for issue in page.issues:
            key = issue.title
            issue_counts[key] = issue_counts.get(key, 0) + 1

    common = sorted(issue_counts.items(), key=lambda x: -x[1])[:5]
    common_str = "\n".join(f"- {title} (found {count} times)" for title, count in common) or "None"

    prompt = SITE_SUMMARY_PROMPT.format(
        page_count=len(session.pages),
        domain=session.target_url,
        overall_score=round(session.overall_score),
        grade=session.overall_grade,
        total_issues=session.total_issues,
        avg_a11y=avg_score("accessibility"),
        avg_seo=avg_score("seo"),
        avg_perf=avg_score("performance"),
        avg_links=avg_score("links"),
        common_issues=common_str,
    )

    try:
        response = await llm.chat([LLMMessage.user(prompt)], temperature=0.3, max_tokens=500)
        return response.content.strip()
    except Exception as e:
        logger.error(f"Site summary failed: {e}")
        return ""


async def generate_recommendations(session: AuditSession, llm: LLMProvider) -> list[str]:
    """Generate prioritized recommendations from all issues."""
    all_issues = [i for p in session.pages for i in p.issues]

    def issues_by_severity(sev: IssueSeverity) -> str:
        items = [i.title for i in all_issues if i.severity == sev][:5]
        return ", ".join(items) or "None"

    def count_by_category(cat: IssueCategory) -> int:
        return sum(1 for i in all_issues if i.category == cat)

    prompt = RECOMMENDATION_PROMPT.format(
        critical_count=sum(1 for i in all_issues if i.severity == IssueSeverity.CRITICAL),
        critical_issues=issues_by_severity(IssueSeverity.CRITICAL),
        serious_count=sum(1 for i in all_issues if i.severity == IssueSeverity.SERIOUS),
        serious_issues=issues_by_severity(IssueSeverity.SERIOUS),
        moderate_count=sum(1 for i in all_issues if i.severity == IssueSeverity.MODERATE),
        moderate_issues=issues_by_severity(IssueSeverity.MODERATE),
        a11y_count=count_by_category(IssueCategory.ACCESSIBILITY),
        seo_count=count_by_category(IssueCategory.SEO),
        perf_count=count_by_category(IssueCategory.PERFORMANCE),
        link_count=count_by_category(IssueCategory.LINKS),
    )

    try:
        response = await llm.chat([LLMMessage.user(prompt)], temperature=0.3, max_tokens=800)
        parsed = parse_llm_json(response.content, [])
        if isinstance(parsed, list):
            return [str(r) for r in parsed[:10]]
        return []
    except Exception as e:
        logger.error(f"Recommendations failed: {e}")
        return []
