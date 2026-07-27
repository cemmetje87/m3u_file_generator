const searchUrlInput = document.getElementById('search-url');
const btnScrape = document.getElementById('btn-scrape');
const btnProcess = document.getElementById('btn-process');
const btnDownload = document.getElementById('btn-download');
const btnClearLogs = document.getElementById('btn-clear-logs');
const btnEditConfig = document.getElementById('btn-edit-config');
const logsContainer = document.getElementById('logs');
const statusDot = document.getElementById('connection-status');
const statusText = document.getElementById('status-text');

// Modal Elements
const configModal = document.getElementById('config-modal');
const closeModalBtn = document.querySelector('.close-modal');
const btnCancelConfig = document.getElementById('btn-cancel-config');
const btnSaveConfig = document.getElementById('btn-save-config');
const configFileSelect = document.getElementById('config-file-select');
const configEditor = document.getElementById('config-editor');
const configError = document.getElementById('config-error');

// API Key Modal Elements
const apiKeyModal = document.getElementById('apikey-modal');
const apiKeyInput = document.getElementById('apikey-input');
const apiKeyError = document.getElementById('apikey-error');
const btnSubmitApiKey = document.getElementById('btn-submit-apikey');
const btnCancelApiKey = document.getElementById('btn-cancel-apikey');

let socket;
let reconnectInterval;

// --- API Key Handling ---
// Key lives in sessionStorage (per-tab); sent as X-API-Key header / ?api_key= param.

function getApiKey() {
    return sessionStorage.getItem('iptv_api_key') || '';
}

// Resolves with the entered key, or '' if the user cancels.
function promptForApiKey() {
    return new Promise((resolve) => {
        apiKeyInput.value = '';
        apiKeyError.textContent = '';
        apiKeyModal.style.display = 'flex';
        apiKeyInput.focus();

        const cleanup = () => {
            apiKeyModal.style.display = 'none';
            btnSubmitApiKey.removeEventListener('click', onSubmit);
            btnCancelApiKey.removeEventListener('click', onCancel);
            apiKeyInput.removeEventListener('keydown', onKeydown);
        };

        const onSubmit = () => {
            const value = apiKeyInput.value.trim();
            if (!value) {
                apiKeyError.textContent = 'Enter a key to continue.';
                return;
            }
            cleanup();
            resolve(value);
        };

        const onCancel = () => {
            cleanup();
            resolve('');
        };

        const onKeydown = (event) => {
            if (event.key === 'Enter') {
                event.preventDefault();
                onSubmit();
            } else if (event.key === 'Escape') {
                event.preventDefault();
                onCancel();
            }
        };

        btnSubmitApiKey.addEventListener('click', onSubmit);
        btnCancelApiKey.addEventListener('click', onCancel);
        apiKeyInput.addEventListener('keydown', onKeydown);
    });
}

// Several callers can hit a 403 at once; share one prompt between them so we
// never stack modals.
let pendingApiKeyPrompt = null;

function ensureApiKey() {
    const existing = getApiKey();
    if (existing) {
        return Promise.resolve(existing);
    }
    if (!pendingApiKeyPrompt) {
        pendingApiKeyPrompt = promptForApiKey().then((key) => {
            if (key) {
                sessionStorage.setItem('iptv_api_key', key);
            }
            pendingApiKeyPrompt = null;
            return key;
        });
    }
    return pendingApiKeyPrompt;
}

function authHeaders(extra = {}) {
    const headers = { ...extra };
    const key = getApiKey();
    if (key) {
        headers['X-API-Key'] = key;
    }
    return headers;
}

// --- WebSocket & Logging Logic ---

function setConnectionStatus(connected) {
    if (connected) {
        statusDot.classList.add('connected');
        statusText.textContent = 'Connected';
        statusText.style.color = 'var(--accent-success)';
    } else {
        statusDot.classList.remove('connected');
        statusText.textContent = 'Disconnected';
        statusText.style.color = 'var(--text-secondary)';
    }
}

