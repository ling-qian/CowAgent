/**
 * Agent Builder — 前端逻辑
 * 管理 Agent 配置的 5 个 Tab：基础、模型、插件、知识、工具
 */

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const AB = {
    config: null,       // 当前 AgentConfig
    plan: 'free',       // 当前租户计划
    plugins: [],        // 可用插件列表
    knowledgeFiles: [], // 知识文件列表
    customTools: [],    // 自定义工具列表
    toolLimit: 0,       // 自定义工具限制
    editingToolId: null,// 正在编辑的工具 ID
    activeTab: 'basic', // 当前 Tab
    _initialized: false,// 防止重复绑定事件
    _saving: false,     // 防止重复提交
    _pluginsLoaded: false, // 插件是否已加载
};

// ---------------------------------------------------------------------------
// API Helpers
// ---------------------------------------------------------------------------

/** HTML 属性值转义（处理 <, >, &, ", '） */
function abEscAttr(str) {
    return String(str ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

/** HTML 内容转义 */
function abEsc(str) {
    const d = document.createElement('div');
    d.textContent = String(str ?? '');
    return d.innerHTML;
}

/** 校验 CSS class 名安全（只允许字母数字下划线空格和连字符） */
function abSafeClass(str) {
    const safe = String(str ?? '').replace(/[^a-zA-Z0-9_\- ]/g, '');
    return safe || 'fas fa-puzzle-piece';
}

/** 显示 toast 通知 */
function abToast(msg, type = 'error') {
    let container = document.getElementById('ab-toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'ab-toast-container';
        container.style.cssText = 'position:fixed;top:20px;right:20px;z-index:9999;display:flex;flex-direction:column;gap:8px;max-height:200px;overflow:hidden;';
        document.body.appendChild(container);
    }
    // 最多显示 3 个 toast
    while (container.children.length >= 3) container.firstChild.remove();
    const colors = type === 'success'
        ? 'bg-green-500 text-white'
        : 'bg-red-500 text-white';
    const el = document.createElement('div');
    el.className = `px-4 py-2 rounded-lg shadow-lg text-sm ${colors} transition-opacity duration-300`;
    el.textContent = msg;
    container.appendChild(el);
    setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 300); }, 3000);
}

async function abFetch(url, opts = {}) {
    const token = localStorage.getItem('cow_api_key') || '';
    const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
    if (token) headers['Authorization'] = `Bearer ${token}`;
    const resp = await fetch(url, { ...opts, headers });
    if (!resp.ok) {
        const err = await resp.json().catch(() => ({ error: resp.statusText }));
        throw new Error(err.error || `HTTP ${resp.status}`);
    }
    return resp.json();
}

// ---------------------------------------------------------------------------
// Tab Switching
// ---------------------------------------------------------------------------

function abSwitchTab(tab) {
    AB.activeTab = tab;
    // Toggle tab buttons
    document.querySelectorAll('.ab-tab').forEach(btn => {
        const isActive = btn.dataset.tab === tab;
        btn.classList.toggle('active', isActive);
        if (isActive) {
            btn.classList.add('bg-white', 'dark:bg-white/10', 'shadow-sm', 'text-primary-600', 'dark:text-primary-400');
            btn.classList.remove('text-slate-500', 'dark:text-slate-400');
        } else {
            btn.classList.remove('bg-white', 'dark:bg-white/10', 'shadow-sm', 'text-primary-600', 'dark:text-primary-400');
            btn.classList.add('text-slate-500', 'dark:text-slate-400');
        }
    });
    // Toggle panels
    document.querySelectorAll('.ab-panel').forEach(p => p.classList.add('hidden'));
    const panel = document.getElementById(`ab-panel-${tab}`);
    if (panel) panel.classList.remove('hidden');
    // Lazy load data
    if (tab === 'plugins' && !AB._pluginsLoaded) abLoadPlugins();
    if (tab === 'knowledge') abLoadKnowledge();
    if (tab === 'tools') abLoadTools();
    if (tab === 'preview') abRenderPreview();
}

