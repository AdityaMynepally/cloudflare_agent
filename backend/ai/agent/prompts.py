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

TOP_ACTIONS_PROMPT = """You are a website consultant writing for a car dealership's marketing manager — busy, non-technical. Below are the findings from an automated audit of {domain} ({page_count} pages, overall grade {grade}, {overall_score}/100), already ranked most-severe first.

Findings:
{findings}

Return ONLY a JSON object, no markdown:
{{
  "summary": "Exactly 2 short sentences: the site's overall state, then the single biggest risk.",
  "actions": [
    {{
      "title": "Imperative, specific, under 12 words",
      "impact": "high" | "medium" | "low",
      "estimated_impact": "One sentence on what fixing it changes for shoppers, leads or search/AI visibility",
      "next_steps": ["1-3 concrete steps, each under 15 words, naming the page/vendor/URL when known"]
    }}
  ]
}}

Rules:
- 3 to 5 actions, highest impact first. Merge related findings into one action.
- Use only the findings above. Numbers in estimated_impact must come from the findings — never invent percentages, revenue or traffic figures.
- No generic advice ("improve SEO", "optimize performance") — every action must point at something this audit found."""

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
