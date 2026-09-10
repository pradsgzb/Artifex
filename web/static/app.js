(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const ui = {
    form: $('composer'), prompt: $('prompt'), image: $('image'), send: $('send'), conversation: $('conversation'),
    welcome: $('welcome'), attachment: $('attachment'), preview: $('preview'), fileName: $('fileName'),
    fileMeta: $('fileMeta'), removeImage: $('removeImage'), status: $('status'), settings: $('settings'),
    toggleSettings: $('toggleSettings'), apiKey: $('apiKey'), systemPrompt: $('systemPrompt'),
    responseFormat: $('responseFormat'), maxTokens: $('maxTokens')
  };
  const state = { history: [], selectedFile: null, selectedDataUrl: '', capabilities: null, busy: false };
  ui.apiKey.value = sessionStorage.getItem('artifexApiKey') || '';

  const escapeName = (name) => name || 'image';
  const humanBytes = (bytes) => bytes < 1024 ? `${bytes} B` : bytes < 1048576 ? `${(bytes/1024).toFixed(1)} KB` : `${(bytes/1048576).toFixed(1)} MB`;
  const headers = () => {
    const result = { 'Content-Type': 'application/json' };
    const key = ui.apiKey.value.trim();
    if (key) result['X-API-Key'] = key;
    return result;
  };

  function setStatus(text, kind = '') {
    ui.status.className = `status ${kind}`.trim();
    ui.status.querySelector('span:last-child').textContent = text;
  }
  function scrollBottom() { requestAnimationFrame(() => window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' })); }
  function createMessage(role, content, imageUrl = '', isError = false) {
    ui.welcome?.remove();
    const row = document.createElement('article'); row.className = `message ${role}`;
    const avatar = document.createElement('div'); avatar.className = 'avatar'; avatar.textContent = role === 'assistant' ? 'A' : 'You';
    const body = document.createElement('div'); body.className = 'message-body';
    const label = document.createElement('div'); label.className = 'message-role'; label.textContent = role === 'assistant' ? 'Artifex' : 'You';
    if (imageUrl) { const image = document.createElement('img'); image.className = 'message-image'; image.src = imageUrl; image.alt = 'Uploaded image'; body.appendChild(image); }
    const text = document.createElement('div'); text.className = `message-content${isError ? ' error-text' : ''}`; text.textContent = content;
    body.prepend(label); body.appendChild(text); row.append(avatar, body); ui.conversation.appendChild(row); scrollBottom(); return row;
  }
  function createTyping() {
    const row = createMessage('assistant', ''); const content = row.querySelector('.message-content');
    content.innerHTML = '<span class="typing"><i></i><i></i><i></i></span>'; return row;
  }
  function resetImage() { state.selectedFile = null; state.selectedDataUrl = ''; ui.image.value = ''; ui.attachment.classList.add('hidden'); ui.preview.removeAttribute('src'); }
  function resizePrompt() { ui.prompt.style.height = 'auto'; ui.prompt.style.height = `${Math.min(ui.prompt.scrollHeight, 180)}px`; }

  async function loadCapabilities() {
    try {
      const [health, capabilities] = await Promise.all([fetch('/api/v1/health'), fetch('/api/v1/capabilities')]);
      if (!health.ok || !capabilities.ok) throw new Error('Service unavailable');
      const h = await health.json(); state.capabilities = await capabilities.json();
      ui.maxTokens.max = String(state.capabilities.maxRequestNewTokens || 4096);
      setStatus(h.modelLoaded ? 'Model ready' : 'Server ready · model loads on first request', 'ok');
    } catch (error) { setStatus('Server unavailable', 'error'); }
  }

  ui.image.addEventListener('change', () => {
    const file = ui.image.files?.[0]; if (!file) return resetImage();
    const allowed = ['image/jpeg','image/png','image/webp'];
    if (!allowed.includes(file.type)) { alert('Select a JPEG, PNG, or WebP image.'); return resetImage(); }
    const max = state.capabilities?.maxUploadBytes || 26214400;
    if (file.size > max) { alert(`Image exceeds the ${humanBytes(max)} limit.`); return resetImage(); }
    const reader = new FileReader(); reader.onload = () => {
      state.selectedFile = file; state.selectedDataUrl = String(reader.result || '');
      ui.preview.src = state.selectedDataUrl; ui.fileName.textContent = escapeName(file.name); ui.fileMeta.textContent = humanBytes(file.size); ui.attachment.classList.remove('hidden');
    }; reader.readAsDataURL(file);
  });
  ui.removeImage.addEventListener('click', resetImage);
  ui.toggleSettings.addEventListener('click', () => ui.settings.classList.toggle('hidden'));
  ui.apiKey.addEventListener('input', () => sessionStorage.setItem('artifexApiKey', ui.apiKey.value));
  ui.prompt.addEventListener('input', resizePrompt);
  ui.prompt.addEventListener('keydown', (event) => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); ui.form.requestSubmit(); } });

  ui.form.addEventListener('submit', async (event) => {
    event.preventDefault(); if (state.busy) return;
    const prompt = ui.prompt.value.trim(); if (!prompt) return;
    const priorHistory = state.history.slice(); const imageUrl = state.selectedDataUrl;
    createMessage('user', prompt, imageUrl); state.history.push({ role: 'user', content: prompt });
    const request = {
      prompt,
      systemPrompt: ui.systemPrompt.value.trim(),
      history: priorHistory,
      imageBase64: imageUrl || null,
      imageMimeType: state.selectedFile?.type || null,
      maxNewTokens: Number(ui.maxTokens.value || 1024),
      responseFormat: ui.responseFormat.value
    };
    ui.prompt.value = ''; resizePrompt(); resetImage(); state.busy = true; ui.send.disabled = true; const typing = createTyping();
    try {
      const response = await fetch('/api/v1/chat', { method: 'POST', headers: headers(), body: JSON.stringify(request) });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || `Request failed with HTTP ${response.status}`);
      typing.remove(); createMessage('assistant', payload.output); state.history.push({ role: 'assistant', content: payload.output }); setStatus('Model ready', 'ok');
    } catch (error) {
      typing.remove(); createMessage('assistant', error instanceof Error ? error.message : String(error), '', true); setStatus('Request failed', 'error');
    } finally { state.busy = false; ui.send.disabled = false; ui.prompt.focus(); }
  });
  loadCapabilities(); ui.prompt.focus();
})();
