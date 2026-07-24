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

let socket;
let reconnectInterval;

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
    const wsUrl = `${protocol}//${window.location.host}/ws/logs`;
    
    socket = new WebSocket(wsUrl);

    socket.onopen = () => {
        setConnectionStatus(true);
        log("System: Connected to log stream.", "log-system");
        clearInterval(reconnectInterval);
    };

    socket.onmessage = (event) => {
        const msg = event.data;
        let className = "";
        
        const lowerMsg = msg.toLowerCase();
        
        if (lowerMsg.includes("error") || lowerMsg.includes("exception") || lowerMsg.includes("failed") || lowerMsg.includes("critical")) {
            className = "log-error";
        } else if (lowerMsg.includes("warning")) {
            className = "log-warning";
        } else if (lowerMsg.includes("success") || lowerMsg.includes("completed") || lowerMsg.includes("finished") || lowerMsg.includes("saved")) {
            className = "log-success";
            if (msg.includes("Process finished")) {
                setLoading(false);
                 btnDownload.disabled = false;
            }
        } else if (lowerMsg.includes("processing") || lowerMsg.includes("fetching") || lowerMsg.includes("starting")) {
            className = "log-info";
        } else if (msg.trim().startsWith("http") || msg.trim().startsWith("/")) {
             className = "log-dim";
        }

        log(msg, className);
    };

    socket.onclose = () => {
        setConnectionStatus(false);
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

function setLoading(isLoading) {
    if (isLoading) {
        btnScrape.disabled = true;
        btnProcess.disabled = true;
        btnEditConfig.disabled = true;
        document.body.style.cursor = 'wait';
    } else {
        btnScrape.disabled = false;
        btnProcess.disabled = false;
        btnEditConfig.disabled = false;
        document.body.style.cursor = 'default';
    }
}

async function triggerAction(endpoint, body = {}) {
    setLoading(true);
    try {
        const response = await fetch(endpoint, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(body)
        });
        
        if (!response.ok) {
            log(`Error starting task: ${response.statusText}`, "log-error");
            setLoading(false);
        }
    } catch (error) {
        log(`Network error: ${error.message}`, "log-error");
        setLoading(false);
    }
}

// --- Configuration Logic ---

async function loadConfigFiles() {
    try {
        const res = await fetch('/api/configs');
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
        const res = await fetch(`/api/config/${filename}`);
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
    
    try {
        // Validate JSON
        const content = JSON.parse(contentStr);
        
        btnSaveConfig.disabled = true;
        btnSaveConfig.textContent = "Saving...";
        
        const res = await fetch(`/api/config/${filename}`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ content: content })
        });
        
        if (!res.ok) throw new Error(await res.text());
        
        log(`Configuration saved successfully: ${filename}`, "log-success");
        closeModal();
        
    } catch (err) {
        alert(`Failed to save: ${err.message}`); // Alert for modal error
        log(`Config save failed: ${err.message}`, "log-error");
    } finally {
        btnSaveConfig.disabled = false;
        btnSaveConfig.textContent = "Save Changes";
    }
}

function openModal() {
    configModal.style.display = "flex";
    loadConfigFiles();
}

function closeModal() {
    configModal.style.display = "none";
}

// --- Event Listeners ---

btnScrape.addEventListener('click', () => {
    const url = searchUrlInput.value;
    if (!url) {
        log("Please enter a valid URL.", "log-warning");
        return;
    }
    log(`> Initiating Scrape: ${url}`, "log-info");
    triggerAction('/api/scrape', { url: url });
});

btnProcess.addEventListener('click', () => {
    log(`> Initiating Processing...`, "log-info");
    triggerAction('/api/process', { input_file: 'iptv_m3u_urls.txt' }); 
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