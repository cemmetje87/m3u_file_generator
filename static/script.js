/* M3U Engine — console behaviour.
 *
 * Three things happen here:
 *   1. The log websocket, which is the only channel that reports job
 *      completion, so it also drives the stage lamps.
 *   2. Readouts, half of which come from parsing the log stream as it
 *      arrives and half from /api/status (which knows what is on disk).
 *   3. The filter rule builder, a two-way view over m3u_filter_config.json.
 */

const $ = (id) => document.getElementById(id);

const sourceUrlInput = $('source-url');
const btnScrape = $('btn-scrape');
const btnProcess = $('btn-process');
const btnDownload = $('btn-download');
const btnClearLogs = $('btn-clear-logs');
const btnEditConfig = $('btn-edit-config');
const logsContainer = $('logs');
const consoleEmpty = $('console-empty');
const consoleCount = $('console-count');
const linkLamp = $('link-lamp');
const linkLabel = $('link-label');
const markGlyph = document.querySelector('.mark-glyph');

const modules = {
    acquire: $('mod-acquire'),
    filter: $('mod-filter'),
    master: $('mod-master'),
};
const stateLabels = {
    acquire: $('state-acquire'),
    filter: $('state-filter'),
    master: $('state-master'),
};
const patches = document.querySelectorAll('.patch');

const roFeeds = $('ro-feeds');
const roRules = $('ro-rules');
const roChannels = $('ro-channels');
const roSize = $('ro-size');
const roStamp = $('ro-stamp');

// Modals
const configModal = $('config-modal');
const apiKeyModal = $('apikey-modal');
const configFileBar = $('config-file-bar');
const configFileSelect = $('config-file-select');
const configEditor = $('config-editor');
const configError = $('config-error');
const btnSaveConfig = $('btn-save-config');
const btnCancelConfig = $('btn-cancel-config');
const closeModalBtn = document.querySelector('.close-modal');

const apiKeyInput = $('apikey-input');
const apiKeyError = $('apikey-error');
const btnSubmitApiKey = $('btn-submit-apikey');
const btnCancelApiKey = $('btn-cancel-apikey');

// Builder
const paneBuild = $('pane-build');
const paneJson = $('pane-json');
const tabs = document.querySelectorAll('.tab');
const ruleList = $('rule-list');
const ruleEmpty = $('rule-empty');
const btnAddRule = $('btn-add-rule');
const setTopFastest = $('set-top-fastest');
const setErrorThreshold = $('set-error-threshold');
const pairWarning = $('pair-warning');
const vocabNote = $('vocab-note');
const btnScanVocab = $('btn-scan-vocab');

let socket;
let reconnectInterval;

/* ---------------- API key ----------------
 * Key lives in sessionStorage (per tab); sent as X-API-Key, or ?api_key=
 * on the websocket. */

function getApiKey() {
    return sessionStorage.getItem('iptv_api_key') || '';
}

