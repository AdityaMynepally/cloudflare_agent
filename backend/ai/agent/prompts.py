"""Prompts for the website quality audit system."""

PAGE_SUMMARY_PROMPT = """You are a website quality analyst. Given this audit data for {url}, write a 2-3 sentence summary of the page's quality.

Page title: {title}
Page type: {page_type}
Accessibility score: {a11y_score}/100 ({a11y_issues} issues)
SEO score: {seo_score}/100 ({seo_issues} issues)
Performance score: {perf_score}/100
Broken links: {broken_link_count}

Top issues:
{top_issues}

Write a concise, professional summary focused on the most important findings. Do not use markdown."""

SITE_SUMMARY_PROMPT = """You are a website quality analyst. Given audit results for {page_count} pages on {domain}, write an executive summary (3-5 sentences).

Overall score: {overall_score}/100 (Grade: {grade})
Pages audited: {page_count}
Total issues found: {total_issues}

Per-category averages:
- Accessibility: {avg_a11y}/100
- SEO: {avg_seo}/100
- Performance: {avg_perf}/100
- Links: {avg_links}/100

Most common issue types:
{common_issues}

Write a professional executive summary highlighting strengths and key areas for improvement. Do not use markdown."""

RECOMMENDATION_PROMPT = """You are a website quality consultant. Given these issues found across a website audit, generate 5-10 prioritized, actionable recommendations.

Issues by severity:
Critical ({critical_count}): {critical_issues}
Serious ({serious_count}): {serious_issues}
Moderate ({moderate_count}): {moderate_issues}

Category breakdown:
- Accessibility: {a11y_count} issues
- SEO: {seo_count} issues
- Performance: {perf_count} issues
- Links: {link_count} issues

Return a JSON array of strings, each being one recommendation. Prioritize by impact.
Example: ["Add alt text to all images to improve accessibility", "Fix the 5 broken internal links on the About page"]"""

PAGE_CLASSIFICATION_PROMPT = """Classify this web page into one of these types based on its URL and title:
- homepage, about, contact, blog, services, products, faq, privacy, careers, portfolio, login, other

URL: {url}
Title: {title}

Return just the page type as a single word."""

SCREENSHOT_ANALYSIS_PROMPT = """Analyze this website screenshot for visual quality issues:
1. Is the page loaded correctly? (no broken layout, missing images, error pages)
2. Is the design responsive/mobile-friendly based on the layout?
3. Are there any obvious UI issues? (overlapping elements, unreadable text, broken images)
4. Is the visual hierarchy clear? (headings, spacing, contrast)

Return a brief JSON:
{{"loaded_correctly": true/false, "mobile_friendly": true/false, "ui_issues": ["list of issues"], "visual_quality": "good/acceptable/poor"}}"""
