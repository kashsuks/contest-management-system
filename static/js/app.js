'use strict';

/* ==========================================================================
   Contest single-page app. Routes live in the URL hash:
     #/problems  #/problems/<id>  #/submissions  #/submissions/<id>
     #/leaderboard  #/admin/<users|problems|settings>
   All server data is inserted with textContent (see el()); the only HTML we
   ever inject is markdown (see markdown.js), which goes through DOMPurify.
   ========================================================================== */

// ---------------------------------------------------------------- helpers
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

/** Build a DOM node. Children can be nodes, strings or numbers; attributes never create HTML. */
function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
        if (value === null || value === undefined || value === false) continue;
        if (key === 'class') node.className = value;
        else if (key === 'text') node.textContent = value;
        else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2), value);
        else node.setAttribute(key, value === true ? '' : value);
    }
    for (const child of children.flat()) {
        if (child === null || child === undefined || child === false) continue;
        node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
}

const storage = {
    get(key) { try { return localStorage.getItem(key); } catch (e) { return null; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch (e) { /* storage full or unavailable */ } },
};

function debounce(fn, wait) {
    let timer;
    const wrapped = (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), wait); };
    wrapped.flush = (...args) => { clearTimeout(timer); fn(...args); };
    return wrapped;
}

// ---------------------------------------------------------------- formatting
const STATUS = {
    AC: { cls: 'ac', label: 'Accepted' },
    WA: { cls: 'wa', label: 'Wrong Answer' },
    TLE: { cls: 'tle', label: 'Time Limit Exceeded' },
    MLE: { cls: 'mle', label: 'Memory Limit Exceeded' },
    RE: { cls: 're', label: 'Runtime Error' },
    CE: { cls: 'ce', label: 'Compilation Error' },
    ERROR: { cls: 'wa', label: 'Judge Error' },
    PENDING: { cls: '', label: 'Pending' },
    skip: { cls: '', label: 'Skipped' },
    PARTIAL: { cls: 'partial', label: 'Partial score' },
};

function chip(status, { full = false } = {}) {
    const info = STATUS[status] || { cls: '', label: String(status) };
    return el('span', { class: `chip ${info.cls}`, title: info.label }, full ? info.label : (status === 'skip' ? 'SKIP' : status));
}

function fmtMs(value) {
    if (value === null || value === undefined || value === '') return '-';
    if (typeof value === 'string') return `${value.replace(/\.0+$/, '')} ms`;
    return value < 10 ? `${value.toFixed(1)} ms` : `${Math.round(value)} ms`;
}

function fmtKb(value) {
    if (value === null || value === undefined || value === '' || value === 0) return '-';
    if (typeof value === 'string') {
        const match = value.match(/^(>?)([\d.]+)$/);
        return match ? `${match[1]}${fmtKb(parseFloat(match[2]))}` : value;
    }
    return value < 1024 ? `${Math.round(value)} KB` : `${(value / 1024).toFixed(1)} MB`;
}

function fmtLimits(problem) {
    const ms = problem.time_limit;
    const time = ms % 1000 === 0 ? `${ms / 1000} s` : `${ms} ms`;
    return `${time} · ${problem.memory_limit} MB`;
}

// Timestamps come from the server as the contest's local wall-clock time, so show them as-is (no relative times)
function fmtWhen(iso) {
    return new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function difficultyChip(difficulty) {
    const key = String(difficulty || '').toLowerCase();
    return el('span', { class: `chip ${['easy', 'medium', 'hard'].includes(key) ? key : ''}` }, difficulty);
}

/** Overall verdict for a contest submission. */
function verdictFor(points, total, batchResults) {
    if (total > 0 && points >= total) return { cls: 'ac', label: 'Accepted' };
    const failed = (batchResults || []).find(b => b.status && b.status !== 'AC');
    if (points > 0) return { cls: 'partial', label: 'Partial score' };
    const info = STATUS[failed ? failed.status : 'WA'] || STATUS.WA;
    return { cls: info.cls, label: info.label };
}

// ---------------------------------------------------------------- toasts
function toast(message, type = 'info', timeout = 4500) {
    const item = el('div', { class: `toast-item ${type}`, role: type === 'error' ? 'alert' : 'status' },
        el('div', { class: 'msg', text: message }),
        el('button', { type: 'button', 'aria-label': 'Dismiss', onclick: () => item.remove() }, '×'));
    $('#toasts').append(item);
    if (timeout) setTimeout(() => item.remove(), timeout);
}

// ---------------------------------------------------------------- api
async function api(path, options) {
    const response = await fetch(path, options);
    const text = await response.text();
    let data = null;
    try { data = JSON.parse(text); } catch (e) { /* not JSON */ }
    // An expired session redirects to the login page (HTML), so send the user back there
    if (response.redirected && new URL(response.url).pathname === '/login') {
        window.location.href = '/login';
        // Callers treat this like any failed request; the page is about to be replaced by the login page
        return { ok: false, status: 401, data: null, text: '' };
    }
    return { ok: response.ok, status: response.status, data, text };
}

function postJson(path, body) {
    return api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
}

function friendlyError(result, fallback = 'Something went wrong. Please try again.') {
    if (result.status === 429) return 'Too many requests. Please wait a minute and try again.';
    if (result.status === 413) return 'That is too large to send.';
    return (result.data && result.data.error) || fallback;
}

function setBusy(button, busy, busyLabel) {
    if (busy) {
        button.dataset.label = button.innerHTML;
        button.disabled = true;
        button.replaceChildren(el('span', { class: 'spinner', 'aria-hidden': 'true' }), ` ${busyLabel}`);
    } else {
        button.disabled = false;
        if (button.dataset.label !== undefined) button.innerHTML = button.dataset.label;
    }
}

/** Offer a Blob to the user as a file download. */
function saveBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const link = el('a', { href: url, download: filename });
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
}

// ---------------------------------------------------------------- state
const state = {
    user: '',
    isAdmin: false,
    contestName: document.title,
    problems: [],
    submissions: [],
    leaderboard: null,
    settings: null,
    contest: { start: null, end: null, offset: 0, phase: 'running', stopped: false }, // see loadContestState
};

let routeToken = 0;

function setTitle(part) {
    document.title = part ? `${part} · ${state.contestName}` : state.contestName;
}

// ---------------------------------------------------------------- contest schedule
const pad2 = n => String(n).padStart(2, '0');

function fmtCountdown(ms) {
    const total = Math.max(0, Math.ceil(ms / 1000));
    const days = Math.floor(total / 86400), hours = Math.floor(total % 86400 / 3600);
    const clock = `${pad2(hours)}:${pad2(Math.floor(total % 3600 / 60))}:${pad2(total % 60)}`;
    return days ? `${days}d ${clock}` : clock;
}