// ---------------------------------------------------------------------------
// Load Config
// ---------------------------------------------------------------------------

async function abLoadConfig() {
    try {
        const data = await abFetch('/api/agent/config');
        AB.config = data;
        AB.plan = data.plan || 'free';
        // Populate form fields
        const el = (id) => document.getElementById(id);
        el('ab-name').value = data.name || '';
        el('ab-description').value = data.description || '';
        el('ab-system-prompt').value = data.system_prompt || '';
        el('ab-temperature').value = data.temperature ?? 0.7;
        el('ab-temperature-val').textContent = data.temperature ?? 0.7;
        el('ab-max-steps').value = data.max_steps ?? 5;
        el('ab-enable-thinking').checked = data.enable_thinking ?? false;
        // Plan badge
        el('ab-plan-badge').textContent = AB.plan.toUpperCase();
    } catch (e) {
        console.error('[AB] Load config failed:', e);
        abToast('加载配置失败: ' + e.message);
    }
}

// ---------------------------------------------------------------------------
// Save Config
// ---------------------------------------------------------------------------

async function abSaveConfig() {
    if (AB._saving) return;
    AB._saving = true;
    const el = (id) => document.getElementById(id);
    const payload = {
        name: el('ab-name').value.trim(),
        description: el('ab-description').value.trim(),
        system_prompt: el('ab-system-prompt').value.trim(),
        temperature: parseFloat(el('ab-temperature').value),
        max_steps: parseInt(el('ab-max-steps').value) || 5,
        enable_thinking: el('ab-enable-thinking').checked,
    };
    try {
        await abFetch('/api/agent/config', {
            method: 'PUT',
            body: JSON.stringify(payload),
        });
        abShowSaveStatus('已保存');
    } catch (e) {
        abToast('保存失败: ' + e.message);
    } finally {
        AB._saving = false;
    }
}

function abResetConfig() {
    if (!confirm('确定要重置配置吗？')) return;
    abLoadConfig();
}

function abShowSaveStatus(msg) {
    const el = document.getElementById('ab-save-status');
    el.textContent = msg;
    el.style.opacity = '1';
    setTimeout(() => { el.style.opacity = '0'; }, 2000);
}

// ---------------------------------------------------------------------------
// Plugins Tab
// ---------------------------------------------------------------------------

async function abLoadPlugins() {
    const loading = document.getElementById('ab-plugins-loading');
    const list = document.getElementById('ab-plugins-list');
    loading.classList.remove('hidden');
    list.classList.add('hidden');
    try {
        const data = await abFetch('/api/agent/plugins');
        // API returns { categories: { cat_key: { display_name, plugins: [...] } } }
        // Flatten all plugins from all categories
        const allPlugins = [];
        const categories = data.categories || {};
        for (const [catKey, catData] of Object.entries(categories)) {
            for (const p of (catData.plugins || [])) {
                p._category = catData.display_name || catKey;
                allPlugins.push(p);
            }
        }
        AB.plugins = allPlugins;
        AB._pluginsLoaded = true;
        const enabled = AB.config?.plugins || [];
        list.innerHTML = AB.plugins.map(p => {
            const isOn = enabled.includes(p.name);
            const locked = !p.available;
            return `
                <div class="flex items-center justify-between p-4 rounded-xl border border-slate-200 dark:border-white/10
                            ${locked ? 'opacity-50' : ''} bg-white dark:bg-[#1A1A1A]">
                    <div class="flex items-center gap-3">
                        <div class="w-8 h-8 rounded-lg bg-primary-50 dark:bg-primary-900/20 flex items-center justify-center">
                            <i class="${abSafeClass(p.icon)} text-primary-500 text-xs"></i>
                        </div>
                        <div>
                            <div class="text-sm font-medium text-slate-700 dark:text-slate-200">${abEsc(p.name)}</div>
                            <div class="text-xs text-slate-400 dark:text-slate-500">${abEsc(p.description || '')}</div>
                        </div>
                    </div>
                    ${locked
                        ? `<span class="text-xs text-slate-400 dark:text-slate-500"><i class="fas fa-lock text-[10px] mr-1"></i>${abEsc(p.required_plan || 'enterprise')}</span>`
                        : `<label class="relative inline-flex items-center cursor-pointer">
                               <input type="checkbox" class="sr-only peer ab-plugin-toggle" data-plugin="${abEscAttr(p.name)}" ${isOn ? 'checked' : ''}>
                               <div class="w-9 h-5 bg-slate-200 dark:bg-slate-700 peer-checked:bg-primary-400 rounded-full
                                           after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white
                                           after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:after:translate-x-full"></div>
                           </label>`
                    }
                </div>`;
        }).join('');
        // Bind toggle events
        list.querySelectorAll('.ab-plugin-toggle').forEach(cb => {
            cb.addEventListener('change', abTogglePlugin);
        });
        loading.classList.add('hidden');
        list.classList.remove('hidden');
    } catch (e) {
        loading.textContent = '加载失败: ' + e.message;
    }
}

