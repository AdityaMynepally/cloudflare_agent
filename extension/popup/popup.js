/**
 * Popup - Simplified to show connection status + link to chat UI.
 */

const statusDot = document.getElementById('status-dot');
const statusText = document.getElementById('status-text');
const wsStatus = document.getElementById('ws-status');
const settingsBtn = document.getElementById('settings-btn');
const openChatBtn = document.getElementById('open-chat-btn');
const reconnectBtn = document.getElementById('reconnect-btn');
const pairCodeInput = document.getElementById('pair-code');
const pairBtn = document.getElementById('pair-btn');
const pairStatus = document.getElementById('pair-status');

async function checkConnection() {
  statusDot.className = 'status-dot checking';
  statusText.textContent = 'Checking...';

  try {
    const response = await chrome.runtime.sendMessage({ type: 'CHECK_CONNECTION' });
    if (response && response.connected) {
      statusDot.className = 'status-dot connected';
      statusText.textContent = 'Backend connected';
      wsStatus.textContent = response.wsConnected ? 'WS: connected' : 'WS: disconnected';
    } else {
      statusDot.className = 'status-dot disconnected';
      statusText.textContent = 'Backend offline';
      wsStatus.textContent = '';
    }
  } catch (err) {
    statusDot.className = 'status-dot disconnected';
    statusText.textContent = 'Error checking connection';
    wsStatus.textContent = '';
  }
}

openChatBtn.addEventListener('click', () => {
  chrome.storage.local.get(['backendUrl'], (config) => {
    const url = (config.backendUrl || 'http://localhost:8000').replace(/\/+$/, '');
    chrome.tabs.create({ url });
  });
});

reconnectBtn.addEventListener('click', () => {
  chrome.runtime.sendMessage({ type: 'CONNECT_WS' }, () => {
    checkConnection();
  });
});

settingsBtn.addEventListener('click', () => {
  chrome.runtime.openOptionsPage();
});

pairBtn.addEventListener('click', () => {
  const code = pairCodeInput.value.trim();
  if (!code) {
    pairStatus.style.color = '#c0392b';
    pairStatus.textContent = 'Enter the pairing code shown in the chat UI first.';
    return;
  }
  pairStatus.style.color = '#555';
  pairStatus.textContent = 'Pairing...';
  chrome.runtime.sendMessage({ type: 'PAIR_SESSION', chatSessionId: code }, (response) => {
    if (response && response.ok) {
      pairStatus.style.color = '#28a745';
      pairStatus.textContent = response.extension_connected
        ? `Paired with ${code}`
        : `Paired with ${code} (extension will finish connecting shortly)`;
    } else {
      pairStatus.style.color = '#c0392b';
      pairStatus.textContent = `Pairing failed: ${response?.error || 'unknown error'}`;
    }
  });
});

function loadSavedPairing() {
  chrome.storage.local.get(['pairedChatSessionId'], (result) => {
    if (result.pairedChatSessionId) {
      pairCodeInput.value = result.pairedChatSessionId;
      pairStatus.style.color = '#555';
      pairStatus.textContent = `Last paired with ${result.pairedChatSessionId}`;
    }
  });
}

loadSavedPairing();
checkConnection();
