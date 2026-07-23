/**
 * REST API client for the backend.
 * Reads backendUrl and apiKey from chrome.storage.local.
 */

export async function getConfig() {
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

/**
 * Pair this extension with a chat/UI session, using the pairing code (the
 * chat session's own SESSION_ID) shown in the chat UI. After this, audits
 * started from that chat session are routed to this specific extension
 * instead of an arbitrary connected one — required for multiple people to
 * share one deployed backend without their audits crossing into each
 * other's browsers.
 */
export async function pairSession(chatSessionId, extSessionId) {
  const { backendUrl, apiKey } = await getConfig();
  const resp = await fetch(`${backendUrl}/api/chat/pair`, {
    method: 'POST',
    headers: buildHeaders(apiKey),
    body: JSON.stringify({ session_id: chatSessionId, ext_session_id: extSessionId })
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`Backend error ${resp.status}: ${text}`);
  }
  return resp.json();
}
