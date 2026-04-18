"""Performance analysis: image sizes, timing, page weight, render-blocking resources."""

import logging
from typing import Optional

from ai.agent.state import AuditIssue, IssueCategory, IssueSeverity

logger = logging.getLogger(__name__)


def analyze_performance(perf_data: Optional[dict]) -> list[AuditIssue]:
    """Analyze performance data and return issues."""
    if not perf_data:
        return []

    issues: list[AuditIssue] = []

    # Large images
    large_images = perf_data.get("largeImages", [])
    if large_images:
        names = ", ".join(img.get("name", "unknown")[:30] for img in large_images[:3])
        issues.append(AuditIssue(
            category=IssueCategory.PERFORMANCE,
            severity=IssueSeverity.SERIOUS,
            title=f"{len(large_images)} images over 500KB",
            description=f"Large images slow down page load. Affected: {names}",
            recommendation="Compress images, use WebP format, and implement lazy loading.",
        ))

    # Page weight
    total_weight = perf_data.get("totalPageWeight", 0)
    if total_weight > 5 * 1024 * 1024:  # 5MB
        mb = round(total_weight / (1024 * 1024), 1)
        issues.append(AuditIssue(
            category=IssueCategory.PERFORMANCE,
            severity=IssueSeverity.SERIOUS,
            title=f"Page weight is {mb}MB",
            description="Total page weight exceeds 5MB, which impacts load time significantly.",
            recommendation="Reduce page weight by compressing assets and removing unused resources.",
        ))
    elif total_weight > 3 * 1024 * 1024:  # 3MB
        mb = round(total_weight / (1024 * 1024), 1)
        issues.append(AuditIssue(
            category=IssueCategory.PERFORMANCE,
            severity=IssueSeverity.MODERATE,
            title=f"Page weight is {mb}MB",
            description="Total page weight exceeds 3MB. Consider optimizing assets.",
            recommendation="Aim for under 3MB total page weight.",
        ))

    # Render-blocking scripts
    blocking = perf_data.get("renderBlockingScripts", 0)
    if blocking > 3:
        issues.append(AuditIssue(
            category=IssueCategory.PERFORMANCE,
            severity=IssueSeverity.SERIOUS,
            title=f"{blocking} render-blocking scripts in <head>",
            description="Synchronous scripts in <head> block page rendering.",
            recommendation="Add async or defer attributes to scripts, or move them to end of <body>.",
        ))
    elif blocking > 0:
        issues.append(AuditIssue(
            category=IssueCategory.PERFORMANCE,
            severity=IssueSeverity.MODERATE,
            title=f"{blocking} render-blocking script(s) in <head>",
            description="Synchronous scripts delay initial rendering.",
            recommendation="Consider adding async or defer to non-critical scripts.",
        ))

    # TTFB
    ttfb = perf_data.get("ttfb", 0)
    if ttfb > 800:
        issues.append(AuditIssue(
            category=IssueCategory.PERFORMANCE,
            severity=IssueSeverity.SERIOUS,
            title=f"Slow server response ({ttfb}ms TTFB)",
            description="Time To First Byte exceeds 800ms, indicating server slowness.",
            recommendation="Optimize server-side processing, enable caching, or use a CDN.",
        ))
    elif ttfb > 400:
        issues.append(AuditIssue(
            category=IssueCategory.PERFORMANCE,
            severity=IssueSeverity.MODERATE,
            title=f"Server response could be faster ({ttfb}ms TTFB)",
            description="TTFB is over 400ms. Good target is under 200ms.",
            recommendation="Consider server-side caching or CDN.",
        ))

    # Full load time
    full_load = perf_data.get("fullLoad", 0)
    if full_load > 6000:
        issues.append(AuditIssue(
            category=IssueCategory.PERFORMANCE,
            severity=IssueSeverity.SERIOUS,
            title=f"Slow page load ({round(full_load/1000, 1)}s)",
            description="Full page load exceeds 6 seconds.",
            recommendation="Optimize critical rendering path, reduce resource count, enable compression.",
        ))
    elif full_load > 3000:
        issues.append(AuditIssue(
            category=IssueCategory.PERFORMANCE,
            severity=IssueSeverity.MODERATE,
            title=f"Page load is {round(full_load/1000, 1)}s",
            description="Page takes over 3 seconds to fully load.",
            recommendation="Aim for under 3 seconds full load time.",
        ))

    # Total resource count
    total_resources = perf_data.get("totalResources", 0)
    if total_resources > 100:
        issues.append(AuditIssue(
            category=IssueCategory.PERFORMANCE,
            severity=IssueSeverity.MODERATE,
            title=f"High resource count ({total_resources} requests)",
            description="Pages with many HTTP requests load more slowly.",
            recommendation="Bundle resources, use sprites, and eliminate unnecessary requests.",
        ))

    return issues
