// Context Engine — VS Code Extension Webview
// Communicates with the extension host via acquireVsCodeApi()
// Communicates with the Python backend via fetch (SSE for /generate/stream)

(function () {
  'use strict';

  const vscode = acquireVsCodeApi();

  // ── State ──────────────────────────────────────────────────────────────
  let state = {
    port: 8000,
    serverRunning: false,
    serverStarting: false,
    ollamaModels: [],       // models from Ollama /api/tags
    customModels: [],       // user-added models (persisted via globalState)
    generatorModel: '',
    assessorModel: '',
    activeModelPicker: null, // 'generator' or 'assessor'
    selectedMode: 'GASR',
    selectedScope: 'repo',  // 'repo' | 'file'
    activeFileForScope: null,
    contextChips: [],
    generating: false,
    messages: [],
    workspacePath: '',
    modelPanelOpen: false
  };

  // ── DOM ────────────────────────────────────────────────────────────────
  let app, chatArea, promptInput, sendBtn, modeSelect;
  let serverDot, serverBanner, indexProgress, contextChipRow;

  function buildUI() {
    document.getElementById('app').innerHTML = `
      <!-- Header -->
      <div class="header">
        <div class="header-left">
          <div class="logo">
            <i class="codicon codicon-circuit-board logo-icon"></i>
            <span>Context Engine</span>
          </div>
          <div class="server-dot" id="serverDot"></div>
        </div>
        <div class="header-actions">
          <button class="icon-btn" id="graphBtn" data-tooltip="View Call Graph" title="View Call Graph (opens in browser)"><i class="codicon codicon-type-hierarchy"></i></button>
          <button class="icon-btn" id="indexBtn" data-tooltip="Index Workspace" title="Index Workspace"><i class="codicon codicon-database"></i></button>
          <button class="icon-btn" id="serverToggleBtn" data-tooltip="Start Server" title="Start Server"><i class="codicon codicon-play" id="serverToggleIcon"></i></button>
          <button class="icon-btn" id="clearBtn" data-tooltip="Clear Chat" title="Clear Chat"><i class="codicon codicon-clear-all"></i></button>
        </div>
      </div>

      <!-- Server offline banner -->
      <div class="server-banner hidden" id="serverBanner">
        <span class="banner-text"><strong>Server offline</strong> — start the backend to use Context Engine.</span>
        <button class="action-btn" id="bannerStartBtn"><i class="codicon codicon-play"></i> Start</button>
      </div>

      <!-- Index progress -->
      <div class="index-progress hidden" id="indexProgress">
        <i class="codicon codicon-sync codicon-modifier-spin spin"></i>
        <span id="indexProgressText">Indexing workspace…</span>
      </div>

      <!-- Chat -->
      <div class="chat-area" id="chatArea">
        <!-- Empty state -->
        <div class="empty-state" id="emptyState">
          <div class="empty-state-icon"><i class="codicon codicon-circuit-board"></i></div>
          <h3>Context Engine (GASR)</h3>
          <p>Ask anything about your codebase. The GASR pipeline retrieves, assesses, graph-expands, and injects context automatically.</p>
          <div class="suggestion-chips">
            <span class="chip" data-prompt="How does the GASR retrieval pipeline work?">How does GASR work?</span>
            <span class="chip" data-prompt="Explain the graph expansion in the pipeline">Graph expansion?</span>
            <span class="chip" data-prompt="What models are available in this project?">Available models?</span>
            <span class="chip" data-prompt="How is the context injected into the prompt?">How is context injected?</span>
          </div>
        </div>
      </div>

      <!-- Input area -->
      <div class="input-area">
        <div class="context-chip-row" id="contextChipRow"></div>
        <div class="textarea-wrapper">
          <textarea id="prompt" placeholder="Ask anything about your codebase…" rows="3"></textarea>
          <button class="send-btn" id="sendBtn" title="Send (Enter)"><i class="codicon codicon-send"></i></button>
        </div>
        <div class="bottom-controls">
          <button class="add-context-btn" id="addFileBtn" title="Add active file as context"><i class="codicon codicon-file-add"></i> File</button>
          <button class="add-context-btn" id="addSelBtn" title="Add selection as context"><i class="codicon codicon-symbol-snippet"></i> Selection</button>
          <div class="spacer"></div>
          <!-- Scope toggle -->
          <div class="scope-toggle" id="scopeToggle" title="Context scope">
            <button class="scope-btn active" id="scopeRepoBtn" data-scope="repo"><i class="codicon codicon-folder"></i> Workspace</button>
            <button class="scope-btn" id="scopeFileBtn" data-scope="file"><i class="codicon codicon-file"></i> File</button>
          </div>
          <div class="model-selectors" style="display: flex; gap: 4px;">
            <button class="model-pill" id="assessorModelPillBtn" title="Choose Assessor model">
              <span style="color:var(--muted)">Assessor:</span>
              <span id="assessorModelPillLabel" style="margin-left: 2px;">Loading…</span>
            </button>
            <button class="model-pill" id="generatorModelPillBtn" title="Choose Generator model">
              <span style="color:var(--muted)">Gen:</span>
              <span id="generatorModelPillLabel" style="margin-left: 2px;">Loading…</span>
            </button>
          </div>
          <select class="select-pill" id="modeSelect" title="Pipeline mode">
            <option value="GASR">GASR (Adaptive)</option>
            <option value="SELF_ROUTE">Auto-Route</option>
            <option value="RAG">RAG</option>
            <option value="LC">Long Context</option>
          </select>
        </div>
        <!-- File scope indicator -->
        <div class="file-scope-bar hidden" id="fileScopeBar">
          <i class="codicon codicon-file file-scope-icon"></i>
          <span id="fileScopeLabel">No file open</span>
          <span class="file-scope-hint">— asking about this file only</span>
        </div>

        <!-- Model Manager Panel (dropdown) -->
        <div class="model-panel hidden" id="modelPanel">
          <div class="model-panel-header">
            <span>Models</span>
            <button class="icon-btn" id="refreshModelsBtn" title="Refresh from Ollama"><i class="codicon codicon-sync"></i></button>
          </div>

          <!-- Detected (Ollama) models -->
          <div class="model-section">
            <div class="model-section-label">Detected (Ollama)</div>
            <div id="ollamaModelList" class="model-list"></div>
          </div>

          <!-- Custom / other models -->
          <div class="model-section">
            <div class="model-section-label">Custom / Other</div>
            <div id="customModelList" class="model-list"></div>
            <!-- Add custom model row -->
            <div class="add-model-row">
              <input
                class="add-model-input"
                id="customModelInput"
                placeholder="e.g. mistral:7b, llama3:8b…"
                autocomplete="off"
                spellcheck="false"
              />
              <button class="add-model-btn" id="addCustomModelBtn">+ Add</button>
            </div>
          </div>
        </div>
      </div>
    `;

    // Bind references
    chatArea = document.getElementById('chatArea');
    promptInput = document.getElementById('prompt');
    sendBtn = document.getElementById('sendBtn');
    modeSelect = document.getElementById('modeSelect');
    serverDot = document.getElementById('serverDot');
    serverBanner = document.getElementById('serverBanner');
    indexProgress = document.getElementById('indexProgress');
    contextChipRow = document.getElementById('contextChipRow');

    // Events
    sendBtn.addEventListener('click', handleSend);
    promptInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); }
    });
    promptInput.addEventListener('input', autoResize);

    document.getElementById('indexBtn').addEventListener('click', handleIndex);
    document.getElementById('clearBtn').addEventListener('click', clearChat);
    document.getElementById('serverToggleBtn').addEventListener('click', toggleServer);
    document.getElementById('bannerStartBtn').addEventListener('click', () => {
      vscode.postMessage({ type: 'startServer' });
    });
    document.getElementById('graphBtn').addEventListener('click', openGraphView);
    document.getElementById('addFileBtn').addEventListener('click', () => {
      vscode.postMessage({ type: 'getActiveFile' });
    });
    document.getElementById('addSelBtn').addEventListener('click', () => {
      vscode.postMessage({ type: 'getSelection' });
    });

    // Scope toggle
    document.getElementById('scopeRepoBtn').addEventListener('click', () => setScope('repo'));
    document.getElementById('scopeFileBtn').addEventListener('click', () => setScope('file'));

    // Model panel toggle
    document.getElementById('assessorModelPillBtn').addEventListener('click', (e) => {
      e.stopPropagation();
      state.activeModelPicker = 'assessor';
      toggleModelPanel();
      renderModelManager();
    });
    document.getElementById('generatorModelPillBtn').addEventListener('click', (e) => {
      e.stopPropagation();
      state.activeModelPicker = 'generator';
      toggleModelPanel();
      renderModelManager();
    });
    document.getElementById('refreshModelsBtn').addEventListener('click', () => {
      fetchOllamaModels();
    });
    document.getElementById('addCustomModelBtn').addEventListener('click', () => {
      const input = document.getElementById('customModelInput');
      const val = (input.value || '').trim();
      if (val) { addCustomModel(val); input.value = ''; }
    });
    document.getElementById('customModelInput').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        const val = (e.target.value || '').trim();
        if (val) { addCustomModel(val); e.target.value = ''; }
      }
    });

    // Close model panel when clicking outside
    document.addEventListener('click', (e) => {
      const panel = document.getElementById('modelPanel');
      const btn1 = document.getElementById('assessorModelPillBtn');
      const btn2 = document.getElementById('generatorModelPillBtn');
      if (panel && !panel.contains(e.target) && !btn1.contains(e.target) && !btn2.contains(e.target)) {
        closeModelPanel();
      }
    });

    modeSelect.addEventListener('change', () => {
      state.selectedMode = modeSelect.value;
    });

    // Suggestion chips
    document.querySelectorAll('.chip[data-prompt]').forEach(chip => {
      chip.addEventListener('click', () => {
        promptInput.value = chip.dataset.prompt;
        autoResize();
        handleSend();
      });
    });

    // Request workspace path, saved custom models, and initial Ollama list
    vscode.postMessage({ type: 'getWorkspacePath' });
    vscode.postMessage({ type: 'loadCustomModels' });
    fetchOllamaModels();
  }

  // ── Graph View ─────────────────────────────────────────────────────────
  function openGraphView() {
    const url = `http://127.0.0.1:${state.port}/graphify-view`;
    // Open in the system browser via the extension host
    vscode.postMessage({ type: 'openExternal', url });
  }

  // ── Scope toggle ──────────────────────────────────────────────────────
  function setScope(scope) {
    state.selectedScope = scope;
    state.activeFileForScope = null;
    document.getElementById('scopeRepoBtn').classList.toggle('active', scope === 'repo');
    document.getElementById('scopeFileBtn').classList.toggle('active', scope === 'file');
    const bar = document.getElementById('fileScopeBar');
    if (scope === 'file') {
      bar.classList.remove('hidden');
      vscode.postMessage({ type: 'getActiveFileInfo' });
    } else {
      bar.classList.add('hidden');
    }
  }

  function updateFileScopeBar(relativePath) {
    const label = document.getElementById('fileScopeLabel');
    if (label) { label.textContent = relativePath || 'No file open'; }
  }

  // ── Auto-resize textarea ─────────────────────────────────────────────────
  function autoResize() {
    promptInput.style.height = 'auto';
    promptInput.style.height = Math.min(promptInput.scrollHeight, 180) + 'px';
  }

  // ── Server UI ─────────────────────────────────────────────────────────
  function setServerUI(running, starting = false) {
    state.serverRunning = running;
    state.serverStarting = starting;
    const dot = serverDot;
    const toggleBtn = document.getElementById('serverToggleBtn');
    const toggleIcon = document.getElementById('serverToggleIcon');
    if (starting) {
      dot.className = 'server-dot starting';
      if (toggleIcon) { toggleIcon.className = 'codicon codicon-loading codicon-modifier-spin'; }
      if (toggleBtn) { toggleBtn.title = 'Stop Server'; }
      if (serverBanner) { serverBanner.classList.add('hidden'); }
    } else if (running) {
      dot.className = 'server-dot running';
      if (toggleIcon) { toggleIcon.className = 'codicon codicon-debug-stop'; }
      if (toggleBtn) { toggleBtn.title = 'Stop Server'; }
      if (serverBanner) { serverBanner.classList.add('hidden'); }
    } else {
      dot.className = 'server-dot';
      if (toggleIcon) { toggleIcon.className = 'codicon codicon-play'; }
      if (toggleBtn) { toggleBtn.title = 'Start Server'; }
      if (serverBanner) { serverBanner.classList.remove('hidden'); }
    }
    // Index & graph buttons are always visible — only warn when clicked if server is down
  }

  function toggleServer() {
    if (state.serverRunning || state.serverStarting) {
      vscode.postMessage({ type: 'stopServer' });
    } else {
      vscode.postMessage({ type: 'startServer' });
      setServerUI(false, true);
    }
  }

  // ── Fetch Ollama models ────────────────────────────────────────────────
  async function fetchOllamaModels() {
    const refreshBtn = document.getElementById('refreshModelsBtn');
    if (refreshBtn) { refreshBtn.classList.add('spin'); }
    try {
      const resp = await fetch(`http://127.0.0.1:${state.port}/models`);
      if (!resp.ok) { throw new Error('bad response'); }
      const data = await resp.json();
      state.ollamaModels = data.models || [];
      renderModelManager();
    } catch {
      // Server not up yet — leave as-is
    } finally {
      if (refreshBtn) { refreshBtn.classList.remove('spin'); }
    }
  }

  // Keep old name as alias
  function fetchModels() { fetchOllamaModels(); }

  // ── Model Panel ───────────────────────────────────────────────────────
  function toggleModelPanel() {
    state.modelPanelOpen = !state.modelPanelOpen;
    const panel = document.getElementById('modelPanel');
    if (panel) { panel.classList.toggle('hidden', !state.modelPanelOpen); }
  }
  function closeModelPanel() {
    state.modelPanelOpen = false;
    const panel = document.getElementById('modelPanel');
    if (panel) { panel.classList.add('hidden'); }
  }

  function allModels() {
    const seen = new Set(state.ollamaModels);
    const extras = state.customModels.filter(m => !seen.has(m));
    return [...state.ollamaModels, ...extras];
  }

  function setSelectedModel(model) {
    if (state.activeModelPicker === 'assessor') {
      state.assessorModel = model;
      const pill = document.getElementById('assessorModelPillLabel');
      if (pill) { pill.textContent = model || 'No model'; }
    } else {
      state.generatorModel = model;
      const pill = document.getElementById('generatorModelPillLabel');
      if (pill) { pill.textContent = model || 'No model'; }
    }
    closeModelPanel();
  }

  function renderModelManager() {
    const ollamaList = document.getElementById('ollamaModelList');
    const customList = document.getElementById('customModelList');
    if (!ollamaList || !customList) { return; }

    const all = allModels();
    if (!state.generatorModel && all.length > 0) {
      state.generatorModel = all[0];
    }
    if (!state.assessorModel && all.length > 0) {
      // Prefer a small model for assessor
      const small = all.find(m => m.includes('3b') || m.includes('3.2') || m.includes('llama3')) || all[0];
      state.assessorModel = small;
    }
    const genPill = document.getElementById('generatorModelPillLabel');
    if (genPill) { genPill.textContent = state.generatorModel || (all.length ? all[0] : 'No model'); }
    const assPill = document.getElementById('assessorModelPillLabel');
    if (assPill) { assPill.textContent = state.assessorModel || (all.length ? all[0] : 'No model'); }

    const activeModel = state.activeModelPicker === 'assessor' ? state.assessorModel : state.generatorModel;

    if (state.ollamaModels.length === 0) {
      ollamaList.innerHTML = `<div class="model-empty">No Ollama models detected.<br><span style="font-size:10px">Run: <code>ollama pull qwen3.5:9b</code></span></div>`;
    } else {
      ollamaList.innerHTML = state.ollamaModels.map(m => modelRow(m, 'ollama', activeModel)).join('');
    }

    const ollamaSet = new Set(state.ollamaModels);
    const customOnly = state.customModels.filter(m => !ollamaSet.has(m));
    if (customOnly.length === 0) {
      customList.innerHTML = `<div class="model-empty">No custom models added yet.</div>`;
    } else {
      customList.innerHTML = customOnly.map(m => modelRow(m, 'custom', activeModel)).join('');
    }

    document.querySelectorAll('.model-row').forEach(row => {
      row.querySelector('.model-row-name').addEventListener('click', () => {
        setSelectedModel(row.dataset.model);
        renderModelManager();
      });
      const removeBtn = row.querySelector('.model-row-remove');
      if (removeBtn) {
        removeBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          removeCustomModel(row.dataset.model);
        });
      }
    });
  }

  function modelRow(name, source, activeModel) {
    const isSelected = name === activeModel;
    const isCustom = source === 'custom';
    return `<div class="model-row ${isSelected ? 'selected' : ''}" data-model="${escHtml(name)}">
      <span class="model-row-dot ${isSelected ? 'active' : ''}"></span>
      <span class="model-row-name" title="${escHtml(name)}">${escHtml(name)}</span>
      <span class="model-row-source ${source}">${source === 'ollama' ? 'Ollama' : 'Custom'}</span>
      ${isCustom ? `<button class="model-row-remove" title="Remove"><i class="codicon codicon-close"></i></button>` : ''}
    </div>`;
  }

  // ── Custom model management ────────────────────────────────────────────
  function addCustomModel(name) {
    if (!name) { return; }
    name = name.trim();
    if (!state.customModels.includes(name)) {
      state.customModels = [...state.customModels, name];
      persistCustomModels();
    }
    setSelectedModel(name);
    renderModelManager();
  }

  function removeCustomModel(name) {
    state.customModels = state.customModels.filter(m => m !== name);
    persistCustomModels();
    const all = allModels();
    if (state.generatorModel === name) { state.generatorModel = all[0] || ''; }
    if (state.assessorModel === name) { state.assessorModel = all[0] || ''; }
    renderModelManager();
  }

  function persistCustomModels() {
    vscode.postMessage({ type: 'saveCustomModels', models: state.customModels });
  }

  // ── Context Chips ─────────────────────────────────────────────────────
  function addContextChip(chip) {
    state.contextChips = state.contextChips.filter(c => c.label !== chip.label);
    state.contextChips.push(chip);
    renderContextChips();
  }

  function removeContextChip(label) {
    state.contextChips = state.contextChips.filter(c => c.label !== label);
    renderContextChips();
  }

  function renderContextChips() {
    contextChipRow.innerHTML = '';
    state.contextChips.forEach(chip => {
      const el = document.createElement('div');
      el.className = 'context-chip';
      el.innerHTML = `<i class="codicon codicon-file-add"></i> <span>${escHtml(chip.label)}</span> <span class="chip-remove" data-label="${escHtml(chip.label)}"><i class="codicon codicon-close"></i></span>`;
      el.querySelector('.chip-remove').addEventListener('click', () => removeContextChip(chip.label));
      contextChipRow.appendChild(el);
    });
  }

  // ── Send Message ───────────────────────────────────────────────────────
  async function handleSend() {
    const text = promptInput.value.trim();
    if (!text || state.generating) { return; }
    if (!state.serverRunning) {
      appendSystemMessage('⚠ Server is offline. Click ▶ in the header to start it.');
      return;
    }

    const emptyState = document.getElementById('emptyState');
    if (emptyState) { emptyState.remove(); }

    let contextPrefix = '';
    if (state.contextChips.length > 0) {
      contextPrefix = state.contextChips.map(c => {
        if (c.type === 'file') {
          return `[File: ${c.label}]\n\`\`\`\n${c.content}\n\`\`\``;
        } else {
          return `[Selection from ${c.label}:${c.startLine}-${c.endLine}]\n\`\`\`\n${c.content}\n\`\`\``;
        }
      }).join('\n\n') + '\n\n';
    }
    const fullPrompt = contextPrefix + text;

    appendUserMessage(text, state.contextChips.slice());
    promptInput.value = '';
    promptInput.style.height = 'auto';
    state.contextChips = [];
    renderContextChips();

    await streamGenerate(fullPrompt, state.selectedScope === 'file' ? state.activeFileForScope?.content : null);
  }

  // ── GASR stage order ─────────────────────────────────────────────────
  // Standard stages; graph_expand + re_prune are injected dynamically for GASR
  const BASE_STAGE_ORDER   = ['retrieve', 'prune', 'assess', 'inject', 'generate'];
  const GASR_STAGE_ORDER   = ['retrieve', 'prune', 'assess', 'graph_expand', 're_prune', 'inject', 'generate'];

  // ── Streaming ─────────────────────────────────────────────────────────
  async function streamGenerate(prompt, fileContext = null) {
    if (state.selectedScope === 'file' && !fileContext) {
      appendSystemMessage('⚠ Fetching current file content…');
      await new Promise(resolve => {
        const handler = (event) => {
          const msg = event.data;
          if (msg && msg.type === 'activeFileForScope') {
            window.removeEventListener('message', handler);
            if (msg.file) {
              state.activeFileForScope = msg.file;
              fileContext = msg.file.content;
              updateFileScopeBar(msg.file.relativePath);
            }
            resolve();
          }
        };
        window.addEventListener('message', handler);
        vscode.postMessage({ type: 'getActiveFileInfo' });
        setTimeout(() => { window.removeEventListener('message', handler); resolve(); }, 3000);
      });
    }
    state.generating = true;
    sendBtn.disabled = true;
    sendBtn.classList.add('loading');
    sendBtn.innerHTML = '<i class="codicon codicon-sync codicon-modifier-spin"></i>';

    const msgId = 'msg-' + Date.now();
    const msgEl = appendAgentMessageShell(msgId);
    const pipelineEl = msgEl.querySelector('.pipeline-accordion');
    const answerEl = msgEl.querySelector('.answer-text');

    const isGasr = state.selectedMode === 'GASR';
    const stageOrder = isGasr ? GASR_STAGE_ORDER : BASE_STAGE_ORDER;
    const stageRowEls = {};

    pipelineEl.querySelector('.pipeline-body').innerHTML = '';
    stageOrder.forEach(s => {
      // graph_expand and re_prune start hidden for GASR — shown when events arrive
      const row = createStageRow(s, 'pending');
      if (isGasr && (s === 'graph_expand' || s === 're_prune')) {
        row.classList.add('stage-row-hidden');
      }
      pipelineEl.querySelector('.pipeline-body').appendChild(row);
      stageRowEls[s] = row;
    });
    pipelineEl.classList.add('open');

    let fullAnswer = '';

    try {
      const resp = await fetch(`http://127.0.0.1:${state.port}/generate/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          prompt,
          mode: state.selectedMode,
          model: state.generatorModel || undefined,
          assessor_model: state.assessorModel || undefined,
          ...(fileContext ? { file_context: fileContext } : {})
        })
      });

      if (!resp.ok) { throw new Error(`HTTP ${resp.status}`); }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) { break; }
        buffer += decoder.decode(value, { stream: true });

        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) { continue; }
          const raw = line.slice(6).trim();
          if (!raw) { continue; }

          let ev;
          try { ev = JSON.parse(raw); } catch { continue; }

          if (ev.event === 'stage_start') {
            const row = stageRowEls[ev.stage];
            if (row) {
              row.classList.remove('stage-row-hidden');
              updateStageRow(row, 'running', ev.label, {});
            }
          } else if (ev.event === 'stage') {
            const row = stageRowEls[ev.stage];
            if (row) {
              row.classList.remove('stage-row-hidden');
              updateStageRow(row, ev.status, ev.label, ev.summary || {}, ev.duration_ms);
            }
          } else if (ev.event === 'token') {
            fullAnswer += ev.content;
            renderAnswer(answerEl, fullAnswer, true);
            scrollToBottom();
          } else if (ev.event === 'done') {
            fullAnswer = ev.output || fullAnswer;
          }
        }
      }
    } catch (err) {
      renderAnswer(answerEl, `⚠ Error: ${err.message}`, false);
    }

    renderAnswer(answerEl, fullAnswer || '(No response)', false);
    const metaEl = document.getElementById(`${msgId}-pipeline-meta`);
    if (metaEl) {
      const doneCount = Object.values(stageRowEls).filter(r => {
        const icon = r && r.querySelector('.stage-icon');
        return icon && (icon.classList.contains('complete') || icon.classList.contains('skipped'));
      }).length;
      metaEl.textContent = `${doneCount}/${stageOrder.length} stages done`;
    }

    state.generating = false;
    sendBtn.disabled = false;
    sendBtn.classList.remove('loading');
    sendBtn.innerHTML = '<i class="codicon codicon-send"></i>';
    scrollToBottom();
  }

  // ── Message DOM helpers ───────────────────────────────────────────────

  function appendUserMessage(text, chips) {
    const el = document.createElement('div');
    el.className = 'message user';
    let chipHtml = '';
    if (chips.length > 0) {
      chipHtml = `<div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:4px;justify-content:flex-end">
        ${chips.map(c => `<span class="context-chip" style="cursor:default"><i class="codicon codicon-file-add"></i> ${escHtml(c.label)}</span>`).join('')}
      </div>`;
    }
    el.innerHTML = `
      <div class="avatar user-avatar">U</div>
      <div class="message-content">
        ${chipHtml}
        <div class="bubble user-bubble">${escHtml(text)}</div>
      </div>`;
    chatArea.appendChild(el);
    scrollToBottom();
  }

  function appendAgentMessageShell(id) {
    const el = document.createElement('div');
    el.className = 'message';
    el.id = id;
    el.innerHTML = `
      <div class="avatar agent-avatar"><i class="codicon codicon-circuit-board"></i></div>
      <div class="message-content" style="width:100%">
        <div class="bubble agent-bubble">
          <div class="pipeline-accordion" id="${id}-pipeline">
            <div class="pipeline-header" onclick="toggleAccordion('${id}-pipeline')">
              <div class="pipeline-header-left">
                <i class="codicon codicon-chevron-right pipeline-chevron"></i>
                <span>Pipeline</span>
              </div>
              <span style="font-size:11px;color:var(--muted)" id="${id}-pipeline-meta"></span>
            </div>
            <div class="pipeline-body"></div>
          </div>
          <div class="answer-text cursor-blink" id="${id}-answer"></div>
        </div>
      </div>`;
    chatArea.appendChild(el);
    scrollToBottom();
    return el;
  }

  function appendSystemMessage(text) {
    const el = document.createElement('div');
    el.style.cssText = 'text-align:center;font-size:12px;color:var(--muted);padding:4px 0;';
    el.textContent = text;
    chatArea.appendChild(el);
    scrollToBottom();
  }

  // ── Stage rows ─────────────────────────────────────────────────────────

  const STAGE_ICONS = {
    retrieve:     '<i class="codicon codicon-search"></i>',
    prune:        '<i class="codicon codicon-filter"></i>',
    assess:       '<i class="codicon codicon-law"></i>',
    graph_expand: '<i class="codicon codicon-type-hierarchy"></i>',
    re_prune:     '<i class="codicon codicon-filter"></i>',
    inject:       '<i class="codicon codicon-insert"></i>',
    generate:     '<i class="codicon codicon-sparkle"></i>'
  };
  const STAGE_LABELS = {
    retrieve:     'Retrieve',
    prune:        'Prune',
    assess:       'Assess',
    graph_expand: 'Graph Expand',
    re_prune:     'Re-Prune',
    inject:       'Inject',
    generate:     'Generate'
  };

  function createStageRow(stage, status) {
    const row = document.createElement('div');
    row.className = 'stage-row';
    row.id = `stage-${stage}-${Date.now()}`;
    row.innerHTML = `
      <div class="stage-icon pending">${STAGE_ICONS[stage] || '<i class="codicon codicon-circle-filled"></i>'}</div>
      <div class="stage-label">${STAGE_LABELS[stage] || stage}</div>
      <div class="stage-meta"></div>`;
    return row;
  }

  function updateStageRow(row, status, label, summary, durationMs) {
    if (!row) { return; }
    const icon = row.querySelector('.stage-icon');
    const meta = row.querySelector('.stage-meta');

    icon.className = `stage-icon ${status}`;
    if (status === 'running')  { icon.innerHTML = '<i class="codicon codicon-sync codicon-modifier-spin"></i>'; }
    else if (status === 'complete') { icon.innerHTML = '<i class="codicon codicon-check"></i>'; }
    else if (status === 'error')    { icon.innerHTML = '<i class="codicon codicon-error"></i>'; }
    else if (status === 'skipped')  { icon.innerHTML = '<i class="codicon codicon-dash"></i>'; }

    const badges = [];
    if (durationMs !== undefined && durationMs > 0) {
      badges.push(`<span style="color:var(--muted)">${durationMs}ms</span>`);
    }
    if (summary) {
      if (summary.note) {
        badges.push(`<span style="color:var(--muted);font-style:italic;font-size:10px">${escHtml(summary.note)}</span>`);
      }
      if (summary.candidate_chunks !== undefined) {
        const cls = summary.candidate_chunks === 0 ? 'badge-error' : 'badge-retrieve';
        badges.push(makeBadge(`${summary.candidate_chunks} chunks`, cls));
        if (summary.candidate_chunks === 0) {
          badges.push(`<span style="color:var(--danger);font-size:10px"><i class="codicon codicon-warning"></i> Index empty — click <i class="codicon codicon-database"></i> to index</span>`);
        }
      }
      if (summary.unique_files !== undefined) {
        badges.push(makeBadge(`${summary.unique_files} files`, 'badge-retrieve'));
      }
      if (summary.stats?.kept_est_tokens !== undefined) {
        badges.push(makeBadge(`${summary.stats.kept_est_tokens} tok`, 'badge-prune'));
        if (summary.stats.dropped_chunks > 0) {
          badges.push(makeBadge(`-${summary.stats.dropped_chunks} dropped`, 'badge-prune'));
        }
      }
      // GASR-specific: route badge with hop info
      if (summary.route) {
        let cls = 'badge-rag';
        if (summary.route === 'LC') { cls = 'badge-lc'; }
        else if (summary.route === 'GASR' || summary.hops_used > 0) { cls = 'badge-gasr'; }
        badges.push(makeBadge(summary.route, cls));
        if (summary.hops_used !== undefined && summary.hops_used > 0) {
          badges.push(makeBadge(`${summary.hops_used} hop${summary.hops_used > 1 ? 's' : ''}`, 'badge-gasr'));
        }
        if (summary.confidence) {
          badges.push(`<span style="color:var(--muted)">${summary.confidence}</span>`);
        }
        if (summary.reason) {
          badges.push(`<span style="color:var(--muted);font-size:10px;font-style:italic">${escHtml(summary.reason)}</span>`);
        }
      }
      // Graph expand: show neighbours count
      if (summary.hops_used !== undefined && summary.route === undefined) {
        badges.push(makeBadge(`${summary.hops_used} hop${summary.hops_used !== 1 ? 's' : ''}`, 'badge-gasr'));
      }
      if (summary.expansions !== undefined) {
        const total = Array.isArray(summary.expansions) ? summary.expansions.reduce((a, e) => a + (e.new_chunks || 0), 0) : 0;
        if (total > 0) { badges.push(makeBadge(`+${total} neighbours`, 'badge-gasr')); }
      }
      if (summary.kept !== undefined) {
        badges.push(makeBadge(`${summary.kept} kept`, 'badge-prune'));
      }
      if (summary.estimated_tokens !== undefined) {
        badges.push(makeBadge(`~${summary.estimated_tokens} tok`, 'badge-inject'));
      }
      if (summary.output_chars !== undefined) {
        badges.push(makeBadge(`${summary.output_chars} chars`, 'badge-generate'));
      }
    }
    meta.innerHTML = badges.join('');
  }

  function makeBadge(text, cls) {
    return `<span class="stage-badge ${cls}">${escHtml(String(text))}</span>`;
  }

  // ── Answer rendering ───────────────────────────────────────────────────

  function renderAnswer(el, text, streaming) {
    el.classList.toggle('cursor-blink', streaming);
    el.innerHTML = markdownToHtml(text);
    el.querySelectorAll('.code-copy-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const code = btn.closest('.code-block-wrapper').querySelector('code');
        vscode.postMessage({ type: 'copyToClipboard', text: code.textContent });
        btn.textContent = 'Copied!';
        setTimeout(() => { btn.textContent = 'Copy'; }, 1500);
      });
    });
  }

  function markdownToHtml(text) {
    text = text.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
      const l = lang || 'code';
      return `<div class="code-block-wrapper">
        <div class="code-block-header">
          <span class="code-lang">${escHtml(l)}</span>
          <button class="code-copy-btn">Copy</button>
        </div>
        <pre class="code-block"><code>${escHtml(code.trimEnd())}</code></pre>
      </div>`;
    });
    text = text.replace(/`([^`]+)`/g, '<code>$1</code>');
    text = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    text = text.replace(/^### (.+)$/gm, '<strong style="font-size:13px;display:block;margin-top:8px">$1</strong>');
    text = text.replace(/^## (.+)$/gm, '<strong style="font-size:14px;display:block;margin-top:10px">$1</strong>');
    text = text.replace(/^# (.+)$/gm, '<strong style="font-size:15px;display:block;margin-top:12px">$1</strong>');
    text = text.replace(/^[*-] (.+)$/gm, '• $1');
    return text;
  }

  // ── Index Workspace ────────────────────────────────────────────────────
  async function handleIndex() {
    if (!state.serverRunning) {
      vscode.postMessage({ type: 'showError', text: 'Start the server first before indexing.' });
      return;
    }
    const repoPath = state.workspacePath;
    const shortPath = repoPath ? '...' + repoPath.split('/').slice(-2).join('/') : 'workspace';
    indexProgress.classList.remove('hidden');
    const progressText = document.getElementById('indexProgressText');
    progressText.textContent = `Indexing ${shortPath} (may take 1-5 min for large repos)...`;

    try {
      const url = new URL(`http://127.0.0.1:${state.port}/index`);
      if (repoPath) { url.searchParams.set('repo_path', repoPath); }
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 10 * 60 * 1000);
      const resp = await fetch(url.toString(), { method: 'POST', signal: controller.signal });
      clearTimeout(timer);
      if (!resp.ok) { throw new Error('HTTP ' + resp.status); }
      const data = await resp.json();
      const summary = data.index_summary || {};
      const files = summary.total_files != null ? summary.total_files : (data.indexed_files || '?');
      const chunks = summary.total_chunks != null ? summary.total_chunks : '?';
      progressText.textContent = `Indexed ${files} files / ${chunks} chunks — Workspace queries ready!`;
      setTimeout(() => indexProgress.classList.add('hidden'), 6000);
    } catch (err) {
      const msg = err.name === 'AbortError'
        ? 'Indexing timed out (>10 min). Try a smaller directory.'
        : 'Index error: ' + err.message;
      progressText.textContent = msg;
      setTimeout(() => indexProgress.classList.add('hidden'), 6000);
    }
  }

  async function checkIndexStatus() {
    try {
      const resp = await fetch(`http://127.0.0.1:${state.port}/index/summary`);
      if (!resp.ok) { return; }
      const data = await resp.json();
      const chunks = data.total_chunks != null ? data.total_chunks : 0;
      if (chunks === 0) {
        const progressText = document.getElementById('indexProgressText');
        indexProgress.classList.remove('hidden');
        progressText.innerHTML = 'Workspace index is empty. Click <strong><i class="codicon codicon-database"></i></strong> in the header to index first.';
        setTimeout(() => indexProgress.classList.add('hidden'), 10000);
      }
    } catch { /* server not ready yet */ }
  }

  // ── Utilities ─────────────────────────────────────────────────────────

  function escHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function scrollToBottom() {
    chatArea.scrollTop = chatArea.scrollHeight;
  }

  function clearChat() {
    chatArea.innerHTML = `
      <div class="empty-state" id="emptyState">
        <div class="empty-state-icon"><i class="codicon codicon-circuit-board"></i></div>
        <h3>Context Engine (GASR)</h3>
        <p>Ask anything about your codebase. The GASR pipeline retrieves, assesses, graph-expands, and injects context automatically.</p>
        <div class="suggestion-chips">
          <span class="chip" data-prompt="How does the GASR retrieval pipeline work?">How does GASR work?</span>
          <span class="chip" data-prompt="Explain the graph expansion in the pipeline">Graph expansion?</span>
          <span class="chip" data-prompt="What models are available in this project?">Available models?</span>
          <span class="chip" data-prompt="How is the context injected into the prompt?">How is context injected?</span>
        </div>
      </div>`;
    document.querySelectorAll('.chip[data-prompt]').forEach(chip => {
      chip.addEventListener('click', () => {
        promptInput.value = chip.dataset.prompt;
        autoResize();
        handleSend();
      });
    });
  }

  // Exposed globally for inline onclick
  window.toggleAccordion = function(id) {
    const el = document.getElementById(id);
    if (el) { el.classList.toggle('open'); }
  };

  // ── Messages from extension host ───────────────────────────────────────
  window.addEventListener('message', (event) => {
    const msg = event.data;
    switch (msg.type) {
      case 'serverStatus':
        if (msg.status === 'running') {
          state.port = msg.port || state.port;
          setServerUI(true);
          fetchModels();
          checkIndexStatus();
        } else if (msg.status === 'starting') {
          setServerUI(false, true);
        } else {
          setServerUI(false);
        }
        break;

      case 'workspacePath':
        state.workspacePath = msg.path || '';
        break;

      case 'customModels':
        state.customModels = Array.isArray(msg.models) ? msg.models : [];
        renderModelManager();
        break;

      case 'activeFile':
        if (msg.file) {
          addContextChip({
            type: 'file',
            label: msg.file.relativePath,
            content: msg.file.content,
            path: msg.file.path
          });
        }
        break;

      case 'selection':
        if (msg.selection) {
          addContextChip({
            type: 'selection',
            label: `${msg.selection.relativePath}:${msg.selection.startLine}-${msg.selection.endLine}`,
            content: msg.selection.text,
            path: msg.selection.path,
            startLine: msg.selection.startLine,
            endLine: msg.selection.endLine
          });
        }
        break;

      case 'editorChanged':
        if (state.selectedScope === 'file' && msg.file) {
          updateFileScopeBar(msg.file.relativePath);
          state.activeFileForScope = null;
        }
        break;

      case 'activeFileForScope':
        if (msg.file) {
          state.activeFileForScope = msg.file;
          updateFileScopeBar(msg.file.relativePath);
        } else {
          updateFileScopeBar('No file open');
        }
        break;

      case 'triggerIndex':
        handleIndex();
        break;
    }
  });

  // ── Init ───────────────────────────────────────────────────────────────
  buildUI();

  // Probe server on load — retry up to 3 times with backoff to catch
  // a server that was already running before the extension opened
  (async function probeOnLoad() {
    const delays = [0, 800, 2000];
    for (const delay of delays) {
      if (delay > 0) { await new Promise(r => setTimeout(r, delay)); }
      if (state.serverRunning) { break; } // already detected
      try {
        const r = await fetch(`http://127.0.0.1:${state.port}/`, { signal: AbortSignal.timeout(2000) });
        if (r.ok) {
          setServerUI(true);
          fetchModels();
          checkIndexStatus();
          break;
        }
      } catch { /* not up yet */ }
    }
    if (!state.serverRunning) { setServerUI(false); }
  })();

})();
