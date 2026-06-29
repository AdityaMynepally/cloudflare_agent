/**
 * Content Script - Injected on demand by service worker to capture page data.
 * Collects DOM, metadata, links, SEO data, performance timing, and optionally runs axe-core.
 */
(function () {
  'use strict';

  // ---- SEO / Meta Extraction ----

  function getMetaContent(name) {
    const el =
      document.querySelector(`meta[name="${name}"]`) ||
      document.querySelector(`meta[property="${name}"]`);
    return el ? el.getAttribute('content') || '' : '';
  }

  function getSEOData() {
    // Heading hierarchy
    const headings = {};
    for (let i = 1; i <= 6; i++) {
      const els = document.querySelectorAll(`h${i}`);
      headings[`h${i}`] = {
        count: els.length,
        texts: Array.from(els).slice(0, 5).map(el => el.textContent.trim().substring(0, 120))
      };
    }

    // Canonical
    const canonicalEl = document.querySelector('link[rel="canonical"]');
    const canonical = canonicalEl ? canonicalEl.getAttribute('href') : null;

    // Structured data (JSON-LD)
    const jsonLdScripts = Array.from(document.querySelectorAll('script[type="application/ld+json"]'));
    const structuredData = jsonLdScripts.map(script => {
      try { return JSON.parse(script.textContent); }
      catch { return null; }
    }).filter(Boolean);

    // Viewport
    const viewport = getMetaContent('viewport');

    // Robots
    const robots = getMetaContent('robots');

    return {
      title: document.title || '',
      titleLength: (document.title || '').length,
      metaDescription: getMetaContent('description'),
      metaDescriptionLength: getMetaContent('description').length,
      canonical,
      headings,
      ogTitle: getMetaContent('og:title'),
      ogDescription: getMetaContent('og:description'),
      ogImage: getMetaContent('og:image'),
      ogType: getMetaContent('og:type'),
      twitterCard: getMetaContent('twitter:card'),
      viewport,
      robots,
      structuredDataCount: structuredData.length,
      structuredData: structuredData.slice(0, 3),
      lang: document.documentElement.lang || null,
      charset: document.characterSet || null,
    };
  }

  // ---- Performance Timing ----

  function getPerformanceData() {
    const perf = {};

    // Navigation timing
    try {
      const nav = performance.getEntriesByType('navigation')[0];
      if (nav) {
        perf.ttfb = Math.round(nav.responseStart - nav.requestStart);
        perf.domContentLoaded = Math.round(nav.domContentLoadedEventEnd - nav.startTime);
        perf.fullLoad = Math.round(nav.loadEventEnd - nav.startTime);
        perf.domInteractive = Math.round(nav.domInteractive - nav.startTime);
        perf.transferSize = nav.transferSize || 0;
      }
    } catch {}

    // Resource stats
    try {
      const resources = performance.getEntriesByType('resource');
      const byType = { image: [], script: [], stylesheet: [], font: [], other: [] };

      for (const r of resources) {
        const type = r.initiatorType;
        const bucket =
          type === 'img' ? 'image' :
          type === 'script' ? 'script' :
          type === 'link' || type === 'css' ? 'stylesheet' :
          type === 'font' ? 'font' : 'other';

        byType[bucket].push({
          url: r.name,
          name: r.name.split('?')[0].split('/').pop() || r.name.substring(0, 80),
          size: r.transferSize || 0,
          duration: Math.round(r.duration),
        });
      }

      perf.resourceCounts = {};
      perf.resourceSizes = {};
      let totalSize = 0;
      for (const [type, entries] of Object.entries(byType)) {
        perf.resourceCounts[type] = entries.length;
        const typeSize = entries.reduce((sum, e) => sum + e.size, 0);
        perf.resourceSizes[type] = typeSize;
        totalSize += typeSize;
      }
      perf.totalResources = resources.length;
      perf.totalPageWeight = totalSize;

      // Flag large images (>500KB)
      perf.largeImages = byType.image
        .filter(img => img.size > 500 * 1024)
        .map(img => ({ name: img.name, size: img.size }));

      // Full image resource data for image audit
      perf.imageResources = byType.image
        .filter(img => img.url)
        .map(img => ({ url: img.url, transferSize: img.size }));
    } catch {}

    // Render-blocking scripts in <head>
    try {
      const headScripts = document.querySelectorAll('head script[src]:not([async]):not([defer])');
      perf.renderBlockingScripts = headScripts.length;
    } catch {}

    return perf;
  }

  // ---- Page Metadata ----

  function getPageMetadata() {
    return {
      url: window.location.href,
      title: document.title,
      readyState: document.readyState,
      formCount: document.querySelectorAll('form').length,
      inputCount: document.querySelectorAll('input').length,
      buttonCount: document.querySelectorAll('button, input[type="submit"], input[type="button"]').length,
      contentLength: document.documentElement.outerHTML.length,
      imageCount: document.querySelectorAll('img').length,
      imagesWithoutAlt: document.querySelectorAll('img:not([alt])').length,
      iframeCount: document.querySelectorAll('iframe').length,
    };
  }

  // ---- Links ----

  function getAllLinks() {
    const links = Array.from(document.querySelectorAll('a[href]'));
    return links.map(a => ({
      href: a.href,
      text: (a.innerText || '').trim().substring(0, 100),
      ariaLabel: (a.getAttribute('aria-label') || '').toLowerCase(),
      opens_new_tab: a.getAttribute('target') === '_blank',
    }));
  }

  // ---- Sprint 6: Inventory graphic / banner links (links wrapping images or in banner areas) ----

  function getInventoryGraphicLinks() {
    const BANNER_AREAS = [
      '.banner', '[class*="banner"]', '[class*="promo"]', '[class*="offer"]',
      '.hero', '[class*="hero"]', '.slider', '[class*="slider"]',
      '[class*="cta"]', '.special', '[class*="special"]',
    ];
    const seen = new Set();
    const result = [];

    function addLink(a) {
      const href = a.href || '';
      if (!href || href.startsWith('javascript:') || href.startsWith('tel:') || href.startsWith('mailto:')) return;
      if (seen.has(href)) return;
      seen.add(href);
      result.push({
        href,
        text: (a.getAttribute('aria-label') || a.textContent || '').trim().substring(0, 100),
      });
    }

    // Links that directly wrap images
    for (const a of document.querySelectorAll('a[href]')) {
      if (a.querySelector('img')) addLink(a);
    }

    // Links inside banner/promo/offer areas
    for (const sel of BANNER_AREAS) {
      try {
        for (const container of document.querySelectorAll(sel)) {
          for (const a of container.querySelectorAll('a[href]')) {
            addLink(a);
          }
        }
      } catch (_) {}
    }

    return result.slice(0, 40);
  }

  // ---- Sprint 6: Inventory page data (model years, history reports, filters, type) ----

  function getInventoryData() {
    const data = {
      inventory_type: 'unknown',
      vehicle_years: [],
      history_reports: [],
      vehicle_count: 0,
      filter_controls: [],
    };

    try {
      // Classify page type from URL
      const pathLower = window.location.pathname.toLowerCase();
      const NEW_PATHS  = ['/new-vehicles', '/new-cars', '/new-car', '/new-inventory', '/new-models'];
      const USED_PATHS = ['/used-vehicles', '/used-cars', '/used-car', '/used-inventory', '/pre-owned', '/preowned'];
      const CPO_PATHS  = ['/certified-pre-owned', '/certified', '/cpo'];
      if (CPO_PATHS.some(p => pathLower.includes(p)))       data.inventory_type = 'cpo';
      else if (NEW_PATHS.some(p => pathLower.includes(p)))  data.inventory_type = 'new';
      else if (USED_PATHS.some(p => pathLower.includes(p))) data.inventory_type = 'used';
      else {
        const headingText = (document.querySelector('h1,h2') || {}).textContent || '';
        const ht = headingText.toLowerCase();
        if (ht.includes('new '))                          data.inventory_type = 'new';
        else if (ht.includes('used ') || ht.includes('pre-owned')) data.inventory_type = 'used';
        else if (ht.includes('certified'))                data.inventory_type = 'cpo';
      }

      // Extract model years from vehicle listing titles
      const YEAR_RE = /\b(20\d{2}|19[89]\d)\b/g;
      const TITLE_SELECTORS = [
        '.vehicle-card h2', '.vehicle-card h3', '.vehicle-card h4',
        '.inventory-item h2', '.inventory-item h3',
        '.srp-item h2', '.srp-item h3',
        '.car-card h2', '.car-card h3',
        '[class*="vehicle-title"]', '[class*="listing-title"]', '[class*="car-title"]',
        '[class*="result-title"]', '[data-year]',
      ];
      const seenTitles = new Set();
      for (const sel of TITLE_SELECTORS) {
        try {
          for (const el of document.querySelectorAll(sel)) {
            const text = el.textContent || '';
            if (seenTitles.has(text)) continue;
            seenTitles.add(text);
            YEAR_RE.lastIndex = 0;
            let m;
            while ((m = YEAR_RE.exec(text)) !== null) {
              data.vehicle_years.push(m[0]);
            }
          }
        } catch (_) {}
      }

      // Also check data-year attributes
      for (const el of document.querySelectorAll('[data-year]')) {
        const yr = el.getAttribute('data-year');
        if (yr && /^\d{4}$/.test(yr)) data.vehicle_years.push(yr);
      }

      // Fallback: scan h3/h4 with year patterns (VDP title)
      if (!data.vehicle_years.length) {
        for (const el of document.querySelectorAll('h1,h2,h3,h4')) {
          const text = (el.textContent || '').trim();
          if (text.length > 80) continue;
          const m = text.match(/\b(20\d{2}|19[89]\d)\b/);
          if (m) data.vehicle_years.push(m[0]);
        }
      }

      // Result count
      const COUNT_SELS = [
        '[class*="results-count"]', '[class*="inventory-count"]',
        '[class*="vehicle-count"]', '[class*="total-results"]',
        '[class*="result-count"]',
      ];
      for (const sel of COUNT_SELS) {
        try {
          const el = document.querySelector(sel);
          if (el) {
            const numM = el.textContent.match(/(\d+)/);
            if (numM) { data.vehicle_count = parseInt(numM[0]); break; }
          }
        } catch (_) {}
      }

      // History report links (Carfax, AutoCheck, NMVTIS) — on VDP pages.
      // Matches: external domains, internal redirect paths like /dealer-inspire-inventory/autocheck/,
      // link text, aria-label, and image alt/src inside the anchor.
      const HR_HREF_FRAGMENTS = ['carfax.com', 'autocheck.com', 'nmvtis.gov', '/carfax/', '/autocheck/', '/vehicle-history/', '/history-report/'];
      const HR_TEXT_KEYWORDS  = ['carfax', 'autocheck', 'vehicle history', 'history report', 'accident report', 'nmvtis'];

      function detectHistoryPlatform(href, text, imgAlt, imgSrc) {
        const h = href.toLowerCase(), t = text.toLowerCase(), ia = imgAlt.toLowerCase(), is_ = imgSrc.toLowerCase();
        if (h.includes('carfax')   || t.includes('carfax')     || ia.includes('carfax')    || is_.includes('carfax'))    return 'Carfax';
        if (h.includes('autocheck')|| t.includes('autocheck')  || ia.includes('autocheck') || is_.includes('autocheck')) return 'AutoCheck';
        if (h.includes('nmvtis')   || t.includes('nmvtis')     || ia.includes('nmvtis')    || is_.includes('nmvtis'))    return 'NMVTIS';
        if (t.includes('vehicle history') || t.includes('history report') || t.includes('accident report')) return 'Vehicle History';
        return null;
      }

      const seenHrefs = new Set();
      for (const a of document.querySelectorAll('a[href]')) {
        const href     = a.href || '';
        const hrefL    = href.toLowerCase();
        const text     = (a.textContent || a.getAttribute('aria-label') || '').trim();
        const img      = a.querySelector('img');
        const imgAlt   = img ? (img.getAttribute('alt') || '') : '';
        const imgSrc   = img ? (img.src || '') : '';

        const hrefMatch = HR_HREF_FRAGMENTS.some(p => hrefL.includes(p));
        const textMatch = HR_TEXT_KEYWORDS.some(p => text.toLowerCase().includes(p) || (a.getAttribute('aria-label') || '').toLowerCase().includes(p));
        const imgMatch  = img && ['carfax', 'autocheck', 'nmvtis'].some(p => imgAlt.toLowerCase().includes(p) || imgSrc.toLowerCase().includes(p));

        if ((hrefMatch || textMatch || imgMatch) && !seenHrefs.has(href)) {
          const platform = detectHistoryPlatform(hrefL, text, imgAlt, imgSrc);
          if (platform) {
            seenHrefs.add(href);
            data.history_reports.push({
              platform,
              href,
              text: text.substring(0, 80),
              opens_new_tab: a.getAttribute('target') === '_blank',
            });
          }
        }
      }
      // Fallback: check for embedded widgets (class/img with no parent anchor)
      if (!data.history_reports.length) {
        const widget = document.querySelector(
          '[class*="carfax"], img[src*="carfax"], [class*="autocheck"], img[src*="autocheck"]'
        );
        if (widget) {
          const combined = ((widget.className || '') + (widget.src || '')).toLowerCase();
          data.history_reports.push({
            platform: combined.includes('autocheck') ? 'AutoCheck' : 'Carfax',
            href: null,
            text: 'Widget detected on page (no link)',
            opens_new_tab: false,
          });
        }
      }

      // Filter controls on SRP
      const FILTER_SELS = [
        'select[name*="make" i]', 'select[name*="model" i]', 'select[name*="year" i]',
        'select[id*="make" i]', 'select[id*="model" i]', 'select[id*="year" i]',
        'select[name*="body" i]', 'select[name*="type" i]',
        '[class*="filter"] select', '[class*="facet"] select',
      ];
      const seenFilters = new Set();
      for (const sel of FILTER_SELS) {
        try {
          for (const el of document.querySelectorAll(sel)) {
            const key = el.name || el.id || sel;
            if (seenFilters.has(key)) continue;
            seenFilters.add(key);
            const options = Array.from(el.options).map(o => ({ value: o.value, text: o.textContent.trim() }));
            if (options.length > 1) {
              data.filter_controls.push({
                name: el.name || el.id || 'filter',
                type: 'select',
                option_count: options.length,
                options: options.slice(0, 15),
              });
            }
          }
        } catch (_) {}
      }
    } catch (_) {}

    return data;
  }

  // ---- Images ----

  function getAllImages() {
    const CAROUSEL_ANCESTORS = '.swiper-wrapper,.slick-list,.owl-stage,.owl-carousel,.carousel,[class*="carousel"],[class*="slider"],[class*="swiper"],.hero-slider,.banner-slider';
    return Array.from(document.querySelectorAll('img')).map(img => {
      const srcLower = (img.currentSrc || img.src || '').toLowerCase();
      const altLower = (img.alt || '').toLowerCase();
      const classLower = (img.className || '').toLowerCase();
      const isLogo = srcLower.includes('logo') || altLower.includes('logo') || classLower.includes('logo') ||
                     !!(img.closest('header,#header,.header') && (srcLower.includes('logo') || altLower.includes('logo') || classLower.includes('logo') || img.closest('a[class*="logo"],a[class*="brand"],.navbar-brand')));
      const isInCarousel = !!img.closest(CAROUSEL_ANCESTORS);
      return {
        src: img.currentSrc || img.src || '',
        alt: img.alt || '',
        naturalWidth: img.naturalWidth || 0,
        naturalHeight: img.naturalHeight || 0,
        loading: img.loading || '',
        is_logo: isLogo,
        is_in_carousel: isInCarousel,
      };
    }).filter(img => img.src && !img.src.startsWith('data:') && !img.src.startsWith('blob:'));
  }

  // ---- Sprint 5: Carousel / Slideshow Data ----

  function getCarouselData() {
    const CAROUSEL_CONTAINERS = [
      '.swiper-wrapper', '.slick-list', '.owl-stage', '.owl-carousel',
      '.carousel', '[class*="carousel"]', '[class*="slider"]', '[class*="swiper"]',
      '[data-ride="carousel"]', '[data-slick]', '.hero-slider', '.banner-slider',
      '.homepage-slider', '.home-slider', '#home-slider', '#homepage-slider',
    ];
    const SLIDE_SELECTORS = [
      '.swiper-slide:not(.swiper-slide-duplicate)',
      '.slick-slide:not(.slick-cloned)',
      '.owl-item:not(.cloned)',
      '.carousel-item',
      '[class*="slide-item"]',
      '[class*="banner-item"]',
      '.slide',
    ];

    const seen = new Set();
    const carousels = [];

    for (const contSel of CAROUSEL_CONTAINERS) {
      try {
        for (const container of document.querySelectorAll(contSel)) {
          if (seen.has(container)) continue;
          seen.add(container);

          let slides = [];
          for (const slideSel of SLIDE_SELECTORS) {
            const slideEls = container.querySelectorAll(slideSel);
            if (slideEls.length >= 2) {
              for (const slide of slideEls) {
                const rect = slide.getBoundingClientRect();
                if (rect.width < 10 && rect.height < 10) continue;
                const linkEl = slide.tagName === 'A' ? slide : slide.querySelector('a[href]');
                slides.push({
                  href: linkEl ? linkEl.href : null,
                  link_text: linkEl ? (linkEl.getAttribute('aria-label') || linkEl.textContent || '').trim().substring(0, 200) : null,
                  slide_text: slide.textContent.trim().replace(/\s+/g, ' ').substring(0, 300),
                  width: Math.round(rect.width),
                  height: Math.round(rect.height),
                });
              }
              if (slides.length > 0) break;
            }
          }

          // Fallback: direct children with significant size
          if (slides.length < 2) {
            slides = [];
            for (const child of container.children) {
              const rect = child.getBoundingClientRect();
              if (rect.width < 100 || rect.height < 50) continue;
              const linkEl = child.tagName === 'A' ? child : child.querySelector('a[href]');
              slides.push({
                href: linkEl ? linkEl.href : null,
                link_text: linkEl ? (linkEl.getAttribute('aria-label') || linkEl.textContent || '').trim().substring(0, 200) : null,
                slide_text: child.textContent.trim().replace(/\s+/g, ' ').substring(0, 300),
                width: Math.round(rect.width),
                height: Math.round(rect.height),
              });
            }
          }

          if (slides.length >= 2) {
            carousels.push({ selector: contSel, slides: slides.slice(0, 20) });
          }
        }
      } catch (_) {}
    }
    return carousels;
  }

  // ---- Sprint 5: CTA Buttons / Banners ----

  function getCTALinks() {
    const CTA_SELECTORS = [
      'a.btn', 'a.button', 'a[class*="cta"]', 'a[class*="btn-primary"]',
      'a[class*="btn-cta"]', '.hero a[href]', '.banner a[href]',
      '[class*="hero"] a[href]', '[class*="banner"] a[href]',
      '.cta-section a[href]', '[class*="cta"] a[href]',
      'a[class*="call-to-action"]', '.offer a[href]', '[class*="offer"] a[href]',
    ];

    const seen = new Set();
    const ctas = [];

    for (const sel of CTA_SELECTORS) {
      try {
        for (const el of document.querySelectorAll(sel)) {
          const href = el.href || '';
          if (!href || href.startsWith('javascript:') || href.startsWith('mailto:') || href.startsWith('tel:')) continue;
          if (seen.has(href)) continue;
          seen.add(href);
          ctas.push({
            href,
            text: (el.getAttribute('aria-label') || el.textContent || '').trim().substring(0, 100),
          });
        }
      } catch (_) {}
    }
    return ctas;
  }

  // ---- Sprint 5: Primary Navigation Menu ----

  function getNavMenuData() {
    const NAV_SELECTORS = [
      'nav[role="navigation"]', '[role="navigation"]', 'nav',
      'header nav', '.main-nav', '.primary-nav', '.nav-menu',
      '.navigation', '#navigation', '#main-nav', '.site-nav',
      '.top-nav', '.header-nav', '#mainmenu', '.main-menu',
    ];

    const seenNavs = new Set();
    const hrefCounts = {};
    const allLinks = [];

    for (const sel of NAV_SELECTORS) {
      try {
        for (const nav of document.querySelectorAll(sel)) {
          if (seenNavs.has(nav)) continue;
          seenNavs.add(nav);
          for (const link of nav.querySelectorAll('a[href]')) {
            const href = link.href || '';
            if (!href || href.startsWith('javascript:') || href.startsWith('tel:') || href.startsWith('mailto:')) continue;
            // Skip pure anchor links
            try {
              const u = new URL(href);
              if (u.hash && (u.pathname === '/' || u.pathname === '') && !u.search) continue;
            } catch { continue; }

            const text = (link.getAttribute('aria-label') || link.textContent || '').trim().substring(0, 100);
            hrefCounts[href] = (hrefCounts[href] || 0) + 1;
            if (hrefCounts[href] === 1) {
              allLinks.push({ href, text, location: sel });
            } else {
              const existing = allLinks.find(l => l.href === href);
              if (existing) existing.occurrences = (existing.occurrences || 1) + 1;
            }
          }
        }
      } catch (_) {}
    }
    return allLinks.slice(0, 60);
  }

  // ---- Sprint 5: Header Logo Check ----

  function getHeaderLogoInfo() {
    const result = { has_link: false, href: null, links_to_homepage: false };
    const LOGO_SELECTORS = [
      'header a[class*="logo"]', 'header [class*="logo"] a', 'header .logo a',
      '#header a[class*="logo"]', '.header a[class*="logo"]',
      'a[class*="logo"]', '.navbar-brand', '[class*="navbar-brand"] a',
      '[class*="brand"] a', 'header a[href="/"]', 'a[href="/"] img',
      '.site-logo a', '#logo a', 'header img[class*="logo"]',
    ];

    const origin = window.location.origin;

    for (const sel of LOGO_SELECTORS) {
      try {
        const el = document.querySelector(sel);
        if (!el) continue;
        const anchor = el.tagName === 'A' ? el : (el.closest('a') || el.querySelector('a'));
        if (anchor && anchor.href) {
          result.has_link = true;
          result.href = anchor.href;
          try {
            const u = new URL(result.href);
            const path = u.pathname.replace(/\/$/, '') || '/';
            result.links_to_homepage = u.origin === origin && (path === '/' || path === '');
          } catch {}
          return result;
        }
      } catch (_) {}
    }
    return result;
  }

  // ---- Sprint 5: Social Media Links ----

  function getSocialMediaLinks() {
    const SOCIAL_PATTERNS = [
      { platform: 'Facebook',     patterns: ['facebook.com', 'fb.com'] },
      { platform: 'Instagram',    patterns: ['instagram.com'] },
      { platform: 'X (Twitter)', patterns: ['twitter.com', 'x.com'] },
      { platform: 'YouTube',      patterns: ['youtube.com', 'youtu.be'] },
      { platform: 'LinkedIn',     patterns: ['linkedin.com'] },
      { platform: 'TikTok',       patterns: ['tiktok.com'] },
      { platform: 'Pinterest',    patterns: ['pinterest.com'] },
      { platform: 'Snapchat',     patterns: ['snapchat.com'] },
    ];

    const seen = new Set();
    const links = [];

    for (const a of document.querySelectorAll('a[href]')) {
      const href = a.href || '';
      if (!href || seen.has(href)) continue;
      const hrefLower = href.toLowerCase();
      for (const { platform, patterns } of SOCIAL_PATTERNS) {
        if (patterns.some(p => hrefLower.includes(p))) {
          seen.add(href);
          links.push({
            href,
            platform,
            opens_new_tab: a.getAttribute('target') === '_blank',
            rel: a.getAttribute('rel') || '',
            text: (a.getAttribute('aria-label') || a.textContent || '').trim().substring(0, 80),
          });
          break;
        }
      }
    }
    return links;
  }

  // ---- Sprint 5: Expired Date Scanning ----

  function getExpiredDates() {
    const today = new Date();
    today.setHours(0, 0, 0, 0);

    const MONTH_MAP = {
      jan: 0, january: 0, feb: 1, february: 1, mar: 2, march: 2,
      apr: 3, april: 3, may: 4, jun: 5, june: 5, jul: 6, july: 6,
      aug: 7, august: 7, sep: 8, september: 8, oct: 9, october: 9,
      nov: 10, november: 10, dec: 11, december: 11,
    };

    // Patterns: "January 31, 2025", "Jan 31 2025", "12/31/2025", "12/31/25"
    const NAMED_DATE_RE = /\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(\d{1,2}),?\s+(20\d{2})\b/gi;
    const SLASH_DATE_RE = /\b(\d{1,2})\/(\d{1,2})\/(20?\d{2})\b/g;
    const EXPIRY_CONTEXT_RE = /expir|valid\s+(?:through|until)|through\s+\w|offer\s+ends|sale\s+ends|ends\s+\d|deadline|limited\s+time|hurry/i;

    const expired = [];
    const seen = new Set();

    const selectors = 'p,li,td,div,span,h1,h2,h3,h4,h5,h6,.disclaimer,.legal,.offer,.promotion,[class*="expir"],[class*="offer"],[class*="promo"],[class*="special"]';

    for (const el of document.querySelectorAll(selectors)) {
      // Skip large containers to avoid scanning the whole page body
      if (el.children.length > 5) continue;
      const text = (el.textContent || '').trim().replace(/\s+/g, ' ');
      if (text.length > 400 || text.length < 8) continue;
      if (!EXPIRY_CONTEXT_RE.test(text)) continue;

      const tryAdd = (dateStr, parsedDate) => {
        if (!parsedDate || isNaN(parsedDate.getTime()) || parsedDate >= today) return;
        const key = dateStr + '|' + text.substring(0, 60);
        if (seen.has(key)) return;
        seen.add(key);
        expired.push({ date_str: dateStr, context: text.substring(0, 200) });
      };

      NAMED_DATE_RE.lastIndex = 0;
      let m;
      while ((m = NAMED_DATE_RE.exec(text)) !== null) {
        const monthKey = m[1].substring(0, 3).toLowerCase();
        const month = MONTH_MAP[monthKey];
        if (month !== undefined) {
          tryAdd(m[0], new Date(parseInt(m[3]), month, parseInt(m[2])));
        }
      }

      SLASH_DATE_RE.lastIndex = 0;
      while ((m = SLASH_DATE_RE.exec(text)) !== null) {
        let year = parseInt(m[3]);
        if (year < 100) year += 2000;
        tryAdd(m[0], new Date(year, parseInt(m[1]) - 1, parseInt(m[2])));
      }
    }

    return expired.slice(0, 20);
  }

  // ---- Axe-core Injection & Run ----

  async function runAxeCore() {
    try {
      // Check if axe is already loaded
      if (typeof window.axe === 'undefined') {
        // Inject axe-core from CDN
        await new Promise((resolve, reject) => {
          const script = document.createElement('script');
          script.src = 'https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.10.2/axe.min.js';
          script.onload = resolve;
          script.onerror = () => reject(new Error('Failed to load axe-core'));
          document.head.appendChild(script);
        });
      }

      // Run axe
      const results = await window.axe.run(document, {
        runOnly: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice'],
        resultTypes: ['violations', 'incomplete'],
      });

      return {
        violations: results.violations.map(v => ({
          id: v.id,
          impact: v.impact,
          description: v.description,
          help: v.help,
          helpUrl: v.helpUrl,
          tags: v.tags,
          nodes: v.nodes.slice(0, 5).map(n => ({
            html: n.html.substring(0, 200),
            target: n.target,
            failureSummary: n.failureSummary,
          })),
        })),
        incompleteCount: results.incomplete.length,
        passesCount: results.passes ? results.passes.length : 0,
        violationCount: results.violations.length,
        timestamp: results.timestamp,
      };
    } catch (err) {
      return { error: err.message, violations: [], violationCount: 0 };
    }
  }

  // ---- Sprint 4: Business Info (address + hours for GBP comparison) ----

  function getBusinessInfo() {
    const info = { name: null, address: null, hours: {}, raw_hours_text: [] };

    const DAY_ABBR = { Mo:'Monday', Tu:'Tuesday', We:'Wednesday', Th:'Thursday', Fr:'Friday', Sa:'Saturday', Su:'Sunday' };
    const DAY_FULL = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];

    // 1. Schema.org JSON-LD — most reliable source
    for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
      try {
        const items = [].concat(JSON.parse(script.textContent));
        for (const item of items) {
          if (!item || typeof item !== 'object') continue;
          const type = String(item['@type'] || '').toLowerCase();
          if (!type.includes('localbusiness') && !type.includes('autodealer') &&
              !type.includes('dealer') && !type.includes('store')) continue;

          if (item.name && !info.name)
            info.name = String(item.name).trim();

          if (item.address && !info.address) {
            const a = item.address;
            info.address = typeof a === 'string' ? a.trim()
              : [a.streetAddress, a.addressLocality, a.addressRegion, a.postalCode]
                  .filter(Boolean).join(', ');
          }

          // openingHoursSpecification → {Monday: "9:00 AM-5:00 PM", ...}
          if (!Object.keys(info.hours).length && item.openingHoursSpecification) {
            for (const spec of [].concat(item.openingHoursSpecification)) {
              for (const raw of [].concat(spec.dayOfWeek || [])) {
                const day = raw.replace(/https?:\/\/schema\.org\//i, '');
                if (day) info.hours[day] = `${spec.opens || ''}-${spec.closes || ''}`;
              }
            }
          }

          // openingHours string "Mo-Fr 09:00-17:00"
          if (!Object.keys(info.hours).length && item.openingHours) {
            for (const oh of [].concat(item.openingHours)) {
              const m = /^([A-Z][a-z](?:-[A-Z][a-z])?)\s+(\d{2}:\d{2})-(\d{2}:\d{2})/.exec(oh);
              if (!m) continue;
              const [, range, opens, closes] = m;
              if (range.includes('-')) {
                const [s, e] = range.split('-');
                const keys = Object.keys(DAY_ABBR);
                const si = keys.indexOf(s), ei = keys.indexOf(e);
                for (let i = si; i <= ei && i >= 0; i++)
                  info.hours[DAY_ABBR[keys[i]]] = `${opens}-${closes}`;
              } else {
                info.hours[DAY_ABBR[range] || range] = `${opens}-${closes}`;
              }
            }
          }
        }
      } catch {}
    }

    // 2. Fallback address: <address> tag or itemprop
    if (!info.address) {
      const el = document.querySelector('address, [itemprop="address"], [class*="address"]:not([class*="form"]):not([class*="input"])');
      if (el) info.address = el.textContent.trim().replace(/\s+/g, ' ').substring(0, 250);
    }

    // 3. Fallback name: og:site_name or page title
    if (!info.name) {
      const og = document.querySelector('meta[property="og:site_name"]');
      info.name = og
        ? og.getAttribute('content')
        : (document.title || '').split(/[|\-–]/)[0].trim().substring(0, 80);
    }

    // 4. Fallback hours: DOM scan for day-name + time patterns
    if (!Object.keys(info.hours).length) {
      const HOURS_RE = /(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s*[-–to]+\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)/i;

      // Map abbreviated names (Mon/Tue/…) and full names to canonical full names
      const DAY_ABBR_MAP = {
        mon:'Monday', tue:'Tuesday', wed:'Wednesday', thu:'Thursday',
        fri:'Friday', sat:'Saturday', sun:'Sunday',
      };
      const ALL_DAYS = [...DAY_FULL, ...Object.keys(DAY_ABBR_MAP)];

      // Expand a day-range like "Mon – Fri" or "Monday - Friday" into individual days
      function expandDayRange(startDay, endDay) {
        const expanded = [];
        const si = DAY_FULL.indexOf(startDay), ei = DAY_FULL.indexOf(endDay);
        if (si !== -1 && ei !== -1 && si <= ei) {
          for (let i = si; i <= ei; i++) expanded.push(DAY_FULL[i]);
        }
        return expanded.length ? expanded : [startDay];
      }

      function resolveDay(raw) {
        const lower = raw.toLowerCase();
        return DAY_ABBR_MAP[lower] ||
               DAY_FULL.find(d => d.toLowerCase() === lower) ||
               null;
      }

      // Helper: find time in element text; if not found, check parent (handles separate td cells)
      function findTime(el, elText) {
        let hm = HOURS_RE.exec(elText);
        if (hm) return hm;
        // Check parent's full text (e.g. <tr> containing a day cell + time cell)
        const parentText = el.parentElement
          ? (el.parentElement.textContent || '').trim().replace(/\s+/g, ' ')
          : '';
        if (parentText.length <= 200) hm = HOURS_RE.exec(parentText);
        return hm || null;
      }

      function isClosed(el, elText) {
        if (/closed/i.test(elText)) return true;
        const parentText = el.parentElement
          ? (el.parentElement.textContent || '').trim().replace(/\s+/g, ' ')
          : '';
        return /closed/i.test(parentText);
      }

      // Include tr so table-row text is scanned even when day/time are in separate cells
      for (const el of document.querySelectorAll('tr, td, li, div, p, span, dt, dd, th')) {
        const text = (el.textContent || '').trim().replace(/\s+/g, ' ');
        if (text.length > 200) continue;

        // Try to match a day range: "Mon – Fri" / "Monday - Friday" / "Mon-Fri"
        const RANGE_RE = /^(Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|Thu(?:rsday)?|Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?)\s*[-–]\s*(Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|Thu(?:rsday)?|Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?)\b/i;
        const rangeMatch = RANGE_RE.exec(text);
        if (rangeMatch) {
          const startDay = resolveDay(rangeMatch[1]);
          const endDay   = resolveDay(rangeMatch[2]);
          if (startDay && endDay) {
            const days = expandDayRange(startDay, endDay);
            const hm = findTime(el, text);
            const closed = !hm && isClosed(el, text);
            for (const day of days) {
              if (info.hours[day]) continue;
              if (hm) {
                info.hours[day] = `${hm[1].trim()}-${hm[2].trim()}`;
                info.raw_hours_text.push(text.substring(0, 100));
              } else if (closed) {
                info.hours[day] = 'Closed';
              }
            }
            continue;
          }
        }

        // Single day: "Monday", "Mon", "Monday:", "Mon:"
        const SINGLE_DAY_RE = /^(Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|Thu(?:rsday)?|Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?)[\s:]/i;
        const singleMatch = SINGLE_DAY_RE.exec(text);
        if (!singleMatch) continue;
        const dayFound = resolveDay(singleMatch[1]);
        if (!dayFound || info.hours[dayFound]) continue;
        const hm = findTime(el, text);
        if (hm) {
          info.hours[dayFound] = `${hm[1].trim()}-${hm[2].trim()}`;
          info.raw_hours_text.push(text.substring(0, 100));
        } else if (isClosed(el, text)) {
          info.hours[dayFound] = 'Closed';
        }
      }
    }

    return info;
  }

  // ---- Sprint 3: Phone Number Collection ----

  function getPhoneNumbers() {
    const phones = [];
    const seen = new Set(); // keyed on normalized 10-digit number

    function normalize(raw) {
      const digits = raw.replace(/\D/g, '');
      return digits.length >= 10 ? digits.slice(-10) : digits;
    }

    // Reject placeholder/fake numbers: all same digit (9999999999, 0000000000, etc.)
    // or known dummy values like 1234567890
    function isValidPhone(norm) {
      if (!norm || norm.length < 10) return false;
      if (/^(\d)\1{9}$/.test(norm)) return false;  // all same digit
      if (norm === '1234567890') return false;
      return true;
    }

    // Determine department from surrounding context text
    function getDepartment(contextText) {
      const DEPT_MAP = [
        { dept: 'Sales',   kw: ['sales', 'new car', 'used car', 'new vehicle', 'used vehicle', 'buy a car', 'shop vehicles'] },
        { dept: 'Service', kw: ['service', 'repair', 'maintenance', 'body shop', 'collision', 'schedule service'] },
        { dept: 'Parts',   kw: ['parts', 'accessories', 'parts dept', 'order parts'] },
        { dept: 'Finance', kw: ['finance', 'financing', 'credit', 'loan', 'payment'] },
      ];
      const t = contextText.toLowerCase();
      for (const { dept, kw } of DEPT_MAP) {
        if (kw.some(k => t.includes(k))) return dept;
      }
      return 'Main';
    }

    // Walk up the DOM to gather department context
    function getContext(el) {
      if (!el) return '';
      let ctx = ((el.textContent || '') + ' ' +
                 (el.getAttribute('aria-label') || '') + ' ' +
                 (el.getAttribute('title') || '')).substring(0, 200);
      let node = el;
      for (let i = 0; i < 5 && node.parentElement; i++) {
        node = node.parentElement;
        const heading = node.querySelector('h1,h2,h3,h4,h5,h6,label,[class*="title"],[class*="dept"],[class*="heading"]');
        if (heading) ctx += ' ' + heading.textContent.substring(0, 100);
      }
      return ctx;
    }

    // 1. tel: links — most reliable (already formatted, clearly labeled)
    for (const link of document.querySelectorAll('a[href^="tel:"]')) {
      const raw = link.href.replace(/^tel:/, '').replace(/\s/g, '');
      const norm = normalize(raw);
      if (!isValidPhone(norm) || seen.has(norm)) continue;
      seen.add(norm);
      const ctx   = getContext(link);
      const dept  = getDepartment(ctx);
      const display = (link.textContent.trim() || raw).substring(0, 30);
      phones.push({ number: raw, display, department: dept });
    }

    // 2. Text-node regex scan for any numbers not already captured via tel: links
    const PHONE_RE = /(?:\+1[\s.\-]?)?(?:\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4})/g;
    try {
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      let tnode;
      while ((tnode = walker.nextNode())) {
        const text = tnode.textContent || '';
        let m;
        PHONE_RE.lastIndex = 0;
        while ((m = PHONE_RE.exec(text)) !== null) {
          const norm = normalize(m[0]);
          if (!isValidPhone(norm) || seen.has(norm)) continue;
          seen.add(norm);
          const ctx  = getContext(tnode.parentElement);
          const dept = getDepartment(ctx);
          phones.push({ number: m[0].trim(), display: m[0].trim(), department: dept });
        }
      }
    } catch (_) {}

    return phones.slice(0, 40);
  }

  // ---- Sprint 2: Dealership Feature Detection ----

  function getPageFeatures() {
    const features = {
      contact_form:        { detected: false, evidence: '' },
      finance_form:        { detected: false, evidence: '' },
      trade_in_tool:       { detected: false, provider: '', evidence: '' },
      service_scheduling:  { detected: false, provider: '', evidence: '' },
      parts_form:          { detected: false, evidence: '' },
      live_chat:           { detected: false, provider: '', evidence: '' },
    };

    try {
      const scriptSrcs  = Array.from(document.querySelectorAll('script[src]')).map(s => (s.src || '').toLowerCase());
      const iframeSrcs  = Array.from(document.querySelectorAll('iframe')).map(f => (f.src || f.getAttribute('src') || '').toLowerCase());
      const allLinks    = Array.from(document.querySelectorAll('a[href]'));
      const allForms    = Array.from(document.querySelectorAll('form'));

      // Returns true only if the link points to a real page, not homepage / anchor / empty
      function isRealLink(a) {
        const href = (a.href || '').trim();
        if (!href || href.startsWith('javascript:') || href.startsWith('mailto:') || href.startsWith('tel:')) return false;
        try {
          const u = new URL(href);
          // Reject if path is exactly "/" (or empty) with no meaningful search/hash content
          if ((u.pathname === '/' || u.pathname === '') && !u.search) return false;
          if (u.hash && (u.pathname === '/' || u.pathname === '') && !u.search) return false;
        } catch { return false; }
        return true;
      }

      // ---- 1. Live Chat Detection ----
      const CHAT_PROVIDERS = [
        { name: 'Gubagoo',          patterns: ['gubagoo.com', 'gubagoo.io'] },
        { name: 'LivePerson',       patterns: ['liveperson.net', 'lpsnmedia.net', 'lptag.liveperson'] },
        { name: 'CarNow',           patterns: ['carnow.com'] },
        { name: 'ActivEngage',      patterns: ['activengage.com'] },
        { name: 'Contact At Once',  patterns: ['contactatonce.com', 'caochat'] },
        { name: 'Podium',           patterns: ['podium.com', 'podiumwebwidget'] },
        { name: 'Intercom',         patterns: ['intercom.io', 'intercomcdn.com'] },
        { name: 'Drift',            patterns: ['drift.com', 'js.driftt.com'] },
        { name: 'Zendesk Chat',     patterns: ['zopim.com', 'zendesk.com/embeddable'] },
        { name: 'Tidio',            patterns: ['tidio.com'] },
        { name: 'Dealer.com',       patterns: ['dealer.com/chat', 'dealerinspire'] },
        { name: 'DealerSocket',     patterns: ['dealersocket.com/chat'] },
        { name: 'EDealer',          patterns: ['edealer.ca', 'edealer.com'] },
        { name: 'LiveChat',         patterns: ['livechatinc.com', 'livechat.com/tracking'] },
      ];
      for (const p of CHAT_PROVIDERS) {
        if (p.patterns.some(pat => scriptSrcs.some(s => s.includes(pat)) || iframeSrcs.some(f => f.includes(pat)))) {
          features.live_chat = { detected: true, provider: p.name, evidence: 'script/iframe' };
          break;
        }
      }
      if (!features.live_chat.detected) {
        const CHAT_SELECTORS = [
          '#chat-widget', '#live-chat', '#livechat', '#chat-btn',
          '[id*="livechat"]', '[id*="chat-widget"]', '[id*="chat-button"]',
          '[class*="live-chat"]', '[class*="chat-widget"]', '[class*="chatButton"]',
          '[aria-label*="chat" i]', '[title*="live chat" i]',
        ];
        for (const sel of CHAT_SELECTORS) {
          try {
            const el = document.querySelector(sel);
            if (el) {
              features.live_chat = { detected: true, provider: 'Unknown', evidence: sel };
              break;
            }
          } catch {}
        }
      }

      // ---- 2. Trade-In Tool Detection ----
      const TRADEIN_PROVIDERS = [
        { name: 'KBB Instant Cash Offer', patterns: ['kbb.com', 'kbb.', 'kelleybluebook'] },
        { name: 'Edmunds Appraisal',      patterns: ['edmunds.com', 'emo.edmunds'] },
        { name: 'TradePending',           patterns: ['tradepending.com', 'tradependinginc'] },
        { name: 'AccuTrade',              patterns: ['accutrade.com'] },
        { name: 'vAuto',                  patterns: ['vauto.com', 'v-auto.com'] },
        { name: 'AutoTrader',             patterns: ['autotrader.com/my-wallet'] },
      ];
      for (const p of TRADEIN_PROVIDERS) {
        if (p.patterns.some(pat => scriptSrcs.some(s => s.includes(pat)) || iframeSrcs.some(f => f.includes(pat)))) {
          features.trade_in_tool = { detected: true, provider: p.name, evidence: 'script/iframe' };
          break;
        }
      }
      if (!features.trade_in_tool.detected) {
        const TRADEIN_LINK_KEYWORDS = [
          'trade-in', 'trade in', 'value my trade', 'value your trade', 'value your vehicle',
          'get trade value', 'instant cash offer', 'sell my car', 'sell your car', 'trade your',
        ];
        const TRADEIN_HREF_KEYWORDS = [
          'trade-in', 'value-your-trade', 'value-my-trade', 'value-your-vehicle',
          'instant-cash-offer', 'sell-my-car', 'trade-value',
        ];
        const tradeLink = allLinks.find(a => {
          if (!isRealLink(a)) return false;
          const text = a.textContent.toLowerCase();
          const href = (a.href || '').toLowerCase();
          return TRADEIN_LINK_KEYWORDS.some(k => text.includes(k)) ||
                 TRADEIN_HREF_KEYWORDS.some(k => href.includes(k));
        });
        if (tradeLink) {
          features.trade_in_tool = { detected: true, provider: 'Custom', evidence: tradeLink.textContent.trim().substring(0, 80), feature_url: tradeLink.href };
        }
      }

      // ---- 3. Service Appointment Scheduling Detection ----
      const SERVICE_PROVIDERS = [
        { name: 'xTime',           patterns: ['xtime.com'] },
        { name: 'Dealer-FX',       patterns: ['dealerfx.com', 'dealer-fx.com'] },
        { name: 'CDK Service',     patterns: ['cdkglobal.com', 'scheduleservice'] },
        { name: 'AutoVitals',      patterns: ['autovitals.com'] },
        { name: 'Snap27',          patterns: ['snap27.com'] },
        { name: 'DealerSocket SVC',patterns: ['dealersocket.com/service'] },
        { name: 'Reynolds Service',patterns: ['reyrey.com/service'] },
      ];
      for (const p of SERVICE_PROVIDERS) {
        if (p.patterns.some(pat => scriptSrcs.some(s => s.includes(pat)) || iframeSrcs.some(f => f.includes(pat)))) {
          features.service_scheduling = { detected: true, provider: p.name, evidence: 'script/iframe' };
          break;
        }
      }
      if (!features.service_scheduling.detected) {
        const SVC_LINK_KEYWORDS = [
          'schedule service', 'book service', 'service appointment',
          'schedule an appointment', 'book appointment', 'schedule my service',
        ];
        const SVC_HREF_KEYWORDS = [
          'schedule-service', 'book-service', 'service-appointment',
          'service-appt', 'schedule-appointment',
        ];
        const svcLink = allLinks.find(a => {
          if (!isRealLink(a)) return false;
          const text = a.textContent.toLowerCase();
          const href = (a.href || '').toLowerCase();
          return SVC_LINK_KEYWORDS.some(k => text.includes(k)) ||
                 SVC_HREF_KEYWORDS.some(k => href.includes(k));
        });
        if (svcLink) {
          features.service_scheduling = { detected: true, provider: 'Custom', evidence: svcLink.textContent.trim().substring(0, 80), feature_url: svcLink.href };
        }
      }

      // ---- 4. Finance / Credit Application Form Detection ----
      // Specifically targets "apply for financing" pages — not generic finance info pages
      const FINANCE_LINK_KEYWORDS = [
        'apply for financing', 'apply for credit', 'finance application', 'credit application',
        'financing application', 'get pre-approved', 'pre-approval', 'apply now',
      ];
      const FINANCE_HREF_KEYWORDS = [
        'apply-for-financing', 'apply-for-credit', 'finance-application', 'finance-app',
        'credit-application', 'credit-app', 'apply-now', 'get-pre-approved', 'pre-approval',
      ];
      const financeLink = allLinks.find(a => {
        if (!isRealLink(a)) return false;
        const text = a.textContent.toLowerCase();
        const href = (a.href || '').toLowerCase();
        return FINANCE_LINK_KEYWORDS.some(k => text.includes(k)) ||
               FINANCE_HREF_KEYWORDS.some(k => href.includes(k));
      });
      // Check if current page has a finance/credit form
      const financeForm = allForms.find(f => {
        const txt = f.innerText.toLowerCase();
        return ['credit', 'ssn', 'social security', 'annual income', 'employment', 'co-applicant'].some(k => txt.includes(k));
      });
      if (financeForm || financeLink) {
        const financeUrl = financeLink?.href || (financeForm ? window.location.href : '');
        features.finance_form = {
          detected: true,
          evidence: financeLink ? financeLink.textContent.trim().substring(0, 80) || 'link found'
                                : 'credit/finance form on page',
          feature_url: financeUrl,
        };
      }

      // ---- 5. Contact / General Inquiry Form Detection ----
      const CONTACT_LINK_KEYWORDS = ['contact us', 'get in touch', 'reach us', 'send us a message', 'contact our', 'contact the'];
      const contactLink = allLinks.find(a => {
        if (!isRealLink(a)) return false;
        const text = a.textContent.toLowerCase();
        const href = (a.href || '').toLowerCase();
        return CONTACT_LINK_KEYWORDS.some(k => text.includes(k)) ||
               href.includes('/contact') || href.includes('contact-us');
      });
      // Contact form on current page: ≥2 inputs + "contact"/"message"/"inquiry" in form text
      const contactForm = allForms.find(f => {
        const inputs = f.querySelectorAll('input[type="text"], input[type="email"], input[type="tel"], textarea');
        if (inputs.length < 2) return false;
        const txt = f.innerText.toLowerCase();
        return txt.includes('contact') || txt.includes('message') || txt.includes('inquiry') || txt.includes('get in touch');
      });
      if (contactForm || contactLink) {
        // Prefer the explicit contact link URL; only fall back to current page when no link found
        const contactUrl = contactLink?.href || (contactForm ? window.location.href : '');
        features.contact_form = {
          detected: true,
          evidence: contactLink ? contactLink.textContent.trim().substring(0, 80) || 'link found'
                                : 'contact form on page',
          feature_url: contactUrl,
        };
      }

      // ---- 6. Parts Ordering / Request Form Detection ----
      const PARTS_LINK_KEYWORDS = [
        'order parts', 'parts request', 'parts department', 'parts & accessories', 'parts inquiry',
        'order your parts', 'request parts',
      ];
      const PARTS_HREF_KEYWORDS = [
        'order-parts', 'parts-request', 'parts-order', 'parts-inquiry', 'parts-department',
      ];
      const partsLink = allLinks.find(a => {
        if (!isRealLink(a)) return false;
        const text = a.textContent.toLowerCase();
        const href = (a.href || '').toLowerCase();
        return PARTS_LINK_KEYWORDS.some(k => text.includes(k)) ||
               PARTS_HREF_KEYWORDS.some(k => href.includes(k));
      });
      const partsForm = allForms.find(f => {
        const txt = f.innerText.toLowerCase();
        return txt.includes('part number') || txt.includes('parts request') || txt.includes('order parts');
      });
      if (partsForm || partsLink) {
        features.parts_form = {
          detected: true,
          evidence: partsForm ? 'parts form on page' : (partsLink?.textContent.trim().substring(0, 80) || 'link found'),
          feature_url: partsForm ? window.location.href : (partsLink?.href || ''),
        };
      }

    } catch (err) {
      // Non-fatal — return partial results
    }

    return features;
  }

  // ---- Main: gather all data ----

  async function captureAll() {
    const [axeResults] = await Promise.all([
      runAxeCore(),
    ]);

    // Read any JS errors captured by injected error listener
    const jsErrors = Array.isArray(window.__jsErrors) ? [...window.__jsErrors] : [];

    const captureData = {
      dom_content: document.documentElement.outerHTML,
      metadata: getPageMetadata(),
      links: getAllLinks(),
      images: getAllImages(),
      seo_data: getSEOData(),
      performance_data: getPerformanceData(),
      axe_results: axeResults,
      js_errors: jsErrors,
      page_features: getPageFeatures(),
      phone_numbers: getPhoneNumbers(),
      business_info: getBusinessInfo(),
      // Sprint 5 — homepage & site-wide checks
      carousel_data: getCarouselData(),
      cta_links: getCTALinks(),
      nav_menu_data: getNavMenuData(),
      header_logo_info: getHeaderLogoInfo(),
      social_links: getSocialMediaLinks(),
      page_dates: getExpiredDates(),
      // Sprint 6 — inventory checks
      inventory_graphic_links: getInventoryGraphicLinks(),
      inventory_data: getInventoryData(),
      timestamp: new Date().toISOString()
    };

    // Send data back to service worker
    chrome.runtime.sendMessage({
      type: 'PAGE_CAPTURE',
      data: captureData
    });
  }

  captureAll();
})();
