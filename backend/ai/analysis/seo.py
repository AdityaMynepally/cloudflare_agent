"""SEO analysis: meta tags, headings, canonical, OG, structured data."""

import logging
from typing import Optional

from ai.agent.state import AuditIssue, IssueCategory, IssueSeverity

logger = logging.getLogger(__name__)


def analyze_seo(seo_data: Optional[dict]) -> list[AuditIssue]:
    """Analyze SEO data and return issues."""
    if not seo_data:
        return [AuditIssue(
            category=IssueCategory.SEO,
            severity=IssueSeverity.SERIOUS,
            title="No SEO data available",
            description="Could not extract SEO metadata from the page.",
            recommendation="Ensure meta tags are present in the HTML head.",
        )]

    issues: list[AuditIssue] = []

    # Title check
    title_len = seo_data.get("titleLength", 0)
    if title_len == 0:
        issues.append(AuditIssue(
            category=IssueCategory.SEO,
            severity=IssueSeverity.CRITICAL,
            title="Missing page title",
            description="The page has no <title> tag.",
            recommendation="Add a descriptive title tag (30-60 characters).",
        ))
    elif title_len < 30:
        issues.append(AuditIssue(
            category=IssueCategory.SEO,
            severity=IssueSeverity.MODERATE,
            title=f"Title too short ({title_len} chars)",
            description="Title tags should be 30-60 characters for optimal SEO.",
            recommendation="Expand the title to include relevant keywords (30-60 chars).",
        ))
    elif title_len > 60:
        issues.append(AuditIssue(
            category=IssueCategory.SEO,
            severity=IssueSeverity.MINOR,
            title=f"Title too long ({title_len} chars)",
            description="Title tags over 60 characters may be truncated in search results.",
            recommendation="Shorten the title to 60 characters or fewer.",
        ))

    # Meta description
    desc_len = seo_data.get("metaDescriptionLength", 0)
    if desc_len == 0:
        issues.append(AuditIssue(
            category=IssueCategory.SEO,
            severity=IssueSeverity.SERIOUS,
            title="Missing meta description",
            description="No meta description found. Search engines may generate their own snippet.",
            recommendation="Add a meta description (120-160 characters).",
        ))
    elif desc_len < 120:
        issues.append(AuditIssue(
            category=IssueCategory.SEO,
            severity=IssueSeverity.MINOR,
            title=f"Meta description too short ({desc_len} chars)",
            description="Meta descriptions under 120 characters may not fully utilize SERP space.",
            recommendation="Expand the meta description to 120-160 characters.",
        ))
    elif desc_len > 160:
        issues.append(AuditIssue(
            category=IssueCategory.SEO,
            severity=IssueSeverity.MINOR,
            title=f"Meta description too long ({desc_len} chars)",
            description="Meta descriptions over 160 characters will be truncated.",
            recommendation="Shorten to 160 characters or fewer.",
        ))

    # H1 check
    headings = seo_data.get("headings", {})
    h1_count = headings.get("h1", {}).get("count", 0)
    if h1_count == 0:
        issues.append(AuditIssue(
            category=IssueCategory.SEO,
            severity=IssueSeverity.SERIOUS,
            title="Missing H1 heading",
            description="No H1 heading found. Every page should have exactly one H1.",
            recommendation="Add a single H1 heading that describes the page content.",
        ))
    elif h1_count > 1:
        issues.append(AuditIssue(
            category=IssueCategory.SEO,
            severity=IssueSeverity.MODERATE,
            title=f"Multiple H1 headings ({h1_count})",
            description="Pages should have exactly one H1 heading.",
            recommendation="Use a single H1 and convert others to H2 or lower.",
        ))

    # Heading hierarchy (check for skipped levels)
    prev_level = 0
    for i in range(1, 7):
        count = headings.get(f"h{i}", {}).get("count", 0)
        if count > 0:
            if prev_level > 0 and i > prev_level + 1:
                issues.append(AuditIssue(
                    category=IssueCategory.SEO,
                    severity=IssueSeverity.MINOR,
                    title=f"Heading hierarchy skips from H{prev_level} to H{i}",
                    description="Skipping heading levels can confuse screen readers and hurt SEO.",
                    recommendation=f"Add H{prev_level + 1} headings between H{prev_level} and H{i}.",
                ))
                break
            prev_level = i

    # Canonical
    if not seo_data.get("canonical"):
        issues.append(AuditIssue(
            category=IssueCategory.SEO,
            severity=IssueSeverity.MODERATE,
            title="Missing canonical URL",
            description="No canonical link tag found. This can cause duplicate content issues.",
            recommendation='Add <link rel="canonical" href="..."> to the page head.',
        ))

    # Open Graph
    if not seo_data.get("ogTitle"):
        issues.append(AuditIssue(
            category=IssueCategory.SEO,
            severity=IssueSeverity.MINOR,
            title="Missing Open Graph title",
            description="No og:title meta tag. Social sharing previews won't display properly.",
            recommendation="Add og:title, og:description, and og:image meta tags.",
        ))

    # Structured data
    if seo_data.get("structuredDataCount", 0) == 0:
        issues.append(AuditIssue(
            category=IssueCategory.SEO,
            severity=IssueSeverity.MINOR,
            title="No structured data (JSON-LD)",
            description="No JSON-LD structured data found. Rich snippets won't appear in search.",
            recommendation="Add JSON-LD structured data appropriate for your content type.",
        ))

    # Language
    if not seo_data.get("lang"):
        issues.append(AuditIssue(
            category=IssueCategory.SEO,
            severity=IssueSeverity.MODERATE,
            title="Missing lang attribute",
            description="The <html> element has no lang attribute.",
            recommendation='Add lang="en" (or appropriate language) to the <html> tag.',
        ))

    # Viewport
    if not seo_data.get("viewport"):
        issues.append(AuditIssue(
            category=IssueCategory.SEO,
            severity=IssueSeverity.SERIOUS,
            title="Missing viewport meta tag",
            description="No viewport meta tag. The page won't render properly on mobile devices.",
            recommendation='Add <meta name="viewport" content="width=device-width, initial-scale=1">.',
        ))

    return issues