async function abTogglePlugin(e) {
    const name = e.target.dataset.plugin;
    const enabled = e.target.checked;
    // Collect all enabled plugins
    const currentPlugins = [];
    document.querySelectorAll('.ab-plugin-toggle:checked').forEach(cb => {
        currentPlugins.push(cb.dataset.plugin);
    });
    try {
        await abFetch('/api/agent/config', {
            method: 'PUT',
            body: JSON.stringify({ plugins: currentPlugins }),
        });
        AB.config.plugins = currentPlugins;
        abShowSaveStatus(enabled ? `已启用 ${name}` : `已禁用 ${name}`);
    } catch (err) {
        e.target.checked = !enabled; // revert
        abToast(err.message);
    }
}

// ---------------------------------------------------------------------------
// Knowledge Tab
// ---------------------------------------------------------------------------

async function abLoadKnowledge() {
    try {
        const data = await abFetch('/api/agent/knowledge');
        AB.knowledgeFiles = data.files || [];
        const limits = data.limits || {};
        const list = document.getElementById('ab-knowledge-list');
        const empty = document.getElementById('ab-knowledge-empty');
        const limitsEl = document.getElementById('ab-knowledge-limits');

        // Show limits
        if (limits.max_files !== undefined) {
            const maxFiles = limits.max_files === 0 ? 0 : (limits.max_files || '∞');
            const maxChars = limits.max_context_chars === 0 ? '0' : ((limits.max_context_chars || '∞'));
            limitsEl.innerHTML = `
                <span><i class="fas fa-file text-[10px] mr-1"></i>${AB.knowledgeFiles.length} / ${maxFiles} 文件</span>
                <span><i class="fas fa-font text-[10px] mr-1"></i>最大 ${maxChars} 字符</span>
            `;
        }

        if (AB.knowledgeFiles.length === 0) {
            list.innerHTML = '';
            empty.classList.remove('hidden');
            return;
        }
        empty.classList.add('hidden');

        // 使用 data-* 属性 + 事件委托，避免 onclick 中的 XSS
        list.innerHTML = AB.knowledgeFiles.map(f => {
            const statusIcon = f.status === 'completed' ? 'fa-check-circle text-green-500'
                : f.status === 'processing' ? 'fa-spinner fa-spin text-primary-500'
                : f.status === 'failed' ? 'fa-exclamation-circle text-red-500'
                : 'fa-clock text-slate-400';
            return `
                <div class="flex items-center justify-between p-3 rounded-lg border border-slate-100 dark:border-white/5
                            bg-slate-50 dark:bg-white/[0.02] hover:bg-slate-100 dark:hover:bg-white/[0.04] transition-colors">
                    <div class="flex items-center gap-3 min-w-0">
                        <i class="fas ${statusIcon} text-sm"></i>
                        <div class="min-w-0">
                            <div class="text-sm text-slate-700 dark:text-slate-200 truncate">${abEsc(f.filename)}</div>
                            <div class="text-xs text-slate-400 dark:text-slate-500">${abEsc(f.chunk_count || 0)} chunks · ${abEsc(f.file_size || 0)} bytes</div>
                        </div>
                    </div>
                    <div class="flex items-center gap-2">
                        ${f.status === 'failed' ? `<button class="ab-btn-reprocess text-xs text-primary-500 hover:text-primary-600 cursor-pointer" data-id="${abEscAttr(f.id)}">重试</button>` : ''}
                        <button class="ab-btn-delete-knowledge text-xs text-red-400 hover:text-red-500 cursor-pointer" data-id="${abEscAttr(f.id)}">
                            <i class="fas fa-trash text-[10px]"></i>
                        </button>
                    </div>
                </div>`;
        }).join('');

        // 事件委托绑定
        list.querySelectorAll('.ab-btn-reprocess').forEach(btn => {
            btn.addEventListener('click', () => abReprocessKnowledge(btn.dataset.id));
        });
        list.querySelectorAll('.ab-btn-delete-knowledge').forEach(btn => {
            btn.addEventListener('click', () => abDeleteKnowledge(btn.dataset.id));
        });
    } catch (e) {
        console.error('[AB] Load knowledge failed:', e);
        abToast('加载知识库失败');
    }
}