function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    let wsUrl = `${protocol}//${window.location.host}/ws/logs`;
    const key = getApiKey();
    if (key) {
        wsUrl += `?api_key=${encodeURIComponent(key)}`;
    }

    socket = new WebSocket(wsUrl);

    socket.onopen = () => {
        setConnectionStatus(true);
        log("System: Connected to log stream.", "log-system");
        clearInterval(reconnectInterval);
    };

    socket.onmessage = (event) => {
        const msg = event.data;
        log(msg, consumeJobTerminalMessage(msg) || classifyLogLine(msg));
    };

    socket.onclose = async (event) => {
        setConnectionStatus(false);
        if (event.code === 4401) {
            // Server requires an API key we didn't provide (or it was wrong).
            sessionStorage.removeItem('iptv_api_key');
            if (await ensureApiKey()) {
                log("System: API key updated. Reconnecting...", "log-system");
                connectWebSocket();
            } else {
                log("System: API key required. Reload the page to try again.", "log-error");
            }
            return;
        }
        // The log stream is the only channel that reports job completion, so
        // once it drops we can no longer tell when the job ends. Release the
        // UI rather than wedging it; a premature retry just gets a 409.
        if (activeJob) {
            log("System: Log stream lost while a task was running. The task may still be running on the server.", "log-warning");
            setIdle();
        }
        log("System: Disconnected. Reconnecting in 3s...", "log-error");
        if (!reconnectInterval) {
            reconnectInterval = setInterval(connectWebSocket, 3000);
        }
    };
    
    socket.onerror = (err) => {
         console.error("WebSocket error:", err);
         socket.close();
    }
}

function log(message, className = "") {
    const line = document.createElement('div');
    line.className = `log-line ${className}`;
    line.textContent = message;
    logsContainer.appendChild(line);
    logsContainer.scrollTop = logsContainer.scrollHeight;
}

// --- Job State ---
// POSTing to /api/scrape or /api/process only *starts* a background task; the
// server reports completion later over the log stream. The UI therefore stays
// busy from the POST until a terminal log line arrives, not until the POST
// returns.

// Terminal lines emitted by run_script() in main.py. Anchored so a subprocess
// echoing similar text mid-run can't end the job early.
const JOB_FINISHED_RE = /^Process finished with exit code (-?\d+)$/;
const JOB_CRASHED_PREFIX = 'CRITICAL ERROR: script execution failed';

const JOB_BUTTONS = { scrape: btnScrape, process: btnProcess };

let activeJob = null; // 'scrape' | 'process' | null

function setBusy(job) {
    activeJob = job;
    btnScrape.disabled = true;
    btnProcess.disabled = true;
    btnEditConfig.disabled = true;

    const btn = JOB_BUTTONS[job];
    // Guard against overwriting the stored label if setBusy runs twice for one
    // job (the 403 retry path re-enters).
    if (btn && !btn.dataset.idleHtml) {
        btn.dataset.idleHtml = btn.innerHTML;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Running...';
    }
}

function setIdle() {
    activeJob = null;
    btnScrape.disabled = false;
    btnProcess.disabled = false;
    btnEditConfig.disabled = false;

    Object.values(JOB_BUTTONS).forEach((btn) => {
        if (btn.dataset.idleHtml) {
            btn.innerHTML = btn.dataset.idleHtml;
            delete btn.dataset.idleHtml;
        }
    });
}

// Returns a log class if this line ended the job, otherwise null.
function consumeJobTerminalMessage(msg) {
    const trimmed = msg.trim();

    const finished = JOB_FINISHED_RE.exec(trimmed);
    if (finished) {
        const exitCode = Number(finished[1]);
        // Only a successful processing run writes master_iptv.m3u; a scrape
        // produces the URL list, and a non-zero exit produces nothing.
        const wasProcess = activeJob === 'process';
        setIdle();
        if (exitCode !== 0) {
            return "log-error";
        }
        if (wasProcess) {
            btnDownload.disabled = false;
        }
        return "log-success";
    }

    if (trimmed.startsWith(JOB_CRASHED_PREFIX)) {
        setIdle();
        return "log-error";
    }

    return null;
}

function classifyLogLine(msg) {
    const trimmed = msg.trim();
    const lowerMsg = trimmed.toLowerCase();

    if (lowerMsg.includes("error") || lowerMsg.includes("exception") || lowerMsg.includes("failed") || lowerMsg.includes("critical")) {
        return "log-error";
    }
    if (lowerMsg.includes("warning")) {
        return "log-warning";
    }
    if (lowerMsg.includes("success") || lowerMsg.includes("completed") || lowerMsg.includes("finished") || lowerMsg.includes("saved")) {
        return "log-success";
    }
    if (lowerMsg.includes("processing") || lowerMsg.includes("fetching") || lowerMsg.includes("starting")) {
        return "log-info";
    }
    if (trimmed.startsWith("http") || trimmed.startsWith("/")) {
        return "log-dim";
    }
    return "";
}

