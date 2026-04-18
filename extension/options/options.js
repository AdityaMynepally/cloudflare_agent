/**
 * Options page logic - saves backend URL and API key to chrome.storage.local
 */

const backendUrlInput = document.getElementById('backend-url');
const apiKeyInput = document.getElementById('api-key');
const saveBtn = document.getElementById('save-btn');
const saveStatus = document.getElementById('save-status');

// Load saved settings
chrome.storage.local.get(['backendUrl', 'apiKey'], (config) => {
  if (config.backendUrl) backendUrlInput.value = config.backendUrl;
  if (config.apiKey) apiKeyInput.value = config.apiKey;
});

// Save settings
saveBtn.addEventListener('click', () => {
  const backendUrl = backendUrlInput.value.trim().replace(/\/+$/, '');
  const apiKey = apiKeyInput.value.trim();

  chrome.storage.local.set({ backendUrl, apiKey }, () => {
    saveStatus.textContent = 'Saved!';
    setTimeout(() => { saveStatus.textContent = ''; }, 2000);
  });
});