async function abUploadKnowledge(files) {
    for (const file of files) {
        const form = new FormData();
        form.append('file', file);
        try {
            const token = localStorage.getItem('cow_api_key') || '';
            const headers = {};
            if (token) headers['Authorization'] = `Bearer ${token}`;
            const resp = await fetch('/api/agent/knowledge/upload', {
                method: 'POST',
                headers,
                body: form,
            });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({ error: resp.statusText }));
                throw new Error(err.error || `HTTP ${resp.status}`);
            }
        } catch (e) {
            abToast(`上传 ${file.name} 失败: ${e.message}`);
        }
    }
    abLoadKnowledge();
}

async function abDeleteKnowledge(fileId) {
    if (!confirm('确定删除此知识文件？')) return;
    try {
        await abFetch(`/api/agent/knowledge/${fileId}`, { method: 'DELETE' });
        abLoadKnowledge();
    } catch (e) {
        abToast('删除失败: ' + e.message);
    }
}

async function abReprocessKnowledge(fileId) {
    try {
        await abFetch(`/api/agent/knowledge/${fileId}/reprocess`, { method: 'POST' });
        abLoadKnowledge();
    } catch (e) {
        abToast('重处理失败: ' + e.message);
    }
}

// ---------------------------------------------------------------------------
// Custom Tools Tab
// ---------------------------------------------------------------------------

