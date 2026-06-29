/**
 * Service Worker - Core orchestration for tab management, capture, and backend communication.
 *
 * Supports:
 * 1. WebSocket commands from backend: navigate, capture, audit_page, discover_links
 * 2. Popup messages: connection checks, reconnect
 */

import { checkHealth } from '../lib/api-client.js';
import { categorizeLinks } from '../lib/page-patterns.js';
import { WSClient } from '../lib/ws-client.js';

const wsClient = new WSClient();

// Generate a stable session ID for this extension instance
function getSessionId() {
  return new Promise((resolve) => {
    chrome.storage.local.get(['wsSessionId'], (result) => {
      if (result.wsSessionId) {
        resolve(result.wsSessionId);
      } else {
        const id = 'ext_' + Date.now().toString(36) + '_' + Math.random().toString(36).substring(2, 8);
        chrome.storage.local.set({ wsSessionId: id }, () => resolve(id));
      }
    });
  });
}

// Auto-connect WebSocket on service worker startup
async function connectWebSocket() {
  try {
    const sessionId = await getSessionId();
    await wsClient.connect(sessionId);
    console.log('[WS] Auto-connected with session:', sessionId);
  } catch (err) {
    console.warn('[WS] Auto-connect failed:', err.message || err);
  }
}

connectWebSocket();

// Keep service worker alive with alarms
chrome.alarms.create('keepalive', { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === 'keepalive') {
    if (!wsClient.isConnected) {
      connectWebSocket();
    }
  }
});

// ---- Message handling from popup ----

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === 'CHECK_CONNECTION') {
    checkHealth().then(async (connected) => {
      if (connected && !wsClient.isConnected) {
        connectWebSocket();
      }
      sendResponse({ connected, wsConnected: wsClient.isConnected });
    });
    return true;
  }

  if (message.type === 'CONNECT_WS') {
    connectWebSocket().then(() => {
      sendResponse({ wsConnected: wsClient.isConnected });
    }).catch(err => {
      sendResponse({ wsConnected: false, error: err.message });
    });
    return true;
  }

  if (message.type === 'GET_WS_STATUS') {
    sendResponse({ wsConnected: wsClient.isConnected, sessionId: wsClient.sessionId });
    return false;
  }

  // Content script sends captured page data
  if (message.type === 'PAGE_CAPTURE') {
    handlePageCapture(message.data, sender);
    return false;
  }
});

// ---- Capture promise management ----
let pendingCapture = null;

function handlePageCapture(data, sender) {
  if (pendingCapture) {
    clearTimeout(pendingCapture.timeoutId);
    pendingCapture.resolve(data);
    pendingCapture = null;
  }
}

function waitForCapture(timeoutMs = 30000) {
  return new Promise((resolve, reject) => {
    const timeoutId = setTimeout(() => {
      pendingCapture = null;
      reject(new Error('Capture timeout'));
    }, timeoutMs);

    pendingCapture = { resolve, reject, timeoutId };
  });
}

// ---- Tab helpers ----

function createTab(url) {
  return new Promise((resolve) => {
    chrome.tabs.create({ url, active: false }, resolve);
  });
}

function waitForTabLoad(tabId) {
  return new Promise((resolve) => {
    function listener(updatedTabId, changeInfo) {
      if (updatedTabId === tabId && changeInfo.status === 'complete') {
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }
    }
    chrome.tabs.onUpdated.addListener(listener);
  });
}

function navigateTab(tabId, url) {
  return new Promise((resolve) => {
    chrome.tabs.update(tabId, { url }, () => resolve());
  });
}

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

// ---- Viewport emulation via Chrome DevTools Protocol ----

const VIEWPORT_PRESETS = {
  desktop: { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false },
  mobile: {
    width: 375, height: 812, deviceScaleFactor: 3, mobile: true,
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1'
  }
};