/** The server's clock, as far as we can tell: our own clock corrected by the offset measured in loadContestState. */
const serverNow = () => Date.now() + state.contest.offset;

function phaseNow() {
    const { start, end } = state.contest;
    if (start !== null && serverNow() < start) return 'upcoming';
    if (end !== null && serverNow() >= end) return 'ended';
    return 'running';
}

async function loadContestState() {
    const result = await api('/contest_state');
    if (!result.ok || !result.data) return;
    const { start_time, end_time, server_time, submissions_stopped } = result.data;
    state.contest = {
        start: start_time ? Date.parse(start_time) : null,
        end: end_time ? Date.parse(end_time) : null,
        offset: Date.parse(server_time) - Date.now(),
        phase: 'running',
        stopped: !!submissions_stopped,
    };
    state.contest.phase = phaseNow();
    renderClock();
    renderProblems();
}

/** Counts down to the start, then to the end; says nothing for contests with no schedule. */
function renderClock() {
    const clock = $('#contestClock');
    const { start, end } = state.contest;
    const phase = phaseNow();
    let text = '', cls = '', title = '';
    if (phase === 'upcoming') {
        text = `Starts in ${fmtCountdown(start - serverNow())}`;
        title = `Starts ${new Date(start).toLocaleString()}`;
    } else if (phase === 'ended') {
        text = 'Contest ended';
        cls = 'over';
        title = `Ended ${new Date(end).toLocaleString()}`;
    } else if (end !== null) {
        const left = end - serverNow();
        text = `${fmtCountdown(left)} left`;
        cls = left <= 10 * 60 * 1000 ? 'soon' : '';
        title = `Ends ${new Date(end).toLocaleString()}`;
    } else if (state.contest.stopped) {
        text = 'Submissions closed';
        cls = 'over';
    }
    clock.hidden = !text;
    clock.className = `contest-clock ${cls}`.trim();
    if (clock.textContent !== text) clock.textContent = text;
    clock.title = title;
}

/** Once a second: update the clock and react when the contest starts or ends without anyone refreshing the page. */
function tickClock() {
    renderClock();
    const phase = phaseNow();
    if (phase === state.contest.phase) return;
    const before = state.contest.phase;
    state.contest.phase = phase;
    if (before === 'upcoming' && phase !== 'upcoming') {
        toast('The contest has started. Good luck!', 'success');
        loadProblems();
        loadLeaderboard();
    } else if (phase === 'ended') {
        toast('The contest has ended. Submissions are closed.', 'info', 8000);
    }
    loadContestState(); // re-sync with the server
}

function startContestClock() {
    renderClock();
    setInterval(tickClock, 1000);
    setInterval(loadContestState, 60000); // picks up changes made while a socket event was missed
}

// ---------------------------------------------------------------- data loaders
async function loadProblems() {
    const result = await api('/problems');
    if (!result.ok) return;
    state.problems = result.data.sort((a, b) => a.id - b.id);
    renderProblems();
    renderAdminProblems();
}
window.loadProblems = loadProblems; // called by the problem-creation window after saving

async function loadSubmissions() {
    const result = await api('/submissions');
    if (!result.ok) return;
    state.submissions = result.data.sort((a, b) => b.id - a.id);
    renderSubmissions();
    renderProblems(); // statuses depend on submissions
}

async function loadLeaderboard() {
    const result = await api('/leaderboard');
    if (!result.ok) return;
    state.leaderboard = result.data;
    renderLeaderboard();
}

// ---------------------------------------------------------------- problems list
function progressFor(problem) {
    const mine = state.submissions.filter(s => s.problem_id === problem.id);
    const total = problem.total_points || 0;
    if (!mine.length) return { kind: 'new', best: 0, total };
    const best = Math.max(0, ...mine.map(s => s.points_earned || 0));
    return { kind: total > 0 && best >= total ? 'solved' : 'tried', best, total };
}

function renderProblems() {
    const list = $('#problemList');
    if (!state.problems.length && !state.isAdmin && phaseNow() === 'upcoming') {
        list.replaceChildren(el('div', { class: 'empty' },
            el('h3', { text: 'The contest has not started yet' }),
            el('p', { text: `Problems appear here at ${new Date(state.contest.start).toLocaleString()}. This page updates by itself.` })));
        $('#problemsSummary').textContent = '';
        return;
    }
    if (!state.problems.length) {
        list.replaceChildren(el('div', { class: 'empty' },
            el('h3', { text: 'No problems yet' }),
            el('p', { text: 'Problems will show up here as soon as they are published.' })));
        $('#problemsSummary').textContent = '';
        return;
    }

    let solved = 0, earned = 0, available = 0;
    const rows = state.problems.map(problem => {
        const progress = progressFor(problem);
        if (progress.kind === 'solved') solved++;
        earned += progress.best;
        available += progress.total;

        const dotClass = progress.kind === 'solved' ? 'ac' : progress.best > 0 ? 'partial' : '';
        const dotTitle = progress.kind === 'solved' ? 'Solved' : progress.kind === 'tried' ? 'Attempted' : 'Not tried yet';
        return el('a', { class: 'problem-row', href: `#/problems/${Number(problem.id)}` },
            el('span', { class: `dot ${dotClass}`, title: dotTitle }),
            el('span', { class: 'short', text: problem.shortname }),
            el('span', { class: 'title', text: problem.title }),
            difficultyChip(problem.difficulty),
            el('span', { class: 'limits', text: fmtLimits(problem) }),
            el('span', { class: 'score', text: progress.kind === 'new' ? `${problem.total_points} pts` : `${progress.best} / ${progress.total}` }));
    });

    const header = el('div', { class: 'problem-row header', 'aria-hidden': 'true' },
        el('span'), el('span', { text: '#' }), el('span', { text: 'Title' }), el('span', { text: 'Level' }),
        el('span', { text: 'Limits' }), el('span', { class: 'score', text: 'Score' }));
    list.replaceChildren(header, ...rows);
    $('#problemsSummary').textContent = `${solved} of ${state.problems.length} solved · ${earned} / ${available} points`;
}

// ---------------------------------------------------------------- problem page
const STARTERS = {
    python: '# Read the input with input() and print the answer\n',
    cpp: '#include <bits/stdc++.h>\nusing namespace std;\n\nint main() {\n    ios::sync_with_stdio(false);\n    cin.tie(nullptr);\n\n    // your code here\n    return 0;\n}\n',
    java: 'import java.util.*;\n\n// The class must be called Solution\npublic class Solution {\n    public static void main(String[] args) {\n        Scanner in = new Scanner(System.in);\n        // your code here\n    }\n}\n',
};
const CM_MODES = { python: 'python', cpp: 'text/x-c++src', java: 'text/x-java' };

let editor = null;
let currentProblem = null;
let editorLanguage = 'python'; // language the text currently in the editor belongs to
let loadingDraft = false;
let customInputIntroduced = false;