async function abLoadTools() {
    try {
        const data = await abFetch('/api/agent/tools');
        AB.customTools = data.tools || [];
        AB.toolLimit = data.limit;
        const list = document.getElementById('ab-tools-list');
        const empty = document.getElementById('ab-tools-empty');
        const count = document.getElementById('ab-tools-count');

        const limitText = AB.toolLimit === -1 ? '∞' : AB.toolLimit;
        count.textContent = `${AB.customTools.length} / ${limitText}`;

        if (AB.customTools.length === 0) {
            list.innerHTML = '';
            empty.classList.remove('hidden');
            return;
        }
        empty.classList.add('hidden');

        // 使用 data-* 属性 + addEventListener，避免 onclick XSS
        list.innerHTML = AB.customTools.map(t => `
            <div class="p-4 rounded-xl border border-slate-200 dark:border-white/10 bg-white dark:bg-[#1A1A1A]">
                <div class="flex items-center justify-between mb-2">
                    <div class="flex items-center gap-2">
                        <code class="text-sm font-mono font-semibold text-primary-600 dark:text-primary-400">${abEsc(t.name)}</code>
                        <span class="px-1.5 py-0.5 rounded text-[10px] font-medium bg-slate-100 dark:bg-white/5 text-slate-500">${abEsc(t.execution?.method || 'GET')}</span>
                    </div>
                    <div class="flex items-center gap-2">
                        <button class="ab-btn-edit-tool text-xs text-slate-400 hover:text-primary-500 cursor-pointer" data-id="${abEscAttr(t.id)}">
                            <i class="fas fa-pen text-[10px]"></i>
                        </button>
                        <button class="ab-btn-delete-tool text-xs text-red-400 hover:text-red-500 cursor-pointer" data-id="${abEscAttr(t.id)}">
                            <i class="fas fa-trash text-[10px]"></i>
                        </button>
                    </div>
                </div>
                <p class="text-xs text-slate-500 dark:text-slate-400 mb-2">${abEsc(t.description)}</p>
                <div class="text-xs text-slate-400 dark:text-slate-500 font-mono truncate">${abEsc(t.execution?.url || '')}</div>
            </div>
        `).join('');

        // 事件委托
        list.querySelectorAll('.ab-btn-edit-tool').forEach(btn => {
            btn.addEventListener('click', () => abEditTool(btn.dataset.id));
        });
        list.querySelectorAll('.ab-btn-delete-tool').forEach(btn => {
            btn.addEventListener('click', () => abDeleteTool(btn.dataset.id));
        });
    } catch (e) {
        console.error('[AB] Load tools failed:', e);
        abToast('加载工具列表失败');
    }
}

function abOpenToolModal(toolId = null) {
    AB.editingToolId = toolId;
    const modal = document.getElementById('ab-tool-modal');
    const title = document.getElementById('ab-tool-modal-title');
    title.textContent = toolId ? '编辑工具' : '添加工具';

    if (toolId) {
        const tool = AB.customTools.find(t => t.id === toolId);
        if (tool) {
            document.getElementById('ab-tool-name').value = tool.name || '';
            document.getElementById('ab-tool-desc').value = tool.description || '';
            document.getElementById('ab-tool-url').value = tool.execution?.url || '';
            document.getElementById('ab-tool-method').value = tool.execution?.method || 'GET';
            document.getElementById('ab-tool-response-path').value = tool.execution?.response_path || '';
            document.getElementById('ab-tool-params').value = tool.parameters
                ? JSON.stringify(tool.parameters, null, 2) : '';
            document.getElementById('ab-tool-headers').value = tool.execution?.headers
                ? JSON.stringify(tool.execution.headers, null, 2) : '';
        }
    } else {
        document.getElementById('ab-tool-name').value = '';
        document.getElementById('ab-tool-desc').value = '';
        document.getElementById('ab-tool-url').value = '';
        document.getElementById('ab-tool-method').value = 'GET';
        document.getElementById('ab-tool-response-path').value = '';
        document.getElementById('ab-tool-params').value = '';
        document.getElementById('ab-tool-headers').value = '';
    }
    modal.classList.remove('hidden');
    // 焦点移到第一个输入框
    setTimeout(() => document.getElementById('ab-tool-name')?.focus(), 100);
}

function abCloseToolModal() {
    document.getElementById('ab-tool-modal').classList.add('hidden');
    AB.editingToolId = null;
}

function abEditTool(toolId) {
    abOpenToolModal(toolId);
}