async function attachDebugger(tabId) {
  return new Promise((resolve, reject) => {
    chrome.debugger.attach({ tabId }, '1.3', () => {
      if (chrome.runtime.lastError) {
        // Already attached is OK
        if (chrome.runtime.lastError.message.includes('Already attached')) {
          resolve();
        } else {
          reject(new Error(chrome.runtime.lastError.message));
        }
      } else {
        resolve();
      }
    });
  });
}

async function sendDebuggerCommand(tabId, method, params = {}) {
  return new Promise((resolve, reject) => {
    chrome.debugger.sendCommand({ tabId }, method, params, (result) => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
      } else {
        resolve(result);
      }
    });
  });
}

async function setViewportEmulation(tabId, viewport) {
  await attachDebugger(tabId);
  await sendDebuggerCommand(tabId, 'Emulation.setDeviceMetricsOverride', {
    width: viewport.width,
    height: viewport.height,
    deviceScaleFactor: viewport.deviceScaleFactor || 1,
    mobile: viewport.mobile || false,
  });
  if (viewport.userAgent) {
    await sendDebuggerCommand(tabId, 'Emulation.setUserAgentOverride', {
      userAgent: viewport.userAgent,
    });
  }
}

async function clearViewportEmulation(tabId) {
  try {
    await sendDebuggerCommand(tabId, 'Emulation.clearDeviceMetricsOverride');
    chrome.debugger.detach({ tabId }, () => {
      if (chrome.runtime.lastError) {
        // Ignore detach errors
      }
    });
  } catch (err) {
    console.warn('[Viewport] Clear emulation error:', err.message);
  }
}

async function captureScreenshot(tabId) {
  try {
    const tab = await chrome.tabs.get(tabId);
    const restrictedProtocols = ['chrome://', 'edge://', 'about:', 'devtools://', 'chrome-extension://', 'edge-extension://'];

    if (!tab.url || tab.url.trim() === '') return null;
    for (const protocol of restrictedProtocols) {
      if (tab.url.startsWith(protocol)) return null;
    }

    // Must be active tab to capture
    await chrome.tabs.update(tabId, { active: true });
    await sleep(300);

    const dataUrl = await chrome.tabs.captureVisibleTab(null, { format: 'png' });
    return dataUrl.replace(/^data:image\/png;base64,/, '');
  } catch (err) {
    console.error('[Screenshot] Failed:', err.message);
    return null;
  }
}

async function injectAndCapture(tabId) {
  const tab = await chrome.tabs.get(tabId);
  const restrictedProtocols = ['chrome://', 'edge://', 'about:', 'devtools://', 'chrome-extension://', 'edge-extension://'];

  if (!tab.url || tab.url.trim() === '') throw new Error('Cannot inject into empty URL');
  for (const protocol of restrictedProtocols) {
    if (tab.url.startsWith(protocol)) throw new Error(`Cannot inject into restricted URL: ${protocol}`);
  }

  const capturePromise = waitForCapture(30000);

  await chrome.scripting.executeScript({
    target: { tabId },
    files: ['content/content-script.js']
  });

  return capturePromise;
}

async function injectJsErrorCapture(tabId) {
  try {
    await chrome.scripting.executeScript({
      target: { tabId },
      func: () => {
        if (!window.__jsErrors) window.__jsErrors = [];
        window.addEventListener('error', (e) => {
          const entry = {
            message: (e.message || 'Unknown error').substring(0, 300),
            source: e.filename || '',
            line: e.lineno || 0,
            col: e.colno || 0,
            stack: (e.error && e.error.stack) ? e.error.stack.substring(0, 500) : ''
          };
          if (!window.__jsErrors.some(x => x.message === entry.message && x.line === entry.line)) {
            window.__jsErrors.push(entry);
          }
        }, true);
        window.addEventListener('unhandledrejection', (e) => {
          const msg = (e.reason instanceof Error)
            ? e.reason.message
            : String(e.reason).substring(0, 300);
          const entry = {
            message: ('Unhandled Promise Rejection: ' + msg).substring(0, 300),
            source: '',
            line: 0,
            col: 0,
            stack: (e.reason instanceof Error && e.reason.stack) ? e.reason.stack.substring(0, 500) : ''
          };
          if (!window.__jsErrors.some(x => x.message === entry.message)) {
            window.__jsErrors.push(entry);
          }
        }, true);
      },
      world: 'MAIN',
    });
  } catch (err) {
    console.warn('[JS Error Capture] Injection failed:', err.message);
  }
}

