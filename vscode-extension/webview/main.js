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
    ollamaModels: [],       // models from Ollama /api/tags
    customModels: [],       // user-added models (persisted via globalState)
    selectedModel: '',
    selectedMode: 'SELF_ROUTE',
    selectedScope: 'repo',  // 'repo' | 'file'
    activeFileForScope: null, // {relativePath, content, language} — populated on demand
    contextChips: [],       // [{type:'file'|'selection', label, content, path, startLine, endLine}]
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
            <div class="logo-icon">⬡</div>
            <span>Context Engine</span>
          </div>
          <div class="server-dot" id="serverDot"></div>
        </div>
        <div class="header-actions">
          <button class="icon-btn" id="indexBtn" data-tooltip="Index Workspace" title="Index Workspace">⊕</button>
          <button class="icon-btn" id="serverToggleBtn" data-tooltip="Start Server" title="Start Server">▶</button>
          <button class="icon-btn" id="clearBtn" data-tooltip="Clear Chat" title="Clear Chat">⊘</button>
        </div>
      </div>

      <!-- Server offline banner -->
      <div class="server-banner hidden" id="serverBanner">
        <span class="banner-text"><strong>Server offline</strong> — start the backend to use Context Engine.</span>
        <button class="action-btn" id="bannerStartBtn">▶ Start</button>
      </div>

      <!-- Index progress -->
      <div class="index-progress hidden" id="indexProgress">
        <span class="spin">⟳</span>
        <span id="indexProgressText">Indexing workspace…</span>
      </div>

      <!-- Chat -->
      <div class="chat-area" id="chatArea">
        <!-- Empty state -->
        <div class="empty-state" id="emptyState">
          <div class="empty-state-icon">⬡</div>
          <h3>Context Engine</h3>
          <p>Ask anything about your codebase. The pipeline retrieves, prunes, and injects context automatically.</p>
          <div class="suggestion-chips">
            <span class="chip" data-prompt="How does the retrieval pipeline work?">How does retrieval work?</span>
            <span class="chip" data-prompt="Explain the pruning module">Explain pruning</span>
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
          <button class="send-btn" id="sendBtn" title="Send (Enter)">↑</button>
        </div>
        <div class="bottom-controls">
          <button class="add-context-btn" id="addFileBtn" title="Add active file as context">📎 File</button>
          <button class="add-context-btn" id="addSelBtn" title="Add selection as context">✂ Selection</button>
          <div class="spacer"></div>
          <!-- Scope toggle -->
          <div class="scope-toggle" id="scopeToggle" title="Context scope">
            <button class="scope-btn active" id="scopeRepoBtn" data-scope="repo">📁 Workspace</button>
            <button class="scope-btn" id="scopeFileBtn" data-scope="file">📄 File</button>
          </div>
          <button class="model-pill" id="modelPillBtn" title="Choose model">
            <span class="model-pill-icon">◈</span>
            <span id="modelPillLabel">Loading…</span>
            <span class="model-pill-arrow">▾</span>
          </button>
          <select class="select-pill" id="modeSelect" title="Pipeline mode">
            <option value="SELF_ROUTE">Auto-Route</option>
            <option value="RAG">RAG</option>
            <option value="LC">Long Context</option>
          </select>
        </div>
        <!-- File scope indicator -->
        <div class="file-scope-bar hidden" id="fileScopeBar">
          <span class="file-scope-icon">📄</span>
          <span id="fileScopeLabel">No file open</span>
          <span class="file-scope-hint">— asking about this file only</span>
        </div>

        <!-- Model Manager Panel (dropdown) -->
        <div class="model-panel hidden" id="modelPanel">
          <div class="model-panel-header">
            <span>Models</span>
            <button class="icon-btn" id="refreshModelsBtn" title="Refresh from Ollama">⟳</button>
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
    document.getElementById('modelPillBtn').addEventListener('click', (e) => {
      e.stopPropagation();
      toggleModelPanel();
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
      const btn = document.getElementById('modelPillBtn');
      if (panel && !panel.contains(e.target) && !btn.contains(e.target)) {
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

  // ── Scope toggle ──────────────────────────────────────────────────────
  function setScope(scope) {
    state.selectedScope = scope;
    state.activeFileForScope = null;
    document.getElementById('scopeRepoBtn').classList.toggle('active', scope === 'repo');
    document.getElementById('scopeFileBtn').classList.toggle('active', scope === 'file');
    const bar = document.getElementById('fileScopeBar');
    if (scope === 'file') {
      bar.classList.remove('hidden');
      // Ask host for the active editor info to show the file name
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
    const dot = serverDot;
    const toggleBtn = document.getElementById('serverToggleBtn');
    if (starting) {
      dot.className = 'server-dot starting';
      toggleBtn.textContent = '⏸';
      toggleBtn.title = 'Stop Server';
    } else if (running) {
      dot.className = 'server-dot running';
      toggleBtn.textContent = '⏹';
      toggleBtn.title = 'Stop Server';
      serverBanner.classList.add('hidden');
    } else {
      dot.className = 'server-dot';
      toggleBtn.textContent = '▶';
      toggleBtn.title = 'Start Server';
      serverBanner.classList.remove('hidden');
    }
  }

  function toggleServer() {
    if (state.serverRunning) {
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

  // Keep old name as alias so serverStatus handler can call it
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
    // Merge: ollama first, then custom ones not already in ollama list
    const seen = new Set(state.ollamaModels);
    const extras = state.customModels.filter(m => !seen.has(m));
    return [...state.ollamaModels, ...extras];
  }

  function setSelectedModel(model) {
    state.selectedModel = model;
    const pill = document.getElementById('modelPillLabel');
    if (pill) { pill.textContent = model || 'No model'; }
    closeModelPanel();
  }

  function renderModelManager() {
    const ollamaList = document.getElementById('ollamaModelList');
    const customList = document.getElementById('customModelList');
    if (!ollamaList || !customList) { return; }

    // Ensure selectedModel is valid — pick first available if not
    const all = allModels();
    if (!state.selectedModel && all.length > 0) {
      state.selectedModel = all[0];
    }
    // Update pill label
    const pill = document.getElementById('modelPillLabel');
    if (pill) { pill.textContent = state.selectedModel || (all.length ? all[0] : 'No model'); }

    // Render Ollama models
    if (state.ollamaModels.length === 0) {
      ollamaList.innerHTML = `<div class="model-empty">No Ollama models detected.<br><span style="font-size:10px">Run: <code>ollama pull qwen3.5:9b</code></span></div>`;
    } else {
      ollamaList.innerHTML = state.ollamaModels.map(m => modelRow(m, 'ollama')).join('');
    }

    // Render custom models (only those NOT already in Ollama list)
    const ollamaSet = new Set(state.ollamaModels);
    const customOnly = state.customModels.filter(m => !ollamaSet.has(m));
    if (customOnly.length === 0) {
      customList.innerHTML = `<div class="model-empty">No custom models added yet.</div>`;
    } else {
      customList.innerHTML = customOnly.map(m => modelRow(m, 'custom')).join('');
    }

    // Bind row clicks
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

  function modelRow(name, source) {
    const isSelected = name === state.selectedModel;
    const isCustom = source === 'custom';
    return `<div class="model-row ${isSelected ? 'selected' : ''}" data-model="${escHtml(name)}">
      <span class="model-row-dot ${isSelected ? 'active' : ''}"></span>
      <span class="model-row-name" title="${escHtml(name)}">${escHtml(name)}</span>
      <span class="model-row-source ${source}">${source === 'ollama' ? 'Ollama' : 'Custom'}</span>
      ${isCustom ? `<button class="model-row-remove" title="Remove">✕</button>` : ''}
    </div>`;
  }

  // ── Custom model management ────────────────────────────────────────────
  function addCustomModel(name) {
    if (!name) { return; }
    // Normalise: strip whitespace
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
    // If removed model was selected, pick first available
    if (state.selectedModel === name) {
      const all = allModels();
      state.selectedModel = all[0] || '';
    }
    renderModelManager();
  }

  function persistCustomModels() {
    vscode.postMessage({ type: 'saveCustomModels', models: state.customModels });
  }

  // ── Context Chips ─────────────────────────────────────────────────────
  function addContextChip(chip) {
    // Avoid duplicate paths
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
      el.innerHTML = `📎 <span>${escHtml(chip.label)}</span> <span class="chip-remove" data-label="${escHtml(chip.label)}">✕</span>`;
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

    // Clear empty state
    const emptyState = document.getElementById('emptyState');
    if (emptyState) { emptyState.remove(); }

    // Build context prefix
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

  // ── Streaming ─────────────────────────────────────────────────────
  async function streamGenerate(prompt, fileContext = null) {
    // If file scope is active but we don’t have the content yet, fetch it first
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
        // Timeout after 3s
        setTimeout(() => { window.removeEventListener('message', handler); resolve(); }, 3000);
      });
    }
    state.generating = true;
    sendBtn.disabled = true;
    sendBtn.classList.add('loading');
    sendBtn.textContent = '…';

    // Create agent message shell
    const msgId = 'msg-' + Date.now();
    const msgEl = appendAgentMessageShell(msgId);
    const pipelineEl = msgEl.querySelector('.pipeline-accordion');
    const answerEl = msgEl.querySelector('.answer-text');

    const stageOrder = ['retrieve', 'prune', 'assess', 'inject', 'generate'];
    const stageRowEls = {};

    // Pre-render "pending" stages
    pipelineEl.querySelector('.pipeline-body').innerHTML = '';
    stageOrder.forEach(s => {
      const row = createStageRow(s, 'pending');
      pipelineEl.querySelector('.pipeline-body').appendChild(row);
      stageRowEls[s] = row;
    });
    // Open accordion
    pipelineEl.classList.add('open');

    let fullAnswer = '';
    let streamDone = false;

    try {
      const resp = await fetch(`http://127.0.0.1:${state.port}/generate/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          prompt,
          mode: state.selectedMode,
          model: state.selectedModel || undefined,
          ...(fileContext ? { file_context: fileContext } : {})
        })
      });

      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }

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
            updateStageRow(stageRowEls[ev.stage], 'running', ev.label, {});
          } else if (ev.event === 'stage') {
            updateStageRow(stageRowEls[ev.stage], ev.status, ev.label, ev.summary || {}, ev.duration_ms);
          } else if (ev.event === 'token') {
            fullAnswer += ev.content;
            renderAnswer(answerEl, fullAnswer, true);
            scrollToBottom();
          } else if (ev.event === 'done') {
            fullAnswer = ev.output || fullAnswer;
            streamDone = true;
          }
        }
      }
    } catch (err) {
      renderAnswer(answerEl, `⚠ Error: ${err.message}`, false);
    }

    // Finalize answer
    renderAnswer(answerEl, fullAnswer || '(No response)', false);
    // Keep accordion open so user can review pipeline stats.
    // The header is already clickable to toggle it closed.
    // Update pipeline meta with a summary count of completed stages.
    const metaEl = document.getElementById(`${msgId}-pipeline-meta`);
    if (metaEl) {
      const doneCount = Object.values(stageRowEls).filter(r => {
        const icon = r && r.querySelector('.stage-icon');
        return icon && (icon.classList.contains('complete') || icon.classList.contains('skipped'));
      }).length;
      metaEl.textContent = `${doneCount}/5 stages done`;
    }

    state.generating = false;
    sendBtn.disabled = false;
    sendBtn.classList.remove('loading');
    sendBtn.textContent = '↑';
    scrollToBottom();
  }

  // ── Message DOM helpers ───────────────────────────────────────────────

  function appendUserMessage(text, chips) {
    const el = document.createElement('div');
    el.className = 'message user';
    let chipHtml = '';
    if (chips.length > 0) {
      chipHtml = `<div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:4px;justify-content:flex-end">
        ${chips.map(c => `<span class="context-chip" style="cursor:default">📎 ${escHtml(c.label)}</span>`).join('')}
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
      <div class="avatar agent-avatar">⬡</div>
      <div class="message-content" style="width:100%">
        <div class="bubble agent-bubble">
          <div class="pipeline-accordion" id="${id}-pipeline">
            <div class="pipeline-header" onclick="toggleAccordion('${id}-pipeline')">
              <div class="pipeline-header-left">
                <span class="pipeline-chevron">▶</span>
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

  const STAGE_ICONS = { retrieve: '🔍', prune: '✂', assess: '⚖', inject: '📝', generate: '✨' };
  const STAGE_LABELS = { retrieve: 'Retrieve', prune: 'Prune', assess: 'Assess', inject: 'Inject', generate: 'Generate' };

  function createStageRow(stage, status) {
    const row = document.createElement('div');
    row.className = 'stage-row';
    row.id = `stage-${stage}-${Date.now()}`;
    row.innerHTML = `
      <div class="stage-icon pending">${STAGE_ICONS[stage] || '•'}</div>
      <div class="stage-label">${STAGE_LABELS[stage] || stage}</div>
      <div class="stage-meta"></div>`;
    return row;
  }

  function updateStageRow(row, status, label, summary, durationMs) {
    if (!row) { return; }
    const icon = row.querySelector('.stage-icon');
    const meta = row.querySelector('.stage-meta');

    icon.className = `stage-icon ${status}`;
    if (status === 'running')  { icon.textContent = '⟳'; }
    else if (status === 'complete') { icon.textContent = '✔'; }
    else if (status === 'error')    { icon.textContent = '✕'; }
    else if (status === 'skipped')  { icon.textContent = '—'; }

    // Build meta badges
    const badges = [];
    if (durationMs !== undefined && durationMs > 0) {
      badges.push(`<span style="color:var(--muted)">${durationMs}ms</span>`);
    }
    if (summary) {
      // Skipped note
      if (summary.note) {
        badges.push(`<span style="color:var(--muted);font-style:italic;font-size:10px">${escHtml(summary.note)}</span>`);
      }
      if (summary.candidate_chunks !== undefined) {
        const cls = summary.candidate_chunks === 0 ? 'badge-error' : 'badge-retrieve';
        badges.push(makeBadge(`${summary.candidate_chunks} chunks`, cls));
        if (summary.candidate_chunks === 0) {
          badges.push(`<span style="color:var(--danger);font-size:10px">⚠ Index empty — click ⊕ to index</span>`);
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
      if (summary.route) {
        const cls = summary.route === 'RAG' ? 'badge-rag' : 'badge-lc';
        badges.push(makeBadge(summary.route, cls));
        if (summary.confidence) {
          badges.push(`<span style="color:var(--muted)">${summary.confidence}</span>`);
        }
        if (summary.reason) {
          badges.push(`<span style="color:var(--muted);font-size:10px;font-style:italic">${escHtml(summary.reason)}</span>`);
        }
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
    // Re-bind copy buttons
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
    // Code blocks
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
    // Inline code
    text = text.replace(/`([^`]+)`/g, '<code>$1</code>');
    // Bold
    text = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    // Headers
    text = text.replace(/^### (.+)$/gm, '<strong style="font-size:13px;display:block;margin-top:8px">$1</strong>');
    text = text.replace(/^## (.+)$/gm, '<strong style="font-size:14px;display:block;margin-top:10px">$1</strong>');
    text = text.replace(/^# (.+)$/gm, '<strong style="font-size:15px;display:block;margin-top:12px">$1</strong>');
    // List items
    text = text.replace(/^[*-] (.+)$/gm, '• $1');
    return text;
  }

  // ── Index Workspace ────────────────────────────────────────────────────
  async function handleIndex() {
    if (!state.serverRunning) {
      vscode.postMessage({ type: 'showError', text: 'Start the server first before indexing.' });
      return;
    }
    indexProgress.classList.remove('hidden');
    document.getElementById('indexProgressText').textContent = 'Indexing workspace…';

    try {
      const url = new URL(`http://127.0.0.1:${state.port}/index`);
      if (state.workspacePath) {
        url.searchParams.set('repo_path', state.workspacePath);
      }
      const resp = await fetch(url.toString(), { method: 'POST' });
      const data = await resp.json();
      const count = data.index_summary?.total_files || data.indexed_files || '?';
      document.getElementById('indexProgressText').textContent = `✔ Indexed ${count} files`;
      setTimeout(() => indexProgress.classList.add('hidden'), 3000);
    } catch (err) {
      document.getElementById('indexProgressText').textContent = `⚠ Index error: ${err.message}`;
      setTimeout(() => indexProgress.classList.add('hidden'), 4000);
    }
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
        <div class="empty-state-icon">⬡</div>
        <h3>Context Engine</h3>
        <p>Ask anything about your codebase. The pipeline retrieves, prunes, and injects context automatically.</p>
        <div class="suggestion-chips">
          <span class="chip" data-prompt="How does the retrieval pipeline work?">How does retrieval work?</span>
          <span class="chip" data-prompt="Explain the pruning module">Explain pruning</span>
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
        // Received persisted custom models from globalState
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
        // Update file scope bar if we're in file mode
        if (state.selectedScope === 'file' && msg.file) {
          updateFileScopeBar(msg.file.relativePath);
          state.activeFileForScope = null; // stale, will re-fetch on next send
        }
        break;

      case 'activeFileForScope':
        // Response to getActiveFileInfo — update scope bar and cache content
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

  // Probe the server on load
  fetch(`http://127.0.0.1:${state.port}/`)
    .then(r => { if (r.ok) { setServerUI(true); fetchModels(); } else { setServerUI(false); } })
    .catch(() => setServerUI(false));

})();
