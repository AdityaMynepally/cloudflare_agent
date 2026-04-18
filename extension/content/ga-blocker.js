/**
 * GA Blocker - Content Script (runs at document_start on all pages)
 * Ported from GAComplianceFilter.get_injection_script() in cloudflare_bypass_enhanced.py
 *
 * Layer 1 of dual-layer GA blocking:
 * - Overrides window.ga, window.gtag, dataLayer.push
 * - Blocks dynamic analytics script loading
 */
(function () {
  'use strict';

  // Disable GA tracking flags
  window['ga-disable-UA'] = true;
  window['ga-disable-G'] = true;

  // Override Google Analytics
  window.ga = window.ga || function () {};
  window.ga.l = Date.now();
  window.ga.q = [];

  // Override Global Site Tag
  window.gtag = window.gtag || function () {};

  // Override dataLayer
  window.dataLayer = window.dataLayer || [];
  window.dataLayer.push = function () {
    return arguments.length;
  };

  // Block dynamic script loading for analytics
  const originalCreateElement = document.createElement.bind(document);
  document.createElement = function (tagName) {
    const element = originalCreateElement(tagName);
    if (tagName.toLowerCase() === 'script') {
      const originalSetAttribute = element.setAttribute.bind(element);
      element.setAttribute = function (name, value) {
        if (name === 'src' && value) {
          const blockedDomains = [
            'google-analytics.com',
            'googletagmanager.com',
            'analytics.google.com',
            'doubleclick.net',
            'googlesyndication.com'
          ];
          for (const domain of blockedDomains) {
            if (value.includes(domain)) {
              console.log('[GA Blocker] Blocked script load:', value);
              return;
            }
          }
        }
        return originalSetAttribute(name, value);
      };

      // Also intercept src property assignment
      const descriptor = Object.getOwnPropertyDescriptor(HTMLScriptElement.prototype, 'src');
      if (descriptor && descriptor.set) {
        const originalSrcSetter = descriptor.set;
        Object.defineProperty(element, 'src', {
          set: function (value) {
            if (value) {
              const blockedDomains = [
                'google-analytics.com',
                'googletagmanager.com',
                'analytics.google.com'
              ];
              for (const domain of blockedDomains) {
                if (value.includes(domain)) {
                  console.log('[GA Blocker] Blocked src assignment:', value);
                  return;
                }
              }
            }
            originalSrcSetter.call(this, value);
          },
          get: descriptor.get ? descriptor.get.bind(element) : undefined
        });
      }
    }
    return element;
  };

  console.log('[GA Blocker] Analytics blocking initialized');
})();