function ensureEditor() {
    if (editor) return editor;
    editor = CodeMirror.fromTextArea($('#code'), {
        mode: 'python',
        theme: 'contest',
        lineNumbers: true,
        indentUnit: 4,
        tabSize: 4,
        indentWithTabs: false,
        extraKeys: {
            Tab: cm => cm.somethingSelected() ? cm.indentSelection('add') : cm.replaceSelection('    ', 'end'),
            'Shift-Tab': cm => cm.indentSelection('subtract'),
            'Ctrl-Enter': () => runCode(),
            'Cmd-Enter': () => runCode(),
        },
    });
    editor.on('change', () => { if (!loadingDraft) saveDraft(); });
    return editor;
}

const draftKey = (problemId, language) => `cms:draft:${state.user}:${problemId}:${language}`;
const saveDraft = debounce(() => {
    if (currentProblem && editor) storage.set(draftKey(currentProblem.id, editorLanguage), editor.getValue());
}, 400);

function loadDraft() {
    const language = $('#language').value;
    editorLanguage = language;
    const saved = storage.get(draftKey(currentProblem.id, language));
    loadingDraft = true;
    editor.setOption('mode', CM_MODES[language]);
    editor.setValue(saved !== null ? saved : STARTERS[language]);
    editor.clearHistory();
    loadingDraft = false;
}

async function showProblem(id, token) {
    const result = await api(`/problem/${id}`);
    if (token !== routeToken) return; // user navigated away while loading
    if (!result.ok) {
        toast(result.status === 403 && result.data ? result.data.error : 'Could not open that problem.', 'error');
        location.hash = '#/problems';
        return;
    }

    currentProblem = result.data;
    setTitle(currentProblem.title);
    $('#problemTitle').textContent = currentProblem.title;
    $('#problemMeta').replaceChildren(
        difficultyChip(currentProblem.difficulty),
        el('span', { text: `Time limit ${currentProblem.time_limit % 1000 === 0 ? currentProblem.time_limit / 1000 + ' s' : currentProblem.time_limit + ' ms'}` }),
        el('span', { text: `Memory ${currentProblem.memory_limit} MB` }),
        el('span', { text: `${currentProblem.total_points} points` }));
    renderMarkdownInto($('#problemStatement'), currentProblem.description);

    ensureEditor();
    const savedLanguage = storage.get(`cms:lang:${state.user}`);
    if (STARTERS[savedLanguage]) $('#language').value = savedLanguage;
    loadDraft();
    $('#resultPanel').hidden = true;
    requestAnimationFrame(() => editor.refresh());
}

/** Re-read the open problem (after an admin edited or deleted it) without touching the editor or the draft. */
async function refreshOpenProblem() {
    const id = currentProblem.id;
    const result = await api(`/problem/${id}`);
    if (result.status === 404) {
        toast('This problem was removed by an organiser.', 'error');
        location.hash = '#/problems';
        return;
    }
    if (!result.ok || !currentProblem || currentProblem.id !== id) return;
    currentProblem = result.data;
    $('#problemTitle').textContent = currentProblem.title;
    $('#problemMeta').replaceChildren(
        difficultyChip(currentProblem.difficulty),
        el('span', { text: `Time limit ${currentProblem.time_limit % 1000 === 0 ? currentProblem.time_limit / 1000 + ' s' : currentProblem.time_limit + ' ms'}` }),
        el('span', { text: `Memory ${currentProblem.memory_limit} MB` }),
        el('span', { text: `${currentProblem.total_points} points` }));
    renderMarkdownInto($('#problemStatement'), currentProblem.description);
    toast('This problem was updated by an organiser.', 'info');
}

function setBusyAll(busy, which) {
    const run = $('#runBtn'), submit = $('#submitBtn');
    if (busy) {
        setBusy(which === 'run' ? run : submit, true, which === 'run' ? 'Running...' : 'Judging...');
        (which === 'run' ? submit : run).disabled = true;
    } else {
        setBusy(run, false);
        setBusy(submit, false);
        run.disabled = false;
        submit.disabled = false;
    }
}

