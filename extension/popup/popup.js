/**
 * Popup - Simplified to show connection status + link to chat UI.
 */

const statusDot = document.getElementById('status-dot');
const statusText = document.getElementById('status-text');
const wsStatus = document.getElementById('ws-status');
const settingsBtn = document.getElementById('settings-btn');
const openChatBtn = document.getElementById('open-chat-btn');
const reconnectBtn = document.getElementById('reconnect-btn');

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

checkConnection();