// Resolves with the entered key, or '' if the operator cancels.
function promptForApiKey() {
    return new Promise((resolve) => {
        apiKeyInput.value = '';
        apiKeyError.textContent = '';
        apiKeyModal.classList.add('is-open');
        apiKeyInput.focus();

        const cleanup = () => {
            apiKeyModal.classList.remove('is-open');
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

        const onCancel = () => { cleanup(); resolve(''); };

        const onKeydown = (event) => {
            if (event.key === 'Enter') { event.preventDefault(); onSubmit(); }
            else if (event.key === 'Escape') { event.preventDefault(); onCancel(); }
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
    if (existing) return Promise.resolve(existing);

    if (!pendingApiKeyPrompt) {
        pendingApiKeyPrompt = promptForApiKey().then((key) => {
            if (key) sessionStorage.setItem('iptv_api_key', key);
            pendingApiKeyPrompt = null;
            return key;
        });
    }
    return pendingApiKeyPrompt;
}

function authHeaders(extra = {}) {
    const headers = { ...extra };
    const key = getApiKey();
    if (key) headers['X-API-Key'] = key;
    return headers;
}

/* ---------------- Console ---------------- */

function stamp() {
    return new Date().toTimeString().slice(0, 8);
}

let lineCount = 0;

function log(message, className = '') {
    if (consoleEmpty) consoleEmpty.hidden = true;

    const line = document.createElement('div');
    line.className = `log-line ${className}`.trim();

    const ts = document.createElement('span');
    ts.className = 'log-ts';
    ts.textContent = stamp();

    const msg = document.createElement('span');
    msg.className = 'log-msg';
    msg.textContent = message;

    line.append(ts, msg);
    logsContainer.appendChild(line);
    logsContainer.scrollTop = logsContainer.scrollHeight;

    lineCount += 1;
    consoleCount.textContent = `${lineCount} line${lineCount === 1 ? '' : 's'}`;
}

function clearConsole() {
    logsContainer.replaceChildren(consoleEmpty);
    consoleEmpty.hidden = false;
    lineCount = 0;
    consoleCount.textContent = '';
}

/* ---------------- Stage lamps ----------------
 * A patch cable carries the signal out of the stage above it, so its flow
 * mirrors that stage's state rather than having a state of its own. */

const STAGE_TEXT = {
    standby: 'Standby',
    running: 'Running',
    done: 'Done',
    fault: 'Fault',
    empty: 'Empty',
    ready: 'Ready',
};

const stageState = { acquire: 'standby', filter: 'standby', master: 'empty' };

function setStage(stage, state) {
    stageState[stage] = state;
    modules[stage].dataset.state = state;
    stateLabels[stage].textContent = STAGE_TEXT[state];
    syncPatches();
}

function flowFor(state) {
    if (state === 'running') return 'on';
    if (state === 'done') return 'done';
    return 'off';
}

function syncPatches() {
    patches[0].dataset.flow = flowFor(stageState.acquire);
    patches[1].dataset.flow = flowFor(stageState.filter);

    const busy = stageState.acquire === 'running' || stageState.filter === 'running';
    markGlyph.classList.toggle('is-live', busy);
}

/* ---------------- Connection ---------------- */

function setConnectionStatus(connected) {
    linkLamp.dataset.state = connected ? 'up' : 'down';
    linkLabel.textContent = connected ? 'Link up' : 'Link down';
}

function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    let wsUrl = `${protocol}//${window.location.host}/ws/logs`;
    const key = getApiKey();
    if (key) wsUrl += `?api_key=${encodeURIComponent(key)}`;

    socket = new WebSocket(wsUrl);

    socket.onopen = () => {
        setConnectionStatus(true);
        log('Connected to the log stream.', 'log-system');
        clearInterval(reconnectInterval);
        reconnectInterval = undefined;
        refreshStatus();
        refreshRuleCount();
    };

    socket.onmessage = (event) => {
        const msg = event.data;
        readMetrics(msg);
        log(msg, consumeJobTerminalMessage(msg) || classifyLogLine(msg));
    };

    socket.onclose = async (event) => {
        setConnectionStatus(false);

        if (event.code === 4401) {
            // Server wants a key we didn't send (or sent a wrong one).
            sessionStorage.removeItem('iptv_api_key');
            if (await ensureApiKey()) {
                log('Key accepted. Reconnecting.', 'log-system');
                connectWebSocket();
            } else {
                log('An API key is required. Reload the page to enter one.', 'log-error');
            }
            return;
        }

        // The log stream is the only channel that reports job completion, so
        // once it drops we can no longer tell when a job ends. Release the UI
        // rather than wedging it; a premature retry just gets a 409.
        if (activeJob) {
            log('Log stream dropped mid-run. The job may still be running on the server.', 'log-warning');
            setIdle();
            setStage(activeJobStage(), 'standby');
        }
        log('Link down. Retrying in 3s.', 'log-error');
        if (!reconnectInterval) {
            reconnectInterval = setInterval(connectWebSocket, 3000);
        }
    };

    socket.onerror = (err) => {
        console.error('WebSocket error:', err);
        socket.close();
    };
}

/* ---------------- Job state ----------------
 * POSTing to /api/scrape or /api/process only *starts* a background task;
 * the server reports completion later over the log stream. The UI therefore
 * stays busy from the POST until a terminal log line arrives, not until the
 * POST returns. */

// Terminal lines emitted by run_script() in main.py. Anchored so a subprocess
// echoing similar text mid-run can't end the job early.
const JOB_FINISHED_RE = /^Process finished with exit code (-?\d+)$/;
const JOB_CRASHED_PREFIX = 'CRITICAL ERROR: script execution failed';

const JOB_BUTTONS = { scrape: btnScrape, process: btnProcess };
const JOB_STAGES = { scrape: 'acquire', process: 'filter' };

let activeJob = null; // 'scrape' | 'process' | null

function activeJobStage() {
    return JOB_STAGES[activeJob] || 'acquire';
}

function setBusy(job) {
    activeJob = job;
    btnScrape.disabled = true;
    btnProcess.disabled = true;
    btnEditConfig.disabled = true;

    const btn = JOB_BUTTONS[job];
    const label = btn && btn.querySelector('.btn-label');
    // Guard against overwriting the stored label if setBusy runs twice for one
    // job (the 403 retry path re-enters).
    if (label && !btn.dataset.idleLabel) {
        btn.dataset.idleLabel = label.textContent;
        label.textContent = 'Running';
        btn.classList.add('is-busy');
    }
    setStage(JOB_STAGES[job], 'running');
}

function setIdle() {
    activeJob = null;
    btnScrape.disabled = false;
    btnProcess.disabled = false;
    btnEditConfig.disabled = false;

    Object.values(JOB_BUTTONS).forEach((btn) => {
        if (btn.dataset.idleLabel) {
            btn.querySelector('.btn-label').textContent = btn.dataset.idleLabel;
            delete btn.dataset.idleLabel;
            btn.classList.remove('is-busy');
        }
    });
}

// Returns a log class if this line ended the job, otherwise null.
function consumeJobTerminalMessage(msg) {
    const trimmed = msg.trim();

    const finished = JOB_FINISHED_RE.exec(trimmed);
    if (finished) {
        const exitCode = Number(finished[1]);
        // Only a successful filter run writes master_iptv.m3u; an acquire run
        // produces the feed list, and a non-zero exit produces nothing.
        const wasProcess = activeJob === 'process';
        const stage = activeJobStage();
        setIdle();

        if (exitCode !== 0) {
            setStage(stage, 'fault');
            return 'log-error';
        }
        setStage(stage, 'done');
        if (wasProcess) {
            btnDownload.disabled = false;
            setStage('master', 'ready');
        }
        refreshStatus();
        return 'log-success';
    }

    if (trimmed.startsWith(JOB_CRASHED_PREFIX)) {
        const stage = activeJobStage();
        setIdle();
        setStage(stage, 'fault');
        return 'log-error';
    }

    return null;
}

function classifyLogLine(msg) {
    const trimmed = msg.trim();
    const lower = trimmed.toLowerCase();

    if (trimmed.startsWith('> ')) return 'log-echo';
    if (lower.includes('error') || lower.includes('exception') || lower.includes('failed') || lower.includes('critical')) {
        return 'log-error';
    }
    if (lower.includes('warning')) return 'log-warning';
    if (lower.includes('success') || lower.includes('completed') || lower.includes('finished') || lower.includes('saved')) {
        return 'log-success';
    }
    if (lower.includes('processing') || lower.includes('fetching') || lower.includes('starting')) {
        return 'log-info';
    }
    if (trimmed.startsWith('http') || trimmed.startsWith('/')) return 'log-dim';
    return '';
}

/* ---------------- Readouts ---------------- */

const fmtCount = (n) => Number(n).toLocaleString();

function fmtBytes(bytes) {
    if (!bytes) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    const value = bytes / 1024 ** i;
    return `${value.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

// Counts the scripts print as they go, so the panel updates mid-run rather
// than only once a job ends.
const METRIC_PATTERNS = [
    [/^Alive URLs:\s*(\d+)$/, (n) => { roFeeds.textContent = fmtCount(n); }],
    [/^(\d+) alive URLs saved to/, (n) => { roFeeds.textContent = fmtCount(n); }],
    [/^-\s*Total unique entries:\s*(\d+)$/, (n) => { roChannels.textContent = fmtCount(n); }],
];

function readMetrics(msg) {
    const trimmed = msg.trim();
    for (const [pattern, apply] of METRIC_PATTERNS) {
        const match = pattern.exec(trimmed);
        if (match) { apply(match[1]); return; }
    }
}

async function refreshStatus() {
    try {
        const res = await fetch('/api/status', { headers: authHeaders() });
        if (!res.ok) return; // 403 here is silent; the websocket does the prompting.
        const data = await res.json();

        if (data.sources && data.sources.exists && typeof data.sources.count === 'number') {
            roFeeds.textContent = fmtCount(data.sources.count);
            if (stageState.acquire === 'standby') setStage('acquire', 'done');
        }

        if (data.output && data.output.exists) {
            roSize.textContent = fmtBytes(data.output.size);
            if (typeof data.output.channels === 'number') {
                roChannels.textContent = fmtCount(data.output.channels);
            }
            btnDownload.disabled = false;
            setStage('master', 'ready');
            roStamp.textContent = `Built ${new Date(data.output.mtime * 1000).toLocaleString()}`;
        } else {
            btnDownload.disabled = true;
            setStage('master', 'empty');
            roStamp.textContent = 'Run the filter to build one.';
        }
    } catch {
        /* Offline: leave the last known readings on the panel. */
    }
}

/* ---------------- Filter rules ----------------
 * The builder and the JSON tab are two views of one document. `configDoc`
 * holds everything the builder does not manage (unknown keys survive a
 * round-trip untouched). */

const RULE_FIELDS = ['group', 'channel', 'tvg_name'];
const RULE_TYPES = ['exact_match', 'contains', 'startswith', 'regex'];

const FIELD_LABELS = {
    group: 'Group',
    channel: 'Channel',
    tvg_name: 'TVG name',
    url_domain: 'URL domain',
};

const OP_LABELS = {
    exact_match: 'is exactly',
    contains: 'contains',
    startswith: 'starts with',
    regex: 'matches regex',
};

// url_domain lives only under exclude, only as `contains`, and matches with
// endswith semantics — so it gets its own wording and no choice of test.
const DOMAIN_OP_LABEL = 'host or subdomain';

// Plurals for the lookup status line, where the labels read as quantities.
const FIELD_PLURALS = {
    group: 'groups',
    channel: 'channel names',
    tvg_name: 'tvg names',
};

let configDoc = {};

function fillSelect(select, values, labels) {
    select.replaceChildren(...values.map((value) => {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = labels[value] || value;
        return option;
    }));
}

/* ---------------- Value lookup ----------------
 * Rules are written against values that actually exist in the sources, so
 * the value box offers them as you type. The vocabulary is captured during
 * a build (before filtering, so excluded values stay discoverable) and
 * searched server-side — tvg-name alone runs to a million distinct values,
 * far too many to ship to the browser. */

let vocabMeta = null;

async function searchVocabulary(field, query, limit = 12) {
    const url = `/api/vocabulary/${field}?q=${encodeURIComponent(query)}&limit=${limit}`;
    const response = await fetch(url, { headers: authHeaders() });
    if (!response.ok) throw new Error(String(response.status));
    return response.json();
}

// Highlight the typed text where it appears literally. Accent folding means
// the server can match "series" inside "SÉRIES", which no plain offset can
// mark up — those rows simply render unhighlighted.
function renderValue(target, value, query) {
    const at = query ? value.toLowerCase().indexOf(query.toLowerCase()) : -1;
    if (at < 0) {
        target.textContent = value;
        return;
    }
    const hit = document.createElement('mark');
    hit.textContent = value.slice(at, at + query.length);
    target.append(value.slice(0, at), hit, value.slice(at + query.length));
}

/**
 * Wire a typeahead onto a text input.
 * @param input       the text box to augment
 * @param fieldFor    () => vocabulary field name, or null to stay dormant
 * @param onPick      (value) => void; defaults to filling the input
 */
function attachLookup(input, fieldFor, onPick) {
    const popup = document.createElement('div');
    popup.className = 'lookup';
    popup.setAttribute('role', 'listbox');

    let items = [];
    let active = -1;
    let timer = null;
    let sequence = 0;

    const place = () => {
        const box = input.getBoundingClientRect();
        const width = Math.max(box.width, 260);
        popup.style.width = `${width}px`;
        popup.style.left = `${Math.max(8, Math.min(box.left, window.innerWidth - width - 8))}px`;

        // Rules near the foot of the sheet have no room below them, so the
        // list opens upward rather than off the bottom of the window.
        const height = popup.offsetHeight;
        const below = window.innerHeight - box.bottom - 8;
        popup.style.top = (height > below && box.top > below)
            ? `${Math.max(8, box.top - height - 2)}px`
            : `${box.bottom + 2}px`;
    };

    const close = () => {
        popup.remove();
        popup.replaceChildren();
        items = [];
        active = -1;
        input.removeAttribute('aria-expanded');
        document.removeEventListener('scroll', place, true);
        window.removeEventListener('resize', place);
    };

    const open = () => {
        if (popup.isConnected) return place();
        document.body.appendChild(popup);
        input.setAttribute('aria-expanded', 'true');
        document.addEventListener('scroll', place, true);
        window.addEventListener('resize', place);
        place();
    };

    const highlight = (next) => {
        if (active >= 0 && items[active]) items[active].classList.remove('is-active');
        active = next;
        if (active >= 0 && items[active]) {
            items[active].classList.add('is-active');
            items[active].scrollIntoView({ block: 'nearest' });
        }
    };

    const note = (text) => {
        const el = document.createElement('div');
        el.className = 'lookup-note';
        el.textContent = text;
        popup.replaceChildren(el);
        items = [];
        active = -1;
        open();
    };

    const choose = (value) => {
        close();
        if (onPick) onPick(value);
        else input.value = value;
    };

    const render = (matches, query) => {
        items = matches.map(({ value, count }) => {
            const item = document.createElement('div');
            item.className = 'lookup-item';
            item.setAttribute('role', 'option');

            const text = document.createElement('span');
            text.className = 'lookup-value';
            renderValue(text, value, query);

            const badge = document.createElement('span');
            badge.className = 'lookup-count';
            badge.textContent = fmtCount(count);
            badge.title = `${fmtCount(count)} channel${count === 1 ? '' : 's'}`;

            item.append(text, badge);
            item.addEventListener('click', () => choose(value));
            return item;
        });
        popup.replaceChildren(...items);
        highlight(0);
        open();
    };

    const query = async () => {
        const field = fieldFor();
        const text = input.value.trim();
        if (!field || text.length < 1) return close();

        const ticket = ++sequence;
        try {
            const data = await searchVocabulary(field, text);
            // A slower earlier request must not overwrite a newer answer.
            if (ticket !== sequence || document.activeElement !== input) return;
            if (!data.available) return note('No value index yet — run a build, or scan the playlist.');
            if (!data.matches.length) return note(`No ${FIELD_LABELS[field].toLowerCase()} contains “${text}”.`);
            render(data.matches, text);
        } catch {
            if (ticket === sequence) close();
        }
    };

    input.setAttribute('autocomplete', 'off');
    input.addEventListener('input', () => {
        clearTimeout(timer);
        timer = setTimeout(query, 130);
    });
    input.addEventListener('blur', () => { clearTimeout(timer); close(); });

    // Capture phase: the chip inputs also bind Enter, and an open list must
    // win that key before the raw text gets committed.
    input.addEventListener('keydown', (event) => {
        if (!popup.isConnected || !items.length) {
            if (event.key === 'ArrowDown' && input.value.trim()) query();
            return;
        }
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            const step = event.key === 'ArrowDown' ? 1 : -1;
            highlight((active + step + items.length) % items.length);
        } else if (event.key === 'Enter' || event.key === 'Tab') {
            event.preventDefault();
            event.stopPropagation();
            choose(items[active].querySelector('.lookup-value').textContent);
        } else if (event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            close();
        }
    }, true);

    // Keep focus on the input so clicking a row never fires the blur commit.
    popup.addEventListener('mousedown', (event) => event.preventDefault());

    return { close };
}

async function refreshVocabulary() {
    try {
        const response = await fetch('/api/vocabulary', { headers: authHeaders() });
        if (!response.ok) throw new Error(String(response.status));
        vocabMeta = await response.json();
    } catch {
        vocabMeta = null;
    }
    renderVocabStatus();
}

function renderVocabStatus() {
    if (!vocabMeta || !vocabMeta.available) {
        vocabNote.textContent = vocabMeta && vocabMeta.can_derive
            ? 'No value lookup yet. Scan the current playlist to suggest values as you type.'
            : 'Value lookup appears once a build has run.';
        btnScanVocab.hidden = !(vocabMeta && vocabMeta.can_derive);
        return;
    }

    const fields = vocabMeta.meta.fields;
    const parts = RULE_FIELDS
        .map((field) => `${fmtCount(fields[field].total)} ${FIELD_PLURALS[field]}`);
    const from = vocabMeta.meta.source === 'playlist'
        ? 'Scanned from the built playlist, so it only offers values your current rules already keep.'
        : 'Captured during the last build, before filtering.';
    // tvg-name and channel run to a million distinct values; say so rather
    // than implying the lookup covers every one of them.
    const capped = RULE_FIELDS.filter((field) => fields[field].truncated);
    const cap = capped.length
        ? ` The ${fmtCount(fields[capped[0]].kept)} most common of each are searchable.`
        : '';
    vocabNote.textContent = `Lookup: ${parts.join(' · ')}. ${from}${cap}`;
    btnScanVocab.hidden = !vocabMeta.can_derive;
}

async function scanVocabulary() {
    const label = btnScanVocab.querySelector('.btn-label');
    const idle = label.textContent;
    btnScanVocab.disabled = true;
    label.textContent = 'Scanning';
    try {
        const response = await fetch('/api/vocabulary/derive', {
            method: 'POST',
            headers: authHeaders(),
        });
        if (!response.ok) throw new Error(String(response.status));
        await refreshVocabulary();
    } catch {
        vocabNote.textContent = 'Could not scan the playlist for values.';
    } finally {
        btnScanVocab.disabled = false;
        label.textContent = idle;
    }
}

function syncRuleRow(row, { keepOp = true } = {}) {
    const mode = row.querySelector('.rule-mode');
    const field = row.querySelector('.rule-field');
    const op = row.querySelector('.rule-op');
    const isDomain = field.value === 'url_domain';

    if (isDomain) {
        mode.value = 'exclude';
        mode.disabled = true;
        fillSelect(op, ['contains'], { contains: DOMAIN_OP_LABEL });
        op.disabled = true;
    } else {
        mode.disabled = false;
        const previous = keepOp ? op.value : null;
        fillSelect(op, RULE_TYPES, OP_LABELS);
        op.disabled = false;
        op.value = RULE_TYPES.includes(previous) ? previous : 'contains';
    }

    row.dataset.mode = mode.value;
}

function addRuleRow(rule = { mode: 'exclude', field: 'group', op: 'contains', value: '' }) {
    const row = document.createElement('div');
    row.className = 'rule';

    const mode = document.createElement('select');
    mode.className = 'rule-mode';
    mode.setAttribute('aria-label', 'Action');
    fillSelect(mode, ['exclude', 'include'], { exclude: 'Exclude', include: 'Include' });
    mode.value = rule.mode;

    const field = document.createElement('select');
    field.className = 'rule-field';
    field.setAttribute('aria-label', 'Field');
    fillSelect(field, [...RULE_FIELDS, 'url_domain'], FIELD_LABELS);
    field.value = rule.field;

    const op = document.createElement('select');
    op.className = 'rule-op';
    op.setAttribute('aria-label', 'Test');

    const value = document.createElement('input');
    value.type = 'text';
    value.className = 'rule-value';
    value.spellcheck = false;
    value.placeholder = 'Value';
    value.setAttribute('aria-label', 'Value');
    value.value = rule.value;

    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'icon-btn rule-del';
    del.title = 'Remove rule';
    del.innerHTML = '<svg class="ico"><use href="#i-x"/></svg>';

    row.append(mode, field, op, value, del);
    ruleList.appendChild(row);

    fillSelect(op, RULE_TYPES, OP_LABELS);
    op.value = rule.op;
    syncRuleRow(row);

    // A regex rule is a pattern, not a literal, so dropping a raw group name
    // like "SERIES | Drama" into it would silently mean something else.
    attachLookup(value, () => {
        if (op.value === 'regex') return null;
        return RULE_FIELDS.includes(field.value) ? field.value : null;
    });

    mode.addEventListener('change', () => { row.dataset.mode = mode.value; });
    field.addEventListener('change', () => syncRuleRow(row, { keepOp: false }));
    del.addEventListener('click', () => { row.remove(); syncRuleEmpty(); });

    syncRuleEmpty();
    return row;
}

function syncRuleEmpty() {
    ruleEmpty.hidden = ruleList.children.length > 0;
}

/* -- chips -- */

function chipSet(containerId, inputId, lookupField = null) {
    const container = $(containerId);
    const input = $(inputId);

    const add = (raw) => {
        const text = raw.trim();
        if (!text || values().includes(text)) return;
        const chip = document.createElement('span');
        chip.className = 'chip';
        chip.append(document.createTextNode(text));

        const remove = document.createElement('button');
        remove.type = 'button';
        remove.title = `Remove ${text}`;
        remove.innerHTML = '<svg class="ico"><use href="#i-x"/></svg>';
        remove.addEventListener('click', () => { chip.remove(); syncPairWarning(); });

        chip.appendChild(remove);
        container.insertBefore(chip, input);
        syncPairWarning();
    };

    const values = () => [...container.querySelectorAll('.chip')]
        .map((chip) => chip.firstChild.textContent.trim());

    input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ',') {
            event.preventDefault();
            add(input.value);
            input.value = '';
        } else if (event.key === 'Backspace' && !input.value) {
            const last = container.querySelectorAll('.chip');
            if (last.length) { last[last.length - 1].remove(); syncPairWarning(); }
        }
    });
    input.addEventListener('blur', () => { add(input.value); input.value = ''; });

    if (lookupField) {
        attachLookup(input, () => lookupField, (value) => { add(value); input.value = ''; });
    }

    return {
        values,
        set(list) {
            container.querySelectorAll('.chip').forEach((chip) => chip.remove());
            (list || []).forEach(add);
        },
    };
}

// Both lists match on the group name, so both look values up there.
const prefixChips = chipSet('chips-prefix', 'chip-prefix-input', 'group');
const contentChips = chipSet('chips-content', 'chip-content-input', 'group');

// Both lists must be filled for the pair to apply; one alone is dead config.
function syncPairWarning() {
    const a = prefixChips.values().length;
    const b = contentChips.values().length;
    pairWarning.hidden = !((a && !b) || (b && !a));
}

/* -- document <-> builder -- */

function docToBuilder(doc) {
    const settings = doc.settings || {};
    setTopFastest.value = settings.top_fastest_count ?? 0;
    setErrorThreshold.value = settings.domain_error_threshold ?? 0;

    ruleList.replaceChildren();
    for (const mode of ['exclude', 'include']) {
        const section = doc[mode] || {};
        for (const field of RULE_FIELDS) {
            const rules = section[field] || {};
            for (const op of RULE_TYPES) {
                for (const value of rules[op] || []) {
                    addRuleRow({ mode, field, op, value: String(value) });
                }
            }
        }
    }
    for (const value of (doc.exclude || {}).url_domain?.contains || []) {
        addRuleRow({ mode: 'exclude', field: 'url_domain', op: 'contains', value: String(value) });
    }
    syncRuleEmpty();

    prefixChips.set((doc.include || {}).group_prefix);
    contentChips.set((doc.include || {}).group_content);
    syncPairWarning();
}

function builderToDoc() {
    // Start from the loaded document so keys the builder knows nothing about
    // survive a save.
    const doc = JSON.parse(JSON.stringify(configDoc || {}));

    doc.settings = { ...(doc.settings || {}) };
    doc.settings.top_fastest_count = Math.max(0, parseInt(setTopFastest.value, 10) || 0);
    doc.settings.domain_error_threshold = Math.max(0, parseInt(setErrorThreshold.value, 10) || 0);

    const blank = () => Object.fromEntries(RULE_TYPES.map((op) => [op, []]));
    doc.exclude = { ...(doc.exclude || {}) };
    doc.include = { ...(doc.include || {}) };
    for (const field of RULE_FIELDS) {
        doc.exclude[field] = blank();
        doc.include[field] = blank();
    }
    doc.exclude.url_domain = { ...(doc.exclude.url_domain || {}), contains: [] };

    for (const row of ruleList.children) {
        const value = row.querySelector('.rule-value').value.trim();
        if (!value) continue;
        const mode = row.querySelector('.rule-mode').value;
        const field = row.querySelector('.rule-field').value;
        const op = row.querySelector('.rule-op').value;

        if (field === 'url_domain') doc.exclude.url_domain.contains.push(value);
        else doc[mode][field][op].push(value);
    }

    doc.include.group_prefix = prefixChips.values();
    doc.include.group_content = contentChips.values();

    return doc;
}

function countRules(doc) {
    let total = 0;
    for (const mode of ['exclude', 'include']) {
        const section = doc[mode] || {};
        for (const field of RULE_FIELDS) {
            for (const op of RULE_TYPES) {
                total += ((section[field] || {})[op] || []).length;
            }
        }
    }
    total += ((doc.exclude || {}).url_domain?.contains || []).length;
    const include = doc.include || {};
    if ((include.group_prefix || []).length && (include.group_content || []).length) total += 1;
    return total;
}

/* -- tabs -- */

let activePane = 'build';

function showPane(name) {
    if (name === activePane) return;

    if (name === 'json') {
        configEditor.value = JSON.stringify(builderToDoc(), null, 2);
    } else {
        // Carry hand-edited JSON back into the builder, refusing only if it
        // cannot be parsed — otherwise the two views would silently diverge.
        try {
            const parsed = JSON.parse(configEditor.value);
            configDoc = parsed;
            docToBuilder(parsed);
        } catch (err) {
            configError.textContent = `That JSON will not parse, so the builder cannot show it: ${err.message}`;
            return;
        }
    }

    configError.textContent = '';
    activePane = name;
    paneBuild.hidden = name !== 'build';
    paneJson.hidden = name !== 'json';
    tabs.forEach((tab) => {
        const on = tab.dataset.pane === name;
        tab.classList.toggle('is-on', on);
        tab.setAttribute('aria-selected', String(on));
    });
}

/* -- load / save -- */

async function loadConfigFiles() {
    try {
        const res = await fetch('/api/configs', { headers: authHeaders() });
        if (!res.ok) throw new Error('could not list config files');
        const data = await res.json();

        configFileSelect.replaceChildren(...data.files.map((file) => {
            const option = document.createElement('option');
            option.value = file;
            option.textContent = file;
            return option;
        }));
        // One editable file is the common case; the picker is then noise.
        configFileBar.hidden = data.files.length < 2;

        if (data.files.length) await loadConfigContent(data.files[0]);
    } catch (err) {
        configError.textContent = `Could not load the rules: ${err.message}`;
    }
}

async function fetchConfig(filename) {
    const res = await fetch(`/api/config/${filename}`, { headers: authHeaders() });
    if (res.status === 403) {
        sessionStorage.removeItem('iptv_api_key');
        if (await ensureApiKey()) return fetchConfig(filename);
        throw new Error('an API key is required');
    }
    if (!res.ok) throw new Error(`server returned ${res.status}`);
    return res.json();
}

async function loadConfigContent(filename) {
    try {
        configDoc = await fetchConfig(filename);
        docToBuilder(configDoc);
        configEditor.value = JSON.stringify(configDoc, null, 2);
        roRules.textContent = fmtCount(countRules(configDoc));
        configError.textContent = '';
    } catch (err) {
        configError.textContent = `Could not load ${filename}: ${err.message}`;
        log(`Could not load ${filename}: ${err.message}`, 'log-error');
    }
}

// Keeps the Filter readout honest without opening the editor.
async function refreshRuleCount() {
    try {
        const res = await fetch('/api/configs', { headers: authHeaders() });
        if (!res.ok) return;
        const { files } = await res.json();
        if (!files.length) return;
        const content = await fetch(`/api/config/${files[0]}`, { headers: authHeaders() });
        if (!content.ok) return;
        const doc = await content.json();
        configDoc = doc;
        roRules.textContent = fmtCount(countRules(doc));
    } catch {
        /* Readout stays at its last value. */
    }
}

async function saveConfig() {
    const filename = configFileSelect.value;
    configError.textContent = '';

    let content;
    try {
        content = activePane === 'json' ? JSON.parse(configEditor.value) : builderToDoc();
    } catch (err) {
        configError.textContent = `That JSON will not parse: ${err.message}`;
        return;
    }

    btnSaveConfig.disabled = true;
    btnSaveConfig.textContent = 'Saving';
    try {
        const res = await fetch(`/api/config/${filename}`, {
            method: 'POST',
            headers: authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ content }),
        });

        if (res.status === 403) {
            sessionStorage.removeItem('iptv_api_key');
            if (await ensureApiKey()) { btnSaveConfig.disabled = false; return saveConfig(); }
            throw new Error('an API key is required');
        }
        if (!res.ok) throw new Error(await res.text());

        configDoc = content;
        roRules.textContent = fmtCount(countRules(content));
        log(`Saved ${countRules(content)} rules to ${filename}.`, 'log-success');
        closeConfigModal();
    } catch (err) {
        configError.textContent = `Could not save: ${err.message}`;
        log(`Could not save ${filename}: ${err.message}`, 'log-error');
    } finally {
        btnSaveConfig.disabled = false;
        btnSaveConfig.textContent = 'Save rules';
    }
}

function openConfigModal() {
    configError.textContent = '';
    configModal.classList.add('is-open');
    loadConfigFiles();
    refreshVocabulary();
}

function closeConfigModal() {
    configModal.classList.remove('is-open');
    configError.textContent = '';
}

/* ---------------- Actions ---------------- */

async function triggerAction(job, endpoint, body = {}) {
    setBusy(job);
    try {
        const response = await fetch(endpoint, {
            method: 'POST',
            headers: authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(body),
        });

        // Accepted: the job is now running server-side. Stay busy; the log
        // stream releases the UI.
        if (response.ok) return;

        if (response.status === 403) {
            log('The API key was missing or wrong.', 'log-error');
            sessionStorage.removeItem('iptv_api_key');
            if (await ensureApiKey()) return triggerAction(job, endpoint, body);
        } else if (response.status === 409) {
            log('Another job is already running. Wait for it to finish.', 'log-warning');
        } else {
            log(`Could not start the job: ${response.status} ${response.statusText}`, 'log-error');
        }
    } catch (error) {
        log(`Network error: ${error.message}`, 'log-error');
    }

    // Only reached when the job never started.
    setIdle();
    setStage(JOB_STAGES[job], 'standby');
}

/* ---------------- Wiring ---------------- */

btnScrape.addEventListener('click', () => {
    const url = sourceUrlInput.value.trim();
    if (!url) {
        log('Enter a source page to scan.', 'log-warning');
        sourceUrlInput.focus();
        return;
    }
    log(`> Acquire: ${url}`, 'log-echo');
    triggerAction('scrape', '/api/scrape', { url });
});

btnProcess.addEventListener('click', () => {
    log('> Filter: applying rules to every acquired feed', 'log-echo');
    triggerAction('process', '/api/process', { input_file: 'iptv_m3u_urls.txt' });
});

btnDownload.addEventListener('click', () => { window.location.href = '/api/download'; });

btnClearLogs.addEventListener('click', () => {
    clearConsole();
    log('Console cleared.', 'log-system');
});

btnEditConfig.addEventListener('click', openConfigModal);
closeModalBtn.addEventListener('click', closeConfigModal);
btnCancelConfig.addEventListener('click', closeConfigModal);
btnSaveConfig.addEventListener('click', saveConfig);
btnScanVocab.addEventListener('click', scanVocabulary);
btnAddRule.addEventListener('click', () => {
    const row = addRuleRow();
    row.querySelector('.rule-value').focus();
});
configFileSelect.addEventListener('change', (e) => loadConfigContent(e.target.value));
tabs.forEach((tab) => tab.addEventListener('click', () => showPane(tab.dataset.pane)));

configModal.addEventListener('click', (event) => {
    if (event.target === configModal) closeConfigModal();
});
document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && configModal.classList.contains('is-open')) closeConfigModal();
});

sourceUrlInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !btnScrape.disabled) btnScrape.click();
});

connectWebSocket();
refreshStatus();
