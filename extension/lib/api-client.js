/**
 * REST API client for the backend.
 * Reads backendUrl and apiKey from chrome.storage.local.
 */

async function getConfig() {
  return new Promise((resolve) => {
    chrome.storage.local.get(['backendUrl', 'apiKey'], (config) => {
      resolve({
        backendUrl: (config.backendUrl || 'http://localhost:8000').replace(/\/+$/, ''),
        apiKey: config.apiKey || ''
      });
    });
  });
}

function buildHeaders(apiKey) {
  const headers = { 'Content-Type': 'application/json' };
  if (apiKey) {
    headers['X-API-Key'] = apiKey;
  }
  return headers;
}

export async function checkHealth() {
  const { backendUrl, apiKey } = await getConfig();
  try {
    const resp = await fetch(`${backendUrl}/health`, {
      headers: buildHeaders(apiKey)
    });
    return resp.ok;
  } catch {
    return false;
  }
}

/**
 * Send a single page capture to the backend.
 */
export async function sendCapture(captureData) {
  const { backendUrl, apiKey } = await getConfig();
  const resp = await fetch(`${backendUrl}/api/v2/capture`, {
    method: 'POST',
    headers: buildHeaders(apiKey),
    body: JSON.stringify(captureData)
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`Backend error ${resp.status}: ${text}`);
  }
  return resp.json();
}

/**
 * Send multi-page captures to the backend.
 */
export async function sendMultiPageCapture(multiData) {
  const { backendUrl, apiKey } = await getConfig();
  const resp = await fetch(`${backendUrl}/api/v2/capture/multipage`, {
    method: 'POST',
    headers: buildHeaders(apiKey),
    body: JSON.stringify(multiData)
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`Backend error ${resp.status}: ${text}`);
  }
  return resp.json();
}

/**
 * Send batch captures to the backend.
 */
export async function sendBatchCapture(batchData) {
  const { backendUrl, apiKey } = await getConfig();
  const resp = await fetch(`${backendUrl}/api/v2/capture/batch`, {
    method: 'POST',
    headers: buildHeaders(apiKey),
    body: JSON.stringify(batchData)
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`Backend error ${resp.status}: ${text}`);
  }
  return resp.json();
}