function showResult(...nodes) {
    $('#resultBody').replaceChildren(...nodes.filter(Boolean)); // replaceChildren would print null as text
    $('#resultPanel').hidden = false;
    $('#resultPanel').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

function outputBlock(label, text, isError = false) {
    return el('div', {},
        el('div', { class: 'form-label', text: label }),
        el('pre', { class: `output-block${isError ? ' error' : ''}`, text: text === '' ? '(empty)' : text }));
}

async function runCode() {
    if (!currentProblem || $('#runBtn').disabled) return;

    const details = $('#customInput');
    if (!customInputIntroduced && !details.open && !$('#testInput').value) {
        // First time: point people at the input box instead of running with nothing
        customInputIntroduced = true;
        details.open = true;
        $('#testInput').focus();
        toast('Type some input for your program, then press Run again.', 'info');
        return;
    }
    customInputIntroduced = true;

    const input = $('#testInput').value;
    const expected = $('#testOutput').value.trim();
    setBusyAll(true, 'run');
    try {
        const result = await postJson('/run_code', {
            code: editor.getValue(),
            language: $('#language').value,
            test_cases: [{ input, output: expected }],
        });

        if (!result.ok) {
            showResult(el('div', { class: 'result-head' }, el('span', { class: 'verdict wa', text: 'Could not run' })),
                el('p', { class: 'mt-2 mb-0', text: friendlyError(result) }));
            return;
        }

        const test = result.data.batch_results[0].test_case_results[0];
        const justOutput = !expected && (test.status === 'AC' || test.status === 'WA');
        const head = el('div', { class: 'result-head' },
            el('span', {
                class: `verdict ${justOutput ? '' : (STATUS[test.status] || {}).cls || ''}`,
                text: justOutput ? 'Output' : (STATUS[test.status] || { label: test.status }).label,
            }),
            test.execution_time ? el('div', { class: 'stat-row' },
                el('span', {}, 'Time ', el('b', { text: fmtMs(test.execution_time) })),
                el('span', {}, 'Memory ', el('b', { text: fmtKb(test.memory_used) }))) : null);

        const body = [head];
        if (test.status === 'CE' || test.status === 'RE') {
            body.push(outputBlock(test.status === 'CE' ? 'Compiler output' : 'Error', test.error || '', true));
        } else if (test.status === 'TLE' || test.status === 'MLE') {
            body.push(el('p', { class: 'mt-2 mb-0 text-secondary', text: test.error || STATUS[test.status].label }));
        } else if (!expected) {
            body.push(outputBlock('Your output', test.got || ''));
        } else if (test.status !== 'AC') {
            body.push(outputBlock('Expected', test.expected || ''), outputBlock('Your output', test.got || ''));
        } else {
            body.push(el('p', { class: 'mt-2 mb-0 text-secondary', text: 'Your output matches the expected output.' }));
        }
        showResult(...body);
    } catch (error) {
        toast('Could not reach the server. Check your connection and try again.', 'error');
    } finally {
        setBusyAll(false);
    }
}

async function submitCode() {
    if (!currentProblem || $('#submitBtn').disabled) return;
    const code = editor.getValue();
    if (!code.trim()) {
        toast('Write some code before submitting.', 'error');
        return;
    }

    saveDraft.flush();
    setBusyAll(true, 'submit');
    try {
        const result = await postJson('/submit', { problem_id: currentProblem.id, code, language: $('#language').value });
        if (!result.ok) {
            showResult(el('div', { class: 'result-head' }, el('span', { class: 'verdict wa', text: 'Not submitted' })),
                el('p', { class: 'mt-2 mb-0', text: friendlyError(result) }));
            return;
        }

        const data = result.data;
        const verdict = verdictFor(data.points_earned, currentProblem.total_points, data.batch_results);
        const failure = data.batch_results.flatMap(b => b.test_case_results).find(t => t.status === 'CE' && t.error);

        showResult(
            el('div', { class: 'result-head' },
                el('span', { class: `verdict ${verdict.cls}`, text: verdict.label }),
                el('div', { class: 'stat-row' },
                    el('span', {}, 'Score ', el('b', { text: `${data.points_earned} / ${currentProblem.total_points}` })),
                    data.execution_time ? el('span', {}, 'Time ', el('b', { text: fmtMs(data.execution_time) })) : null,
                    data.memory_used ? el('span', {}, 'Memory ', el('b', { text: fmtKb(data.memory_used) })) : null)),
            failure ? outputBlock('Compiler output', failure.error, true) : null,
            renderBatches(data.batch_results),
            el('div', { class: 'mt-3' }, el('a', { href: `#/submissions/${Number(data.id)}`, text: 'View full submission →' })));

        if (verdict.cls === 'ac') toast(`Accepted! ${data.points_earned} points.`, 'success');
        loadSubmissions();
    } catch (error) {
        toast('Could not reach the server. Check your connection and try again.', 'error');
    } finally {
        setBusyAll(false);
    }
}

/** Collapsible per-batch results shared by the problem page and the submission page. */
function renderBatches(batchResults) {
    let testNumber = 0;
    const wrapper = el('div');
    batchResults.forEach((batch, index) => {
        const tests = batch.test_case_results || [];
        const many = tests.length > 10;
        const passed = tests.filter(t => t.status === 'AC').length;

        const lines = [];
        tests.forEach(test => {
            testNumber++;
            if (many && test.status === 'AC') return; // keep long batches readable: only list the interesting ones
            lines.push(el('div', { class: 'test-line' },
                el('span', { class: 'name', text: `Test ${testNumber}` }),
                chip(test.status),
                test.status !== 'skip' && test.execution_time !== undefined
                    ? el('span', { class: 'meta', text: `${fmtMs(test.execution_time)} · ${fmtKb(test.memory_used)}` }) : null));
            if (test.error && (test.status === 'RE' || test.status === 'CE')) {
                lines.push(el('pre', { class: 'output-block error', text: test.error }));
            }
        });
        if (many) lines.unshift(el('div', { class: 'test-line' }, el('span', { class: 'meta', text: `${passed} of ${tests.length} tests passed` })));

        wrapper.append(el('details', { class: 'batch', open: batch.status !== 'AC' },
            el('summary', {}, chip(batch.status), `Batch ${index + 1}`,
                el('span', { class: 'pts', text: batch.status === 'AC' ? `+${batch.batch_points} pts` : '0 pts' })),
            el('div', { class: 'tests' }, ...lines)));
    });
    return wrapper;
}

// ---------------------------------------------------------------- submissions
function renderSubmissions() {
    const body = $('#submissionsList');
    const subs = state.submissions;
    $('#submissionsEmpty').hidden = subs.length > 0;
    $('#submissionsTable').hidden = subs.length === 0;
    $('#submissionsSummary').textContent = subs.length
        ? `${subs.length} submission${subs.length === 1 ? '' : 's'}, newest first.` : '';

    body.replaceChildren(...subs.map(s => {
        const go = () => { location.hash = `#/submissions/${Number(s.id)}`; };
        const row = el('tr', { class: 'clickable', onclick: event => { if (!event.target.closest('a')) go(); } },
            el('td', {}, el('a', { href: `#/submissions/${Number(s.id)}`, text: s.problem.title })),
            el('td', { text: { python: 'Python', cpp: 'C++', java: 'Java' }[s.language] || s.language }),
            el('td', {}, chip(s.status === 'AC' ? verdictChipStatus(s) : s.status)),
            el('td', { class: 'num', text: `${s.points_earned} / ${s.problem.total_points}` }),
            el('td', { class: 'num', text: fmtMs(s.execution_time) }),
            el('td', { class: 'num', text: fmtKb(s.memory_used) }),
            el('td', { class: 'muted', text: fmtWhen(s.submitted_at) }));
        return row;
    }));
}

// The server marks any submission that earned points as AC; show full marks as AC and partial scores as PARTIAL
function verdictChipStatus(s) {
    return s.points_earned >= s.problem.total_points ? 'AC' : 'PARTIAL';
}

function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
    // The contest is served over plain HTTP on a LAN address, where the Clipboard API is unavailable
    const area = el('textarea', { style: 'position:fixed;opacity:0' });
    area.value = text;
    document.body.append(area);
    area.select();
    const ok = document.execCommand('copy');
    area.remove();
    return ok ? Promise.resolve() : Promise.reject(new Error('copy failed'));
}