async function abSaveTool() {
    if (AB._saving) return;
    const name = document.getElementById('ab-tool-name').value.trim();
    const desc = document.getElementById('ab-tool-desc').value.trim();
    const url = document.getElementById('ab-tool-url').value.trim();
    const method = document.getElementById('ab-tool-method').value;
    const responsePath = document.getElementById('ab-tool-response-path').value.trim();
    const paramsStr = document.getElementById('ab-tool-params').value.trim();
    const headersStr = document.getElementById('ab-tool-headers').value.trim();

    if (!name || !desc || !url) {
        abToast('Name, Description and URL are required');
        return;
    }

    // 验证工具名格式
    if (!/^[a-zA-Z][a-zA-Z0-9_]*$/.test(name)) {
        abToast('Tool name must start with a letter and contain only letters, numbers, and underscores');
        return;
    }

    let parameters = { type: 'object', properties: {} };
    if (paramsStr) {
        try { parameters = JSON.parse(paramsStr); }
        catch { abToast('Parameters must be valid JSON'); return; }
    }

    let headers = {};
    if (headersStr) {
        try { headers = JSON.parse(headersStr); }
        catch { abToast('Headers must be valid JSON'); return; }
    }

    const toolDef = {
        name,
        description: desc,
        parameters,
        execution: {
            type: 'http',
            url,
            method,
            headers,
            response_path: responsePath,
        },
    };

    // 保存 editingToolId 到局部变量（abCloseToolModal 会清空它）
    const isEdit = !!AB.editingToolId;
    const editId = AB.editingToolId;

    AB._saving = true;
    try {
        if (isEdit) {
            await abFetch(`/api/agent/tools/${editId}`, {
                method: 'PUT',
                body: JSON.stringify(toolDef),
            });
        } else {
            await abFetch('/api/agent/tools', {
                method: 'POST',
                body: JSON.stringify(toolDef),
            });
        }
        abCloseToolModal();
        abLoadTools();
        abShowSaveStatus(isEdit ? '工具已更新' : '工具已添加');
    } catch (e) {
        abToast('保存失败: ' + e.message);
    } finally {
        AB._saving = false;
    }
}

async function abDeleteTool(toolId) {
    if (!confirm('确定删除此工具？')) return;
    try {
        await abFetch(`/api/agent/tools/${toolId}`, { method: 'DELETE' });
        abLoadTools();
    } catch (e) {
        abToast('删除失败: ' + e.message);
    }
}

// ---------------------------------------------------------------------------
// Preview & Test
// ---------------------------------------------------------------------------

function abRenderPreview() {
    const summaryEl = document.getElementById('ab-preview-summary');
    if (!summaryEl) return;

    const cfg = AB.config || {};
    const items = [
        { label: '名称', value: cfg.name || '-' },
        { label: '模型', value: cfg.model || '-' },
        { label: '温度', value: cfg.temperature ?? '-' },
        { label: '最大步数', value: cfg.max_steps ?? '-' },
        { label: '系统提示词', value: (cfg.system_prompt || '-').substring(0, 200) + ((cfg.system_prompt || '').length > 200 ? '...' : '') },
        { label: '插件', value: (cfg.plugins && cfg.plugins.length) ? cfg.plugins.join(', ') : '-' },
        { label: '知识文件', value: (cfg.knowledge_ids && cfg.knowledge_ids.length) ? cfg.knowledge_ids.length + ' 个' : '-' },
        { label: '自定义工具', value: AB.tools ? AB.tools.length + ' 个' : '-' },
    ];

    summaryEl.innerHTML = items.map(it =>
        `<div class="flex justify-between py-1.5 border-b border-slate-100 dark:border-white/5 last:border-0">
            <span class="text-slate-500 dark:text-slate-400">${abEsc(it.label)}</span>
            <span class="text-slate-800 dark:text-slate-200 text-right max-w-[60%] truncate">${abEsc(String(it.value))}</span>
        </div>`
    ).join('');
}