// ---- Dedicated agent tab ----

let agentTabId = null;

async function getOrCreateAgentTab(url) {
  if (agentTabId) {
    try {
      await chrome.tabs.get(agentTabId);
      return agentTabId;
    } catch {
      agentTabId = null;
    }
  }

  const tab = await chrome.tabs.create({ url: url || 'about:blank', active: false });
  agentTabId = tab.id;
  console.log('[Agent] Created dedicated tab:', agentTabId);
  return agentTabId;
}

// ---- WebSocket command handling ----

wsClient.onCommand(async (command) => {
  console.log('[WS] Received command:', command.type);

  try {
    // ---- Ping: wake-up check from backend ----
    if (command.type === 'ping') {
      wsClient.send({ type: 'pong', timestamp: Date.now() });
      return;
    }

    if (command.type === 'navigate') {
      const tabId = await getOrCreateAgentTab();
      await navigateTab(tabId, command.url);
      await waitForTabLoad(tabId);
      await injectJsErrorCapture(tabId);
      await sleep(2000);
      const data = await injectAndCapture(tabId);
      const screenshot = await captureScreenshot(tabId);
      wsClient.sendCaptureResult({ ...data, screenshot_base64: screenshot });
    }

    if (command.type === 'capture') {
      const tabId = await getOrCreateAgentTab();
      const data = await injectAndCapture(tabId);
      const screenshot = await captureScreenshot(tabId);
      wsClient.sendCaptureResult({ ...data, screenshot_base64: screenshot });
    }

    if (command.type === 'audit_page') {
      // All-in-one: navigate + wait + capture DOM + run axe-core + screenshot
      // Supports optional viewport parameter for mobile emulation
      const tabId = await getOrCreateAgentTab();

      // Apply viewport emulation if specified
      const viewport = command.viewport;
      if (viewport && viewport.mobile) {
        try {
          await setViewportEmulation(tabId, viewport);
        } catch (err) {
          console.warn('[Viewport] Emulation failed, proceeding without:', err.message);
        }
      }

      await navigateTab(tabId, command.url);
      await waitForTabLoad(tabId);
      await injectJsErrorCapture(tabId);
      await sleep(2000);
      const data = await injectAndCapture(tabId);
      const screenshot = await captureScreenshot(tabId);

      // Clear emulation after capture
      if (viewport && viewport.mobile) {
        await clearViewportEmulation(tabId);
      }

      wsClient.sendCaptureResult({
        ...data,
        screenshot_base64: screenshot,
        audited_url: command.url,
        viewport_type: viewport?.mobile ? 'mobile' : 'desktop',
      });
    }

    if (command.type === 'discover_links') {
      // Navigate to page, extract all internal links for backend to plan crawl
      const tabId = await getOrCreateAgentTab();
      await navigateTab(tabId, command.url);
      await waitForTabLoad(tabId);
      await sleep(2000);
      const data = await injectAndCapture(tabId);
      const screenshot = await captureScreenshot(tabId);

      // Categorize links by page type
      let baseDomain;
      try { baseDomain = new URL(command.url).hostname; } catch { baseDomain = ''; }
      const categorized = categorizeLinks(data.links || [], baseDomain);

      // Also collect all unique internal links
      const internalLinks = [];
      const seen = new Set();
      for (const link of (data.links || [])) {
        try {
          const parsed = new URL(link.href);
          if (parsed.hostname === baseDomain && !seen.has(parsed.pathname)) {
            seen.add(parsed.pathname);
            internalLinks.push({
              href: link.href,
              text: link.text,
              pathname: parsed.pathname,
            });
          }
        } catch {}
      }

      wsClient.sendCaptureResult({
        ...data,
        screenshot_base64: screenshot,
        categorized_links: categorized,
        internal_links: internalLinks.slice(0, 50),
        discovered_url: command.url,
      });
    }

    if (command.type === 'click') {
      const tabId = await getOrCreateAgentTab();
      await chrome.scripting.executeScript({
        target: { tabId },
        func: (selector) => {
          const el = document.querySelector(selector);
          if (el) el.click();
        },
        args: [command.selector]
      });
      await sleep(2000);
      const data = await injectAndCapture(tabId);
      const screenshot = await captureScreenshot(tabId);
      wsClient.sendCaptureResult({ ...data, screenshot_base64: screenshot });
    }

    if (command.type === 'get_elements') {
      const tabId = await getOrCreateAgentTab();
      const results = await chrome.scripting.executeScript({
        target: { tabId },
        func: () => {
          const elements = [];
          const selectors = 'a[href], button, input, select, textarea, [role="button"], [onclick]';
          document.querySelectorAll(selectors).forEach((el) => {
            const rect = el.getBoundingClientRect();
            const isVisible = rect.width > 0 && rect.height > 0 &&
              window.getComputedStyle(el).display !== 'none' &&
              window.getComputedStyle(el).visibility !== 'hidden';
            if (!isVisible) return;

            let cssSelector = '';
            if (el.id) {
              cssSelector = `#${el.id}`;
            } else {
              const tag = el.tagName.toLowerCase();
              const classes = Array.from(el.classList).slice(0, 2).join('.');
              cssSelector = classes ? `${tag}.${classes}` : tag;
            }

            elements.push({
              tag: el.tagName.toLowerCase(),
              type: el.type || null,
              text: (el.textContent || '').trim().substring(0, 100),
              href: el.href || null,
              ariaLabel: el.getAttribute('aria-label') || null,
              selector: cssSelector,
              visible: isVisible
            });
          });
          return elements.slice(0, 100);
        }
      });
      wsClient.sendCaptureResult({
        type: 'elements_result',
        elements: results[0]?.result || []
      });
    }

    if (command.type === 'scroll') {
      const tabId = await getOrCreateAgentTab();
      await chrome.scripting.executeScript({
        target: { tabId },
        func: (direction, amount) => {
          if (direction === 'top') window.scrollTo(0, 0);
          else if (direction === 'bottom') window.scrollTo(0, document.body.scrollHeight);
          else if (direction === 'down') window.scrollBy(0, amount);
          else if (direction === 'up') window.scrollBy(0, -amount);
        },
        args: [command.direction || 'down', command.amount || 500]
      });
      await sleep(1000);
      const data = await injectAndCapture(tabId);
      const screenshot = await captureScreenshot(tabId);
      wsClient.sendCaptureResult({ ...data, screenshot_base64: screenshot });
    }

    if (command.type === 'find_vehicle_link') {
      // Find the first vehicle listing link on the current inventory/SRP page.
      // Looks for common dealership vehicle card patterns and returns the top result.
      const tabId = await getOrCreateAgentTab();

      const results = await chrome.scripting.executeScript({
        target: { tabId },
        func: () => {
          // Dealership vehicle card selectors (ordered by specificity)
          const VEHICLE_SELECTORS = [
            // Common VDP link patterns
            'a[href*="/inventory/"]',
            'a[href*="/vehicle/"]',
            'a[href*="/vdp/"]',
            'a[href*="/new/"]',
            'a[href*="/used/"]',
            'a[href*="/certified/"]',
            'a[href*="/listing/"]',
            // Generic vehicle card links with descriptive text
            '.vehicle-card a',
            '.inventory-item a',
            '.srp-item a',
            '.car-card a',
            '.listing-card a',
            '[data-vehicle] a',
            '[class*="vehicle"] a[href]',
            '[class*="inventory"] a[href]',
            '[class*="listing"] a[href]',
          ];

          for (const selector of VEHICLE_SELECTORS) {
            try {
              const els = document.querySelectorAll(selector);
              for (const el of els) {
                const href = el.href || '';
                if (!href || href.startsWith('javascript:') || href.startsWith('#')) continue;
                // Skip navigation, header/footer links
                const rect = el.getBoundingClientRect();
                if (rect.width < 10 || rect.height < 10) continue;

                // Prefer links that look like VDPs (contain year + make pattern)
                const text = el.textContent.trim() || '';
                const title = el.title || el.getAttribute('aria-label') || text;
                return { vehicle_url: href, vehicle_title: title.substring(0, 120), selector };
              }
            } catch (_) { /* ignore selector errors */ }
          }

          // Fallback: scan all anchor tags for vehicle-like URLs
          const allLinks = Array.from(document.querySelectorAll('a[href]'));
          for (const el of allLinks) {
            const href = el.href || '';
            const hrefLower = href.toLowerCase();
            const vehiclePathPatterns = [
              '/inventory/', '/vehicle/', '/vdp/', '/new/', '/used/',
              '/certified/', '/listing/', '/cars/', '/trucks/', '/suvs/',
            ];
            if (vehiclePathPatterns.some(p => hrefLower.includes(p))) {
              const rect = el.getBoundingClientRect();
              if (rect.width < 5 || rect.height < 5) continue;
              const text = el.textContent.trim() || '';
              return {
                vehicle_url: href,
                vehicle_title: (el.title || text).substring(0, 120),
                selector: 'a[href]',
              };
            }
          }

          return { vehicle_url: null, vehicle_title: null, selector: null };
        }
      });

      const vehicleInfo = results[0]?.result || {};
      wsClient.sendCaptureResult(vehicleInfo);
    }
    if (command.type === 'check_srp_filters') {
      // Detect filter controls on the current SRP page and test for zero-result combos.
      // Handles both standard <select> filters and URL-parameter / custom widget filters.
      const tabId = await getOrCreateAgentTab();

      const results = await chrome.scripting.executeScript({
        target: { tabId },
        func: async () => {
          function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

          function getResultCount() {
            const COUNT_SELS = [
              '[class*="results-count"]', '[class*="inventory-count"]',
              '[class*="vehicle-count"]', '[class*="total-results"]',
              '[class*="result-count"]', '[class*="vehicles-found"]',
              '[class*="search-count"]', '[class*="listings-count"]',
            ];
            for (const sel of COUNT_SELS) {
              const el = document.querySelector(sel);
              if (el) {
                const m = el.textContent.match(/(\d[\d,]*)/);
                if (m) return parseInt(m[0].replace(/,/g, ''));
              }
            }
            const cards = document.querySelectorAll(
              '.vehicle-card, .inventory-item, .srp-item, .car-card, ' +
              '[class*="vehicle-listing"], [class*="inventory-card"], [class*="vehicle-tile"]'
            );
            return cards.length || null;
          }

          // ─── Strategy A: detect standard <select> filter controls ───────────
          // Exclude selects inside <header>, <footer>, <nav> and tiny utility selects.
          function isInsideExcluded(el) {
            let p = el.parentElement;
            while (p) {
              const tag = p.tagName.toLowerCase();
              if (tag === 'header' || tag === 'footer' || tag === 'nav') return true;
              const cls = (p.className || '').toLowerCase();
              if (cls.includes('cookie') || cls.includes('gdpr') || cls.includes('language-switcher')) return true;
              p = p.parentElement;
            }
            return false;
          }

          // Preference: named/id-based inventory selects first, then any visible select
          const NAMED_SELS = [
            'select[name*="make" i]', 'select[id*="make" i]',
            'select[name*="model" i]', 'select[id*="model" i]',
            'select[name*="year" i]', 'select[id*="year" i]',
            'select[name*="body" i]', 'select[id*="body" i]',
            'select[name*="type" i]', 'select[id*="type" i]',
            'select[name*="trim" i]', 'select[id*="trim" i]',
            '[class*="filter"] select:not([disabled])',
            '[class*="search"] select:not([disabled])',
            '[class*="facet"] select:not([disabled])',
            '[class*="sidebar"] select:not([disabled])',
          ];

          const selectFilters = [];
          const seenEls = new WeakSet();

          for (const sel of NAMED_SELS) {
            try {
              for (const el of document.querySelectorAll(sel)) {
                if (seenEls.has(el) || isInsideExcluded(el)) continue;
                seenEls.add(el);
                const opts = Array.from(el.options).filter(o => o.value && o.value.trim() !== '');
                if (opts.length >= 2) {
                  selectFilters.push({ el, name: el.name || el.id || el.getAttribute('aria-label') || 'filter', options: opts });
                  if (selectFilters.length >= 3) break;
                }
              }
            } catch (_) {}
            if (selectFilters.length >= 3) break;
          }

          // Fallback: any visible <select> with 3+ options not already captured
          if (!selectFilters.length) {
            try {
              for (const el of document.querySelectorAll('select:not([disabled])')) {
                if (seenEls.has(el) || isInsideExcluded(el)) continue;
                seenEls.add(el);
                const opts = Array.from(el.options).filter(o => o.value && o.value.trim() !== '');
                if (opts.length >= 3) {
                  selectFilters.push({ el, name: el.name || el.id || el.getAttribute('aria-label') || 'filter', options: opts });
                  if (selectFilters.length >= 3) break;
                }
              }
            } catch (_) {}
          }

          // ─── Strategy B: detect URL-parameter-based filters ─────────────────
          // These are links (or the current URL) that contain inventory filter query params.
          // Common patterns: ?make=ford, ?type=used, _dFR[make]=ford, ?category=trucks
          const FILTER_PARAM_PATTERNS = [
            /[?&](make|model|year|type|condition|category|body.?style|trim|mileage|price)=/i,
            /_dFR\[/,                           // Algolia InstantSearch
            /\[make\]=|%5Bmake%5D=/i,           // bracket-encoded
            /facet|filter|refinement/i,
          ];

          let urlParamFiltersDetected = false;
          const currentUrl = window.location.href;
          const currentSearch = window.location.search;

          if (FILTER_PARAM_PATTERNS.some(rx => rx.test(currentUrl))) {
            urlParamFiltersDetected = true;
          }

          if (!urlParamFiltersDetected) {
            // Check if filter links exist in the page
            const anchors = Array.from(document.querySelectorAll('a[href]')).slice(0, 200);
            const filterLinkCount = anchors.filter(a => {
              const href = a.getAttribute('href') || '';
              return FILTER_PARAM_PATTERNS.some(rx => rx.test(href));
            }).length;
            if (filterLinkCount >= 3) urlParamFiltersDetected = true;
          }

          if (!urlParamFiltersDetected) {
            // Check for Algolia/React widget class names
            const algoliaEls = document.querySelectorAll(
              '.ais-RefinementList, .ais-Menu, .ais-HierarchicalMenu, ' +
              '[class*="refinement-list"], [class*="facet-list"], ' +
              '[class*="filter-group"], [class*="filter-panel"], ' +
              '[class*="filter-options"], [data-filter], [data-facet]'
            );
            if (algoliaEls.length > 0) urlParamFiltersDetected = true;
          }

          // ─── Interactive test for <select> filters ───────────────────────────
          if (!selectFilters.length && !urlParamFiltersDetected) {
            return { zero_results: [], filters_checked: 0, filter_type: 'none' };
          }

          if (!selectFilters.length && urlParamFiltersDetected) {
            // URL-param / custom widget filters exist but can't be tested interactively
            return {
              zero_results: [],
              filters_checked: 0,
              filter_type: 'url_params',
              note: 'Filter controls detected (URL-parameter or custom widget style — interactive testing not applicable)',
            };
          }

          const zeroResults = [];

          for (const { el, name, options } of selectFilters) {
            const originalValue = el.value;
            let tested = 0;
            for (const opt of options.slice(0, 4)) {
              if (opt.value === originalValue) continue;
              el.value = opt.value;
              el.dispatchEvent(new Event('change', { bubbles: true }));
              el.dispatchEvent(new Event('input', { bubbles: true }));
              await sleep(2500);
              const count = getResultCount();
              tested++;
              if (count === 0) {
                zeroResults.push({
                  filter: name,
                  option_value: opt.value,
                  option_text: opt.textContent.trim().substring(0, 60),
                  result_count: 0,
                });
              }
            }
            // Restore original selection
            el.value = originalValue;
            el.dispatchEvent(new Event('change', { bubbles: true }));
            await sleep(1000);
          }

          return {
            zero_results: zeroResults,
            filters_checked: selectFilters.length,
            filter_type: 'select',
          };
        }
      });

      const filterData = results[0]?.result || { zero_results: [], filters_checked: 0, filter_type: 'none' };
      wsClient.sendCaptureResult({ type: 'filter_check_result', ...filterData });
    }

    if (command.type === 'fill_contact_form') {
      // Fill visible contact form fields with dummy data (does NOT submit).
      const tabId = await getOrCreateAgentTab();

      const filled = await chrome.scripting.executeScript({
        target: { tabId },
        func: () => {
          const DUMMY = {
            // Name variants
            firstname:    'Alex',
            first_name:   'Alex',
            fname:        'Alex',
            lastname:     'Thompson',
            last_name:    'Thompson',
            lname:        'Thompson',
            name:         'Alex Thompson',
            fullname:     'Alex Thompson',
            full_name:    'Alex Thompson',
            // Contact
            email:        'alex.thompson@example.com',
            phone:        '5558675309',
            phonenumber:  '5558675309',
            phone_number: '5558675309',
            mobile:       '5558675309',
            zip:          '19406',
            zipcode:      '19406',
            zip_code:     '19406',
            postal:       '19406',
            city:         'King of Prussia',
            state:        'PA',
            address:      '123 Main Street',
            // Message / comments
            message:      'I am interested in learning more. Please contact me at your earliest convenience.',
            comments:     'I am interested in learning more. Please contact me at your earliest convenience.',
            comment:      'I am interested in learning more.',
            subject:      'Website Inquiry',
            inquiry:      'General Inquiry',
            note:         'I am interested in learning more.',
            notes:        'I am interested in learning more.',
            howcanwehelp: 'I am interested in learning more about your services.',
          };

          const filledFields = [];
          const inputs = document.querySelectorAll(
            'input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="checkbox"]):not([type="radio"]):not([type="file"]):not([disabled]), textarea:not([disabled]), select:not([disabled])'
          );

          inputs.forEach((el) => {
            if (!el.offsetParent) return; // skip hidden elements

            // Derive a key from name, id, placeholder, or aria-label
            const raw = (
              el.name || el.id || el.placeholder || el.getAttribute('aria-label') || ''
            ).toLowerCase().replace(/[\s\-_]/g, '');

            const value = DUMMY[raw] || null;

            if (el.tagName === 'SELECT') {
              // Pick a non-empty option
              const opts = Array.from(el.options);
              const opt = opts.find(o => o.value && o.value !== el.value);
              if (opt) {
                el.value = opt.value;
                el.dispatchEvent(new Event('change', { bubbles: true }));
                filledFields.push(el.name || el.id || 'select');
              }
            } else if (value) {
              el.focus();
              el.value = value;
              el.dispatchEvent(new Event('input',  { bubbles: true }));
              el.dispatchEvent(new Event('change', { bubbles: true }));
              el.blur();
              filledFields.push(el.name || el.id || raw);
            }
          });

          return { filled: filledFields, count: filledFields.length };
        }
      });

      // Let the page react to the filled values before screenshotting
      await sleep(1500);
      const screenshot = await captureScreenshot(tabId);
      const fillResult = filled[0]?.result || { filled: [], count: 0 };
      wsClient.sendCaptureResult({
        ...fillResult,
        screenshot_base64: screenshot,
        type: 'form_filled',
      });
    }

  } catch (err) {
    console.error('[WS] Command error:', command.type, err);
    wsClient.sendCaptureResult({ error: err.message, command_type: command.type });
  }
});

console.log('[Service Worker] Web Audit Assistant initialized');