async function showSubmission(id, token) {
    const result = await api(`/submission/${id}`);
    if (token !== routeToken) return;
    if (!result.ok) {
        toast('Could not open that submission.', 'error');
        location.hash = '#/submissions';
        return;
    }

    const s = result.data;
    const verdict = verdictFor(s.points_earned, s.problem.total_points, s.batch_results);
    setTitle(`Submission #${s.id}`);

    const codeHost = el('div', { class: 'code-readonly' });
    const detail = $('#submissionDetail');
    detail.replaceChildren(
        el('div', { class: 'page-head' },
            el('div', {},
                el('h1', {}, el('a', { href: `#/problems/${Number(s.problem_id)}`, text: s.problem.title, style: 'color: inherit;' })),
                el('p', { class: 'sub', text: `Submission #${s.id} · ${fmtWhen(s.submitted_at)}` }))),
        el('div', { class: 'panel' }, el('div', { class: 'panel-body' },
            el('div', { class: 'result-head' },
                el('span', { class: `verdict ${verdict.cls}`, text: verdict.label }),
                el('div', { class: 'stat-row' },
                    el('span', {}, 'Score ', el('b', { text: `${s.points_earned} / ${s.problem.total_points}` })),
                    el('span', {}, 'Language ', el('b', { text: { python: 'Python', cpp: 'C++', java: 'Java' }[s.language] || s.language })))),
            renderBatches(s.batch_results || []))),
        el('div', { class: 'panel' },
            el('div', { class: 'panel-head' },
                el('span', { text: 'Your code' }),
                el('div', { class: 'd-flex gap-2' },
                    el('button', {
                        type: 'button', class: 'btn btn-secondary btn-sm', text: 'Copy',
                        onclick: async event => {
                            try { await copyText(s.code); event.target.textContent = 'Copied'; }
                            catch (e) { toast('Could not copy. Select the code and copy it manually.', 'error'); }
                            setTimeout(() => { event.target.textContent = 'Copy'; }, 1500);
                        },
                    }),
                    el('button', {
                        type: 'button', class: 'btn btn-primary btn-sm', text: 'Edit this code',
                        onclick: () => {
                            storage.set(draftKey(s.problem_id, s.language), s.code);
                            storage.set(`cms:lang:${state.user}`, s.language);
                            location.hash = `#/problems/${Number(s.problem_id)}`;
                        },
                    }))),
            codeHost));

    CodeMirror(codeHost, {
        value: s.code,
        mode: CM_MODES[s.language] || 'text/plain',
        theme: 'contest',
        readOnly: true,
        lineNumbers: true,
        viewportMargin: Infinity,
    });
}

// ---------------------------------------------------------------- leaderboard
function renderLeaderboard() {
    const data = state.leaderboard;
    if (!data) return;
    $('#leaderboardFrozenAlert').hidden = !data.is_frozen;
    $('#leaderboardEmpty').hidden = data.users.length > 0;
    $('#leaderboardTable').hidden = data.users.length === 0;

    $('#leaderboardTable thead tr').replaceChildren(
        el('th', { text: 'Rank' }),
        el('th', { text: 'Participant' }),
        ...data.problems.map(p => el('th', { class: 'lb-cell', title: p.title, text: p.shortname })),
        el('th', { class: 'num', text: 'Total' }));

    // Per problem, the earliest successful submission time gets a highlight (times are HH:MM:SS within one day)
    const firstSolve = {};
    data.users.forEach(user => user.problem_points.forEach((cell, i) => {
        if (cell.points > 0 && cell.submission_time && (!firstSolve[i] || cell.submission_time < firstSolve[i].time)) {
            firstSolve[i] = { time: cell.submission_time, user: user.username };
        }
    }));

    let previousTotal = null, previousRank = 0;
    $('#leaderboardList').replaceChildren(...data.users.map((user, index) => {
        const rank = user.total_points === previousTotal ? previousRank : index + 1;
        previousTotal = user.total_points;
        previousRank = rank;
        const isMe = user.username === state.user;
        const medal = { 1: '🥇', 2: '🥈', 3: '🥉' }[rank];

        return el('tr', { class: isMe ? 'me' : '' },
            el('td', { class: `rank r${rank}`, text: user.total_points > 0 && medal ? medal : rank }),
            el('td', {}, user.username, isMe ? el('span', { class: 'you-tag', text: 'you' }) : null),
            ...user.problem_points.map((cell, i) => el('td', {
                class: `lb-cell${cell.points > 0 && firstSolve[i] && firstSolve[i].user === user.username ? ' first' : ''}${cell.points > 0 ? '' : ' empty-cell'}`,
            }, cell.points > 0 ? el('div', { class: 'lb-score' }, String(cell.points), el('small', { text: cell.submission_time || '' })) : '·')),
            el('td', { class: 'num' }, el('strong', { text: user.total_points })));
    }));
}

// ---------------------------------------------------------------- admin
function renderAdminProblems() {
    const body = $('#adminProblemList');
    $('#adminProblemsEmpty').hidden = state.problems.length > 0;
    body.replaceChildren(...state.problems.map(problem => el('tr', {},
        el('td', { class: 'muted', text: problem.shortname }),
        el('td', { text: problem.title }),
        el('td', {}, difficultyChip(problem.difficulty)),
        el('td', { class: 'muted', text: fmtLimits(problem) }),
        el('td', { class: 'num', text: problem.total_points }),
        el('td', { class: 'num', text: problem.submission_count ?? 0 }),
        el('td', { class: 'num' },
            el('div', { class: 'd-inline-flex gap-1' },
                el('a', { class: 'btn btn-quiet btn-sm', href: `#/problems/${Number(problem.id)}`, text: 'View' }),
                el('button', { type: 'button', class: 'btn btn-quiet btn-sm', text: 'Edit',
                    onclick: () => window.open(`/problem_creation.html?edit=${Number(problem.id)}`, '_blank') }),
                el('button', { type: 'button', class: 'btn btn-quiet btn-sm', text: 'Rejudge',
                    onclick: () => rejudgeProblem(problem) }),
                el('button', { type: 'button', class: 'btn btn-danger btn-sm', text: 'Delete',
                    onclick: () => deleteProblem(problem) }))))));
}

async function deleteProblem(problem) {
    const count = problem.submission_count || 0;
    const warning = count
        ? `Delete "${problem.title}" and its ${count} submission${count === 1 ? '' : 's'}? This cannot be undone.`
        : `Delete "${problem.title}"? This cannot be undone.`;
    if (!confirm(warning)) return;
    const result = await api(`/problem/${Number(problem.id)}`, { method: 'DELETE' });
    if (result.ok) {
        toast(`Deleted "${problem.title}".`, 'success');
        await Promise.all([loadProblems(), loadSubmissions()]);
        loadLeaderboard();
    } else {
        toast(friendlyError(result), 'error');
    }
}

async function loadSettings() {
    const result = await api('/contest_settings');
    if (!result.ok) return;
    state.settings = result.data;
    $('#contestName').value = result.data.contest_name || '';
    $('#leaderboardFrozen').checked = !!result.data.leaderboard_frozen;
    $('#submissionStop').checked = !!result.data.submissions_stopped;
    $('#startTime').value = toLocalInput(result.data.start_time);
    $('#endTime').value = toLocalInput(result.data.end_time);
    $('#timeZoneHint').textContent = `Times use this computer's time zone (${Intl.DateTimeFormat().resolvedOptions().timeZone}).`;
}