async function abSendPreviewMessage() {
    const input = document.getElementById('ab-preview-input');
    const chatEl = document.getElementById('ab-preview-chat');
    if (!input || !chatEl) return;

    const msg = input.value.trim();
    if (!msg) return;
    input.value = '';

    // 显示用户消息
    chatEl.innerHTML += `<div class="flex justify-end"><div class="bg-primary-500 text-white px-3 py-1.5 rounded-xl rounded-br-sm text-sm max-w-[80%]">${abEsc(msg)}</div></div>`;

    // 显示加载中
    const loadingId = 'ab-preview-loading-' + Date.now();
    chatEl.innerHTML += `<div id="${loadingId}" class="flex justify-start"><div class="bg-slate-100 dark:bg-white/10 px-3 py-1.5 rounded-xl rounded-bl-sm text-sm text-slate-400"><i class="fas fa-spinner fa-spin"></i></div></div>`;
    chatEl.scrollTop = chatEl.scrollHeight;

    try {
        const res = await abFetch('/api/agent/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: msg }),
        });
        const data = await res.json();
        const loadingEl = document.getElementById(loadingId);
        if (loadingEl) {
            const reply = data.content || data.message || data.error || '无响应';
            loadingEl.outerHTML = `<div class="flex justify-start"><div class="bg-slate-100 dark:bg-white/10 px-3 py-1.5 rounded-xl rounded-bl-sm text-sm text-slate-700 dark:text-slate-200 max-w-[80%] whitespace-pre-wrap">${abEsc(reply)}</div></div>`;
        }
    } catch (e) {
        const loadingEl = document.getElementById(loadingId);
        if (loadingEl) {
            loadingEl.outerHTML = `<div class="flex justify-start"><div class="bg-red-50 dark:bg-red-900/20 px-3 py-1.5 rounded-xl rounded-bl-sm text-sm text-red-500">请求失败</div></div>`;
        }
    }
    chatEl.scrollTop = chatEl.scrollHeight;
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

function abInit() {
    // 防止重复绑定事件
    if (AB._initialized) {
        abLoadConfig();
        abSwitchTab('basic');
        return;
    }
    AB._initialized = true;

    // Tab bar event delegation
    const tabBar = document.getElementById('ab-tab-bar');
    if (tabBar) {
        tabBar.addEventListener('click', (e) => {
            const tab = e.target.closest('.ab-tab');
            if (tab && tab.dataset.tab) abSwitchTab(tab.dataset.tab);
        });
    }
    // File upload handler
    const fileInput = document.getElementById('ab-knowledge-file-input');
    if (fileInput) {
        fileInput.addEventListener('change', (e) => {
            if (e.target.files.length > 0) abUploadKnowledge(e.target.files);
            e.target.value = '';
        });
    }
    // Drag & drop
    const uploadArea = document.getElementById('ab-knowledge-upload');
    if (uploadArea) {
        uploadArea.addEventListener('dragover', (e) => {
            e.preventDefault();
            uploadArea.classList.add('border-primary-400', 'dark:border-primary-500');
        });
        uploadArea.addEventListener('dragleave', () => {
            uploadArea.classList.remove('border-primary-400', 'dark:border-primary-500');
        });
        uploadArea.addEventListener('drop', (e) => {
            e.preventDefault();
            uploadArea.classList.remove('border-primary-400', 'dark:border-primary-500');
            if (e.dataTransfer.files.length > 0) abUploadKnowledge(e.dataTransfer.files);
        });
    }
    // Esc 关闭模态框
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && !document.getElementById('ab-tool-modal')?.classList.contains('hidden')) {
            abCloseToolModal();
        }
    });
    // Preview send button & enter key
    const previewSendBtn = document.getElementById('ab-preview-send');
    if (previewSendBtn) previewSendBtn.addEventListener('click', abSendPreviewMessage);
    const previewInput = document.getElementById('ab-preview-input');
    if (previewInput) previewInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); abSendPreviewMessage(); }
    });
    // Load config
    abLoadConfig();
    // Activate default tab
    abSwitchTab('basic');
}

// Expose to global
window.abSwitchTab = abSwitchTab;
window.abSaveConfig = abSaveConfig;
window.abResetConfig = abResetConfig;
window.abOpenToolModal = abOpenToolModal;
window.abCloseToolModal = abCloseToolModal;
window.abSaveTool = abSaveTool;
window.abEditTool = abEditTool;
window.abDeleteTool = abDeleteTool;
window.abDeleteKnowledge = abDeleteKnowledge;
window.abReprocessKnowledge = abReprocessKnowledge;
window.abInit = abInit;