async function triggerAction(job, endpoint, body = {}) {
    setBusy(job);
    try {
        const response = await fetch(endpoint, {
            method: 'POST',
            headers: authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(body)
        });

        if (response.ok) {
            // Task accepted and now running server-side. Stay busy; the log
            // stream will release the UI.
            return;
        }

        if (response.status === 403) {
            log("API key missing or invalid.", "log-error");
            sessionStorage.removeItem('iptv_api_key');
            if (await ensureApiKey()) {
                return triggerAction(job, endpoint, body);
            }
        } else if (response.status === 409) {
            log("A task is already running. Wait for it to finish.", "log-warning");
        } else {
            log(`Error starting task: ${response.statusText}`, "log-error");
        }
    } catch (error) {
        log(`Network error: ${error.message}`, "log-error");
    }

    // Only reached when the task never started.
    setIdle();
}

// --- Configuration Logic ---

async function loadConfigFiles() {
    try {
        const res = await fetch('/api/configs', { headers: authHeaders() });
        if (!res.ok) throw new Error('Failed to list configs');
        const data = await res.json();
        
        configFileSelect.innerHTML = '';
        data.files.forEach(file => {
            const option = document.createElement('option');
            option.value = file;
            option.textContent = file;
            configFileSelect.appendChild(option);
        });
        
        if (data.files.length > 0) {
            loadConfigContent(data.files[0]);
        }
    } catch (err) {
        log(`Error loading config list: ${err.message}`, "log-error");
    }
}

async function loadConfigContent(filename) {
    configEditor.value = "Loading...";
    configEditor.disabled = true;
    try {
        const res = await fetch(`/api/config/${filename}`, { headers: authHeaders() });
        if (res.status === 403) {
            sessionStorage.removeItem('iptv_api_key');
            if (await ensureApiKey()) return loadConfigContent(filename);
            throw new Error('API key required');
        }
        if (!res.ok) throw new Error('Failed to load config content');
        const json = await res.json();
        configEditor.value = JSON.stringify(json, null, 2);
    } catch (err) {
        configEditor.value = `Error: ${err.message}`;
        log(`Error loading config content: ${err.message}`, "log-error");
    } finally {
        configEditor.disabled = false;
    }
}

async function saveConfig() {
    const filename = configFileSelect.value;
    const contentStr = configEditor.value;

    configError.textContent = '';

    try {
        // Validate JSON
        const content = JSON.parse(contentStr);

        btnSaveConfig.disabled = true;
        btnSaveConfig.textContent = "Saving...";

        const res = await fetch(`/api/config/${filename}`, {
            method: 'POST',
            headers: authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ content: content })
        });

        if (res.status === 403) {
            sessionStorage.removeItem('iptv_api_key');
            if (await ensureApiKey()) return saveConfig();
            throw new Error('API key required');
        }
        if (!res.ok) throw new Error(await res.text());

        log(`Configuration saved successfully: ${filename}`, "log-success");
        closeModal();

    } catch (err) {
        configError.textContent = `Failed to save: ${err.message}`;
        log(`Config save failed: ${err.message}`, "log-error");
    } finally {
        btnSaveConfig.disabled = false;
        btnSaveConfig.textContent = "Save Changes";
    }
}

function openModal() {
    configError.textContent = '';
    configModal.style.display = "flex";
    loadConfigFiles();
}

function closeModal() {
    configModal.style.display = "none";
    configError.textContent = '';
}

// --- Event Listeners ---

btnScrape.addEventListener('click', () => {
    const url = searchUrlInput.value;
    if (!url) {
        log("Please enter a valid URL.", "log-warning");
        return;
    }
    log(`> Initiating Scrape: ${url}`, "log-info");
    triggerAction('scrape', '/api/scrape', { url: url });
});

btnProcess.addEventListener('click', () => {
    log(`> Initiating Processing...`, "log-info");
    triggerAction('process', '/api/process', { input_file: 'iptv_m3u_urls.txt' });
});

btnDownload.addEventListener('click', () => {
    window.location.href = '/api/download';
});

btnClearLogs.addEventListener('click', () => {
    logsContainer.innerHTML = '';
    log("Logs cleared.", "log-system");
});

btnEditConfig.addEventListener('click', openModal);
closeModalBtn.addEventListener('click', closeModal);
btnCancelConfig.addEventListener('click', closeModal);
btnSaveConfig.addEventListener('click', saveConfig);
configFileSelect.addEventListener('change', (e) => loadConfigContent(e.target.value));

window.onclick = function(event) {
    if (event.target == configModal) {
        closeModal();
    }
}

// Initial connection
connectWebSocket();