/** ISO instant -> value for <input type="datetime-local"> in local time ('' when unset). */
function toLocalInput(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}T${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

/** <input type="datetime-local"> value (local time) -> ISO instant, or null when empty. */
function fromLocalInput(value) {
    const moment = value ? new Date(value) : null;
    return moment && !Number.isNaN(moment.getTime()) ? moment.toISOString().replace(/\.\d{3}Z$/, 'Z') : null;
}

function setContestName(name) {
    state.contestName = name || 'Coding Contest';
    $('#brandName').textContent = state.contestName;
}

function showFormError(id, message) {
    const box = $(id);
    box.textContent = message || '';
    box.classList.toggle('show', !!message);
}

function wireAdmin() {
    $('#newProblemBtn').addEventListener('click', () => window.open('/problem_creation.html', '_blank'));

    $('#createUserForm').addEventListener('submit', async event => {
        event.preventDefault();
        showFormError('#createUserError', '');
        const button = $('#createUserButton');
        setBusy(button, true, 'Creating...');
        try {
            const result = await postJson('/create_user', {
                username: $('#newUsername').value.trim(),
                email: $('#newEmail').value.trim(),
                password: $('#newPassword').value,
            });
            if (result.ok) {
                toast(`Account "${$('#newUsername').value.trim()}" created.`, 'success');
                event.target.reset();
                loadUserList();
                loadLeaderboard();
            } else {
                showFormError('#createUserError', friendlyError(result));
            }
        } catch (error) {
            showFormError('#createUserError', 'Could not reach the server.');
        } finally {
            setBusy(button, false);
        }
    });

    let bulkPdfUrl = null;
    $('#bulkUsersForm').addEventListener('submit', async event => {
        event.preventDefault();
        showFormError('#bulkError', '');
        $('#bulkResult').hidden = true;
        const count = parseInt($('#bulkCount').value, 10);
        const prefix = $('#bulkPrefix').value.trim();
        const button = $('#bulkButton');
        setBusy(button, true, 'Creating accounts...');
        try {
            const response = await fetch('/create_users_batch', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ count, prefix }),
            });
            if (response.redirected && new URL(response.url).pathname === '/login') {
                window.location.href = '/login';
                return;
            }
            if (!response.ok) {
                let data = null;
                try { data = await response.json(); } catch (e) { /* not JSON */ }
                showFormError('#bulkError', friendlyError({ status: response.status, data }));
                return;
            }

            const blob = await response.blob();
            if (bulkPdfUrl) URL.revokeObjectURL(bulkPdfUrl);
            bulkPdfUrl = URL.createObjectURL(blob);
            const match = (response.headers.get('Content-Disposition') || '').match(/filename="([^"]+)"/);
            const filename = match ? match[1] : 'accounts.pdf';

            const link = $('#bulkDownloadAgain');
            link.href = bulkPdfUrl;
            link.download = filename;
            link.click(); // start the first download straight away

            $('#bulkResultTitle').textContent = `Created ${response.headers.get('X-Accounts-Created')} accounts: ` +
                `${response.headers.get('X-First-Username')} to ${response.headers.get('X-Last-Username')}.`;
            $('#bulkResult').hidden = false;
            toast('Accounts created. The PDF has been downloaded.', 'success');
            loadUserList();
            loadLeaderboard();
        } catch (error) {
            showFormError('#bulkError', 'Could not reach the server.');
        } finally {
            setBusy(button, false);
        }
    });

    $$('[data-clear-time]').forEach(button => button.addEventListener('click', () => { $(`#${button.dataset.clearTime}`).value = ''; }));

    $('#contestSettingsForm').addEventListener('submit', async event => {
        event.preventDefault();
        const button = $('#saveSettingsButton');
        setBusy(button, true, 'Saving...');
        try {
            const name = $('#contestName').value.trim();
            const result = await postJson('/update_contest_settings', {
                contest_name: name,
                leaderboard_frozen: $('#leaderboardFrozen').checked,
                submissions_stopped: $('#submissionStop').checked,
                start_time: fromLocalInput($('#startTime').value),
                end_time: fromLocalInput($('#endTime').value),
            });
            if (result.ok) {
                toast('Settings saved.', 'success');
                setContestName(name);
                loadContestState();
                setTitle('Admin');
                loadLeaderboard();
            } else {
                toast(friendlyError(result), 'error');
            }
        } catch (error) {
            toast('Could not reach the server.', 'error');
        } finally {
            setBusy(button, false);
        }
    });
}

// ---------------------------------------------------------------- admin: accounts
let adminUsers = [];
let resetCredentials = null;

async function loadUserList() {
    const result = await api('/admin/users');
    if (!result.ok) return;
    adminUsers = result.data;
    renderUserList();
}

function renderUserList() {
    const query = $('#userSearch').value.trim().toLowerCase();
    const shown = adminUsers.filter(user => !query || user.username.toLowerCase().includes(query));
    $('#userCount').textContent = adminUsers.length ? `(${adminUsers.length})` : '';
    $('#usersEmpty').hidden = shown.length > 0;
    $('#userTable').hidden = shown.length === 0;
    $('#userList').replaceChildren(...shown.map(user => el('tr', {},
        el('td', { text: user.username }),
        el('td', { class: 'muted', text: user.email }),
        el('td', {}, el('span', { class: `chip ${user.disabled ? 'wa' : 'ac'}`, text: user.disabled ? 'Disabled' : 'Active' })),
        el('td', { class: 'num', text: user.submission_count }),
        el('td', { class: 'num' }, el('div', { class: 'd-inline-flex gap-1' },
            el('button', { type: 'button', class: 'btn btn-quiet btn-sm', text: 'Reset password', onclick: () => resetPassword(user) }),
            el('button', { type: 'button', class: 'btn btn-quiet btn-sm', text: user.disabled ? 'Enable' : 'Disable',
                onclick: () => setUserDisabled(user, !user.disabled) }),
            el('button', { type: 'button', class: 'btn btn-danger btn-sm', text: 'Delete', onclick: () => deleteUser(user) }))))));
}

async function resetPassword(user) {
    if (!confirm(`Give ${user.username} a new password? The old one stops working and they are signed out.`)) return;
    const result = await api(`/admin/users/${user.id}/reset_password`, { method: 'POST' });
    if (!result.ok) {
        toast(friendlyError(result), 'error');
        return;
    }
    resetCredentials = result.data;
    $('#resetUser').textContent = result.data.username;
    $('#resetPassword').textContent = result.data.password;
    $('#resetResult').hidden = false;
    $('#resetResult').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

async function setUserDisabled(user, disabled) {
    if (disabled && !confirm(`Disable ${user.username}? They are signed out and cannot log in until you enable the account again.`)) return;
    const result = await postJson(`/admin/users/${user.id}/disabled`, { disabled });
    if (result.ok) {
        toast(`${user.username} ${disabled ? 'disabled' : 'enabled'}.`, 'success');
        loadUserList();
        loadLeaderboard();
    } else {
        toast(friendlyError(result), 'error');
    }
}

async function deleteUser(user) {
    const count = user.submission_count;
    const warning = count
        ? `Delete ${user.username} and their ${count} submission${count === 1 ? '' : 's'}? This cannot be undone.`
        : `Delete ${user.username}? This cannot be undone.`;
    if (!confirm(warning)) return;
    const result = await api(`/admin/users/${user.id}`, { method: 'DELETE' });
    if (result.ok) {
        toast(`${user.username} deleted.`, 'success');
        loadUserList();
        loadLeaderboard();
    } else {
        toast(friendlyError(result), 'error');
    }
}

function wireAccounts() {
    $('#userSearch').addEventListener('input', renderUserList);
    $('#resetDismiss').addEventListener('click', () => {
        resetCredentials = null;
        $('#resetPassword').textContent = '';
        $('#resetResult').hidden = true;
    });
    $('#resetCopy').addEventListener('click', async event => {
        try {
            await copyText(resetCredentials.password);
            event.target.textContent = 'Copied';
        } catch (e) {
            toast('Could not copy. Select the password and copy it manually.', 'error');
        }
        setTimeout(() => { event.target.textContent = 'Copy'; }, 1500);
    });
    $('#resetSlip').addEventListener('click', async () => {
        const response = await fetch('/admin/slip_pdf', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(resetCredentials),
        });
        if (response.ok) saveBlob(await response.blob(), `login-${resetCredentials.username}.pdf`);
        else toast('Could not create the PDF.', 'error');
    });
}

