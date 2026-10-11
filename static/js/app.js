'use strict';

/* ==========================================================================
   Contest single-page app. Routes live in the URL hash:
     #/problems  #/problems/<id>  #/submissions  #/submissions/<id>
     #/leaderboard  #/admin/<users|problems|settings>
   All server data is inserted with textContent (see el()); the only HTML we
   ever inject is markdown, which goes through DOMPurify.
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

function renderMarkdown(text) {
    return DOMPurify.sanitize(marked.parse(text ?? ''));
}

// Links inside problem statements open in a new tab
DOMPurify.addHook('afterSanitizeAttributes', node => {
    if (node.tagName === 'A') {
        node.setAttribute('target', '_blank');
        node.setAttribute('rel', 'noopener noreferrer');
    }
});

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
        throw new Error('Signed out');
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

// ---------------------------------------------------------------- state
const state = {
    user: '',
    isAdmin: false,
    contestName: document.title,
    problems: [],
    submissions: [],
    leaderboard: null,
    settings: null,
};

let routeToken = 0;

function setTitle(part) {
    document.title = part ? `${part} · ${state.contestName}` : state.contestName;
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
        toast('Could not open that problem.', 'error');
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
    $('#problemStatement').innerHTML = renderMarkdown(currentProblem.description);

    ensureEditor();
    const savedLanguage = storage.get(`cms:lang:${state.user}`);
    if (STARTERS[savedLanguage]) $('#language').value = savedLanguage;
    loadDraft();
    $('#resultPanel').hidden = true;
    requestAnimationFrame(() => editor.refresh());
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
            }, cell.points > 0 ? [String(cell.points), el('small', { text: cell.submission_time || '' })] : '·')),
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
        el('td', { class: 'num' }, el('a', { href: `#/problems/${Number(problem.id)}`, text: 'View' })))));
}

async function loadSettings() {
    const result = await api('/contest_settings');
    if (!result.ok) return;
    state.settings = result.data;
    $('#contestName').value = result.data.contest_name || '';
    $('#leaderboardFrozen').checked = !!result.data.leaderboard_frozen;
    $('#submissionStop').checked = !!result.data.submissions_stopped;
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
            });
            if (result.ok) {
                toast('Settings saved.', 'success');
                setContestName(name);
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

// ---------------------------------------------------------------- router
function showView(name) {
    $$('[data-view]').forEach(section => { section.hidden = section.dataset.view !== name; });
}

async function route() {
    const token = ++routeToken;
    const [section = 'problems', arg] = (location.hash.replace(/^#\/?/, '') || 'problems').split('/');
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
        const tab = ['users', 'problems', 'settings'].includes(arg) ? arg : 'users';
        showView('admin');
        setTitle('Admin');
        $$('[data-admin-tab]').forEach(link => link.classList.toggle('active', link.dataset.adminTab === tab));
        $$('[data-admin-panel]').forEach(panel => { panel.hidden = panel.dataset.adminPanel !== tab; });
        if (tab === 'settings') loadSettings();
    } else {
        showView('problems');
        setTitle('Problems');
        loadProblems();
        loadSubmissions();
    }
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

async function init() {
    wireProblemPage();
    wireAdmin();

    const [name, admin] = await Promise.all([api('/username'), api('/check_admin')]);
    state.user = name.ok ? name.text.trim() : ''; // /username returns plain text
    state.isAdmin = !!(admin.ok && admin.data && admin.data.is_admin);

    $('#username').textContent = state.user;
    $('#avatar').textContent = state.user.charAt(0);
    $('#adminNav').hidden = !state.isAdmin;
    state.contestName = $('#brandName').textContent.trim() || state.contestName;

    await Promise.all([loadProblems(), loadSubmissions()]);
    window.addEventListener('hashchange', route);
    route();

    // Live updates
    const socket = io();
    socket.on('update_leaderboard', async message => {
        await loadLeaderboard();
        if (typeof message === 'string' && message) toast(message, 'info');
    });
    socket.on('new_problem', problem => {
        loadProblems();
        toast(`New problem added: ${problem.title}`, 'info');
    });
}

document.addEventListener('DOMContentLoaded', init);
