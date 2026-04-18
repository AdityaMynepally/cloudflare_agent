/**
 * Page pattern detection - general website page types.
 */

export const PAGE_PATTERNS = {
  about: ['/about', '/about-us', '/our-story', '/our-team', '/team', '/company'],
  contact: ['/contact', '/contact-us', '/get-in-touch', '/support', '/help'],
  blog: ['/blog', '/articles', '/news', '/posts', '/insights'],
  services: ['/services', '/solutions', '/what-we-do', '/offerings'],
  products: ['/products', '/shop', '/store', '/catalog', '/pricing'],
  faq: ['/faq', '/faqs', '/frequently-asked', '/help-center'],
  privacy: ['/privacy', '/privacy-policy', '/terms', '/terms-of-service', '/legal'],
  careers: ['/careers', '/jobs', '/join-us', '/openings'],
  portfolio: ['/portfolio', '/work', '/projects', '/case-studies', '/gallery'],
  login: ['/login', '/signin', '/sign-in', '/account', '/dashboard'],
  // Dealership-specific inventory patterns
  inventory: [
    '/inventory', '/new-vehicles', '/used-vehicles', '/new-cars',
    '/used-cars', '/certified', '/vehicles', '/search', '/srp',
    '/new/', '/used/', '/trucks', '/suvs', '/cars',
  ],
};

const TEXT_INDICATORS = {
  about: ['about us', 'our story', 'our team', 'who we are'],
  contact: ['contact', 'get in touch', 'reach out'],
  blog: ['blog', 'articles', 'latest news'],
  services: ['services', 'solutions', 'what we do'],
  products: ['products', 'shop', 'pricing'],
  faq: ['faq', 'frequently asked'],
  careers: ['careers', 'jobs', 'join our team'],
};

/**
 * Categorize links into page types, returning one URL per page type.
 */
export function categorizeLinks(links, baseDomain) {
  const detected = {};

  for (const link of links) {
    const href = link.href || '';
    const text = (link.text || '').toLowerCase();
    const aria = (link.ariaLabel || '').toLowerCase();

    if (!href || href.startsWith('#') || href.startsWith('javascript:')) continue;
    if (href.startsWith('tel:') || href.startsWith('mailto:') || href.startsWith('sms:')) continue;

    let parsedHostname;
    try {
      const parsed = new URL(href);
      if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') continue;
      parsedHostname = parsed.hostname;
    } catch {
      continue;
    }

    if (parsedHostname && parsedHostname !== baseDomain) continue;

    const hrefLower = href.toLowerCase();

    for (const [pageType, patterns] of Object.entries(PAGE_PATTERNS)) {
      if (detected[pageType]) continue;

      for (const pattern of patterns) {
        if (hrefLower.includes(pattern)) {
          detected[pageType] = href;
          break;
        }
      }

      if (!detected[pageType] && TEXT_INDICATORS[pageType]) {
        for (const indicator of TEXT_INDICATORS[pageType]) {
          if (text.includes(indicator) || aria.includes(indicator)) {
            detected[pageType] = href;
            break;
          }
        }
      }
    }
  }

  return detected;
}

/**
 * Detect page type from a URL.
 */
export function detectPageType(url) {
  const urlLower = url.toLowerCase();
  for (const [pageType, patterns] of Object.entries(PAGE_PATTERNS)) {
    for (const pattern of patterns) {
      if (urlLower.includes(pattern)) return pageType;
    }
  }
  return 'unknown';
}