// ---------------------------------------------------------------- admin: rejudge
let rejudgeTimer = null;
let rejudgeWasRunning = false;

async function pollRejudge() {
    clearTimeout(rejudgeTimer);
    const result = await api('/admin/rejudge_status');
    if (!result.ok) return;
    const job = result.data;
    const box = $('#rejudgeStatus');
    if (job.running) {
        rejudgeWasRunning = true;
        box.hidden = false;
        $('#rejudgeStatusText').textContent = `Rejudging "${job.title}": ${job.done + job.failed} of ${job.total} done...`;
        rejudgeTimer = setTimeout(pollRejudge, 1500);
        return;
    }
    box.hidden = true;
    if (rejudgeWasRunning) {
        rejudgeWasRunning = false;
        const summary = `Rejudge of "${job.title}" finished: ${job.done} rejudged` + (job.failed ? `, ${job.failed} failed` : '') + '.';
        toast(job.error ? `${summary} ${job.error}` : summary, job.error || job.failed ? 'error' : 'success', 8000);
        loadProblems();
        loadLeaderboard();
    }
}

async function rejudgeProblem(problem) {
    const count = problem.submission_count || 0;
    if (!count) {
        toast('There are no submissions for this problem yet.', 'info');
        return;
    }
    if (!confirm(`Rejudge all ${count} submission${count === 1 ? '' : 's'} for "${problem.title}" using its current tests? Scores and the leaderboard can change.`)) return;
    const result = await api(`/admin/problem/${problem.id}/rejudge`, { method: 'POST' });
    if (result.ok) pollRejudge();
    else toast(friendlyError(result), 'error');
}

// ---------------------------------------------------------------- admin: submissions
const adminSubs = { items: [], total: 0 };

function adminSubsQuery(offset) {
    const params = new URLSearchParams({ limit: '50', offset: String(offset) });
    const problem = $('#filterProblem').value, user = $('#filterUser').value.trim(), status = $('#filterStatus').value;
    if (problem) params.set('problem_id', problem);
    if (user) params.set('username', user);
    if (status) params.set('status', status);
    return params;
}

function fillProblemFilter() {
    const select = $('#filterProblem');
    const chosen = select.value;
    select.replaceChildren(el('option', { value: '', text: 'All problems' }),
        ...state.problems.map(p => el('option', { value: p.id, text: `${p.shortname}. ${p.title}` })));
    select.value = chosen;
}

async function loadAdminSubmissions(reset = true) {
    if (reset) adminSubs.items = [];
    fillProblemFilter();
    const result = await api(`/admin/submissions?${adminSubsQuery(adminSubs.items.length)}`);
    if (!result.ok) return;
    adminSubs.items.push(...result.data.items);
    adminSubs.total = result.data.total;
    renderAdminSubmissions();
}

function renderAdminSubmissions() {
    $('#adminSubsTotal').textContent = adminSubs.total ? `(${adminSubs.total})` : '';
    $('#adminSubsEmpty').hidden = adminSubs.items.length > 0;
    $('#adminSubsTable').hidden = adminSubs.items.length === 0;
    $('#adminSubsMore').hidden = adminSubs.items.length >= adminSubs.total;
    $('#adminSubsList').replaceChildren(...adminSubs.items.map(s => el('tr',
        { class: 'clickable', onclick: event => { if (!event.target.closest('a')) location.hash = `#/admin/submissions/${s.id}`; } },
        el('td', {}, el('a', { href: `#/admin/submissions/${s.id}`, text: String(s.id) })),
        el('td', { text: s.username }),
        el('td', { text: `${s.problem.shortname}. ${s.problem.title}` }),
        el('td', { text: { python: 'Python', cpp: 'C++', java: 'Java' }[s.language] || s.language }),
        el('td', {}, chip(s.status === 'AC' && s.points_earned < s.problem.total_points ? 'PARTIAL' : s.status)),
        el('td', { class: 'num', text: `${s.points_earned} / ${s.problem.total_points}` }),
        el('td', { class: 'num', text: fmtMs(s.execution_time) }),
        el('td', { class: 'muted', text: fmtWhen(s.submitted_at) }))));
}

async function showAdminSubmission(id, token) {
    const result = await api(`/admin/submission/${id}`);
    if (token !== routeToken) return;
    if (!result.ok) {
        toast('Could not open that submission.', 'error');
        location.hash = '#/admin/submissions';
        return;
    }

    const s = result.data;
    const verdict = verdictFor(s.points_earned, s.problem.total_points, s.batch_results);
    const host = el('div', { class: 'code-readonly' });
    const rejudgeButton = el('button', { type: 'button', class: 'btn btn-primary btn-sm', text: 'Rejudge' });
    rejudgeButton.addEventListener('click', async () => {
        setBusy(rejudgeButton, true, 'Rejudging...');
        const response = await api(`/admin/submission/${s.id}/rejudge`, { method: 'POST' });
        if (response.ok) {
            toast(`Rejudged: ${response.data.points_earned} / ${response.data.problem.total_points} points.`, 'success');
            showAdminSubmission(id, routeToken);
        } else {
            toast(friendlyError(response), 'error');
            setBusy(rejudgeButton, false);
        }
    });

    $('#adminSubmissionDetail').replaceChildren(
        el('div', { class: 'page-head' },
            el('div', {},
                el('h1', { text: `Submission #${s.id}` }),
                el('p', { class: 'sub', text: `${s.username} · ${s.problem.shortname}. ${s.problem.title} · ${fmtWhen(s.submitted_at)}` })),
            rejudgeButton),
        el('div', { class: 'panel' }, el('div', { class: 'panel-body' },
            el('div', { class: 'result-head' },
                el('span', { class: `verdict ${verdict.cls}`, text: s.status === 'ERROR' ? 'Judge error' : verdict.label }),
                el('div', { class: 'stat-row' },
                    el('span', {}, 'Score ', el('b', { text: `${s.points_earned} / ${s.problem.total_points}` })),
                    el('span', {}, 'Language ', el('b', { text: { python: 'Python', cpp: 'C++', java: 'Java' }[s.language] || s.language })),
                    s.execution_time ? el('span', {}, 'Time ', el('b', { text: fmtMs(s.execution_time) })) : null,
                    s.memory_used ? el('span', {}, 'Memory ', el('b', { text: fmtKb(s.memory_used) })) : null)),
            renderBatches(s.batch_results || []))),
        el('div', { class: 'panel' },
            el('div', { class: 'panel-head', text: 'Submitted code' }),
            host));

    const editorView = CodeMirror(host, {
        value: s.code,
        mode: CM_MODES[s.language] || 'text/plain',
        theme: 'contest',
        readOnly: true,
        lineNumbers: true,
        viewportMargin: Infinity,
    });
    requestAnimationFrame(() => editorView.refresh());
}

