"""Process axe-core results and generate accessibility audit issues."""

import logging
from typing import Optional

from ai.agent.state import AuditIssue, IssueCategory, IssueSeverity

logger = logging.getLogger(__name__)

# Map axe impact levels to our severity
IMPACT_MAP = {
    "critical": IssueSeverity.CRITICAL,
    "serious": IssueSeverity.SERIOUS,
    "moderate": IssueSeverity.MODERATE,
    "minor": IssueSeverity.MINOR,
}


def analyze_accessibility(
    axe_results: Optional[dict],
    metadata: Optional[dict] = None,
) -> list[AuditIssue]:
    """Process axe-core violations and additional checks into AuditIssue objects."""
    issues: list[AuditIssue] = []

    # Process axe-core violations
    if axe_results and not axe_results.get("error"):
        for violation in axe_results.get("violations", []):
            severity = IMPACT_MAP.get(violation.get("impact", "minor"), IssueSeverity.MINOR)

            # Extract WCAG criteria from tags
            wcag = None
            for tag in violation.get("tags", []):
                if tag.startswith("wcag"):
                    wcag = tag
                    break

            # Get first affected element
            element = None
            nodes = violation.get("nodes", [])
            if nodes:
                element = nodes[0].get("html", "")[:150]

            issues.append(AuditIssue(
                category=IssueCategory.ACCESSIBILITY,
                severity=severity,
                title=violation.get("help", violation.get("id", "Unknown")),
                description=violation.get("description", ""),
                wcag_criterion=wcag,
                element=element,
                recommendation=nodes[0].get("failureSummary", "") if nodes else "",
                help_url=violation.get("helpUrl"),
            ))

    # Additional checks from metadata
    if metadata:
        imgs_without_alt = metadata.get("imagesWithoutAlt", 0)
        if imgs_without_alt > 0:
            # Only add if axe didn't already flag it
            axe_ids = {v.get("id") for v in (axe_results or {}).get("violations", [])}
            if "image-alt" not in axe_ids:
                issues.append(AuditIssue(
                    category=IssueCategory.ACCESSIBILITY,
                    severity=IssueSeverity.SERIOUS,
                    title=f"{imgs_without_alt} images missing alt text",
                    description="Images without alt text are inaccessible to screen readers.",
                    wcag_criterion="wcag111",
                    recommendation="Add descriptive alt text to all images.",
                ))

    return issues
