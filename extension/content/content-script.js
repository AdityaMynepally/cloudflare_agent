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
      ariaLabel: (a.getAttribute('aria-label') || '').toLowerCase()
    }));
  }

  // ---- Images ----

  function getAllImages() {
    return Array.from(document.querySelectorAll('img')).map(img => ({
      src: img.currentSrc || img.src || '',
      alt: img.alt || '',
      naturalWidth: img.naturalWidth || 0,
      naturalHeight: img.naturalHeight || 0,
      loading: img.loading || '',
    })).filter(img => img.src && !img.src.startsWith('data:') && !img.src.startsWith('blob:'));
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
      for (const el of document.querySelectorAll('td, li, div, p, span, dt, dd')) {
        const text = (el.textContent || '').trim().replace(/\s+/g, ' ');
        if (text.length > 100) continue; // skip large containers
        const dayFound = DAY_FULL.find(d =>
          new RegExp(`^${d}|${d}:`, 'i').test(text)
        );
        if (!dayFound || info.hours[dayFound]) continue;
        const hm = HOURS_RE.exec(text);
        if (hm) {
          info.hours[dayFound] = `${hm[1].trim()}-${hm[2].trim()}`;
          info.raw_hours_text.push(text.substring(0, 80));
        } else if (/closed/i.test(text)) {
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
      if (!norm || norm.length < 7 || seen.has(norm)) continue;
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
          if (!norm || norm.length < 10 || seen.has(norm)) continue;
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
        const TRADEIN_LINK_KEYWORDS = ['trade-in', 'trade in', 'value my trade', 'get trade value', 'instant cash offer', 'sell my car', 'trade your'];
        const tradeLink = allLinks.find(a => {
          const text = a.textContent.toLowerCase();
          const href = (a.href || '').toLowerCase();
          return TRADEIN_LINK_KEYWORDS.some(k => text.includes(k) || href.includes(k.replace(/\s/g, '-')));
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
        const SVC_LINK_KEYWORDS = ['schedule service', 'book service', 'service appointment', 'schedule an appointment', 'book appointment'];
        const svcLink = allLinks.find(a => {
          const text = a.textContent.toLowerCase();
          const href = (a.href || '').toLowerCase();
          return SVC_LINK_KEYWORDS.some(k => text.includes(k)) ||
                 href.includes('schedule') || href.includes('service-appt') || href.includes('service-appointment');
        });
        if (svcLink) {
          features.service_scheduling = { detected: true, provider: 'Custom', evidence: svcLink.textContent.trim().substring(0, 80), feature_url: svcLink.href };
        }
      }

      // ---- 4. Finance / Credit Application Form Detection ----
      const FINANCE_LINK_KEYWORDS = ['finance application', 'credit application', 'apply for financing', 'apply for credit', 'financing options', 'get pre-approved', 'pre-approval'];
      const financeLink = allLinks.find(a => {
        const text = a.textContent.toLowerCase();
        const href = (a.href || '').toLowerCase();
        return FINANCE_LINK_KEYWORDS.some(k => text.includes(k)) ||
               href.includes('finance') || href.includes('credit-app') || href.includes('apply');
      });
      // Check if current page has a finance/credit form
      const financeForm = allForms.find(f => {
        const txt = f.innerText.toLowerCase();
        return ['credit', 'ssn', 'social security', 'annual income', 'employment', 'co-applicant'].some(k => txt.includes(k));
      });
      if (financeForm || financeLink) {
        features.finance_form = {
          detected: true,
          evidence: financeForm ? 'credit/finance form on page' : (financeLink?.textContent.trim().substring(0, 80) || 'link found'),
          feature_url: financeForm ? window.location.href : (financeLink?.href || ''),
        };
      }

      // ---- 5. Contact / General Inquiry Form Detection ----
      const CONTACT_LINK_KEYWORDS = ['contact us', 'get in touch', 'reach us', 'send us a message', 'contact our', 'contact the'];
      const contactLink = allLinks.find(a => {
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
        features.contact_form = {
          detected: true,
          evidence: contactForm ? 'contact form on page' : (contactLink?.textContent.trim().substring(0, 80) || 'link found'),
          feature_url: contactForm ? window.location.href : (contactLink?.href || ''),
        };
      }

      // ---- 6. Parts Ordering / Request Form Detection ----
      const PARTS_LINK_KEYWORDS = ['order parts', 'parts request', 'parts department', 'parts & accessories', 'parts inquiry'];
      const partsLink = allLinks.find(a => {
        const text = a.textContent.toLowerCase();
        const href = (a.href || '').toLowerCase();
        return PARTS_LINK_KEYWORDS.some(k => text.includes(k)) ||
               href.includes('/parts') || href.includes('parts-request') || href.includes('order-parts');
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