function wireAdminSubmissions() {
    $('#adminSubsFilter').addEventListener('submit', event => {
        event.preventDefault();
        loadAdminSubmissions(true);
    });
    $('#adminSubsMoreBtn').addEventListener('click', () => loadAdminSubmissions(false));
}

// ---------------------------------------------------------------- router
function showView(name) {
    $$('[data-view]').forEach(section => { section.hidden = section.dataset.view !== name; });
}

async function route() {
    const token = ++routeToken;
    const [section = 'problems', arg, arg2] = (location.hash.replace(/^#\/?/, '') || 'problems').split('/');
    $$('[data-nav]').forEach(link => link.classList.toggle('active', link.dataset.nav === section));
    window.scrollTo(0, 0);

    if (section === 'problems' && arg) {
        showView('problem');
        await showProblem(Number(arg), token);
    } else if (section === 'submissions' && arg) {
        showView('submission');
        await showSubmission(Number(arg), token);
    } else if (section === 'submissions') {
        showView('submissions');
        setTitle('Submissions');
        loadSubmissions();
    } else if (section === 'leaderboard') {
        showView('leaderboard');
        setTitle('Leaderboard');
        loadLeaderboard();
    } else if (section === 'admin') {
        if (!state.isAdmin) { location.hash = '#/problems'; return; }
        const tab = ['users', 'problems', 'submissions', 'settings'].includes(arg) ? arg : 'users';
        const detailId = tab === 'submissions' && /^\d+$/.test(arg2 || '') ? Number(arg2) : null;
        showView('admin');
        setTitle('Admin');
        $$('[data-admin-tab]').forEach(link => link.classList.toggle('active', link.dataset.adminTab === tab));
        const visiblePanel = detailId ? 'submission' : tab;
        $$('[data-admin-panel]').forEach(panel => { panel.hidden = panel.dataset.adminPanel !== visiblePanel; });
        if (tab === 'users') loadUserList();
        if (tab === 'problems') { loadProblems(); pollRejudge(); }
        if (tab === 'settings') loadSettings();
        if (tab === 'submissions') {
            if (detailId) await showAdminSubmission(detailId, token);
            else loadAdminSubmissions(true);
        }
    } else {
        showView('problems');
        setTitle('Problems');
        loadProblems();
        loadSubmissions();
    }
}

// ---------------------------------------------------------------- judge health (admins)
async function checkJudge(refresh = false) {
    const result = await api(`/judge_status${refresh ? '?refresh=1' : ''}`);
    const banner = $('#judgeBanner');
    if (!result.ok || !result.data) return;
    const { ok, warning, message } = result.data;
    banner.hidden = ok && !warning;
    banner.classList.toggle('warning', ok && !!warning);
    $('#judgeBannerText').textContent = ok
        ? message
        : `Judge unavailable: ${message} Contestants cannot run or submit code until this is fixed.`;
}

function startJudgeMonitor() {
    $('#judgeRecheck').addEventListener('click', () => checkJudge(true));
    checkJudge();
    setInterval(checkJudge, 30000);
}

// ---------------------------------------------------------------- init
function wireProblemPage() {
    $('#language').addEventListener('change', () => {
        saveDraft.flush(); // store the code under the language it was written in
        storage.set(`cms:lang:${state.user}`, $('#language').value);
        loadDraft();
    });

    $('#resetCode').addEventListener('click', () => {
        if (!confirm('Replace your code with the starter template? This cannot be undone.')) return;
        loadingDraft = true;
        editor.setValue(STARTERS[editorLanguage]);
        loadingDraft = false;
        storage.set(draftKey(currentProblem.id, editorLanguage), STARTERS[editorLanguage]);
    });

    $('#runBtn').addEventListener('click', runCode);
    $('#submitBtn').addEventListener('click', submitCode);
}

// Saved drafts live in this browser, so on a shared computer they must not outlive the person who wrote them
function clearSavedData() {
    try {
        Object.keys(localStorage).filter(key => key.startsWith('cms:')).forEach(key => localStorage.removeItem(key));
    } catch (e) { /* storage unavailable */ }
}

async function init() {
    wireProblemPage();
    wireAdmin();
    wireAccounts();
    wireAdminSubmissions();
    $('form[action="/logout"]').addEventListener('submit', clearSavedData);

    const [name, admin] = await Promise.all([api('/username'), api('/check_admin')]);
    state.user = name.ok ? name.text.trim() : ''; // /username returns plain text
    state.isAdmin = !!(admin.ok && admin.data && admin.data.is_admin);

    $('#username').textContent = state.user;
    $('#avatar').textContent = state.user.charAt(0);
    $('#adminNav').hidden = !state.isAdmin;
    state.contestName = $('#brandName').textContent.trim() || state.contestName;

    if (state.isAdmin) startJudgeMonitor();

    await loadContestState();
    startContestClock();
    await Promise.all([loadProblems(), loadSubmissions()]);
    window.addEventListener('hashchange', route);
    route();

    // Live updates
    const socket = io();
    socket.on('update_leaderboard', async message => {
        await loadLeaderboard();
        if (typeof message === 'string' && message) toast(message, 'info');
    });
    // An organiser changed the schedule or the submission switch
    socket.on('contest_changed', async () => {
        await loadContestState();
        loadProblems();
        loadLeaderboard();
    });
    socket.on('new_problem', problem => {
        loadProblems();
        toast(`New problem added: ${problem.title}`, 'info');
    });
    // A problem was edited or deleted: refresh the lists, and the statement if it is open
    socket.on('problems_changed', async () => {
        await Promise.all([loadProblems(), loadSubmissions()]);
        if (currentProblem && /^#\/problems\/\d+$/.test(location.hash)) refreshOpenProblem();
    });
}

document.addEventListener('DOMContentLoaded', init);
