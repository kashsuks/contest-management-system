'use strict';

/* ==========================================================================
   Problem statements: Markdown plus LaTeX maths, rendered offline.
     $x_i$       inline maths        $$ ... $$   display maths
     \$          a literal dollar sign
   Maths is lifted out before Markdown runs (so Markdown cannot eat the
   backslashes or underscores), the Markdown output is sanitised by DOMPurify,
   and only then are the formulas put back, into text nodes only. Code spans
   and code blocks are left alone, and "costs $5 and $10" is not maths.
   ========================================================================== */

// Links inside problem statements open in a new tab
DOMPurify.addHook('afterSanitizeAttributes', node => {
    if (node.tagName === 'A') {
        node.setAttribute('target', '_blank');
        node.setAttribute('rel', 'noopener noreferrer');
    }
});

// Order matters: code is matched first so its dollar signs are skipped
const MATH_TOKEN = new RegExp([
    '(^|\\n)[ ]{0,3}(`{3,}|~{3,})[^\\n]*\\n[\\s\\S]*?(?:\\n[ ]{0,3}\\2[`~]*[ \\t]*(?=\\n|$)|$)', // fenced code block
    '(`+)(?:(?!\\3)[\\s\\S])+?\\3',                                                          // inline code
    '\\\\[\\\\$]',                                                                           // \\ or \$
    '\\$\\$([\\s\\S]+?)\\$\\$',                                                              // $$ display $$
    '\\$(?=[^\\s$])((?:\\\\.|[^$`\\n\\\\])*?(?:\\\\.|[^\\s\\\\$`]))\\$(?!\\d)',                // $ inline $
].join('|'), 'g');

const MAX_FORMULAS = 300;

function renderMath(source, displayMode) {
    try {
        return katex.renderToString(source, {
            displayMode,
            throwOnError: false,
            trust: false,
            strict: 'ignore',
            maxExpand: 1000,
            maxSize: 50,
        });
    } catch (e) {
        const span = document.createElement('span');
        span.className = 'math-error';
        span.textContent = (displayMode ? '$$' : '$') + source + (displayMode ? '$$' : '$');
        return span.outerHTML;
    }
}

/** Render a statement into `target`, replacing its contents. */
function renderMarkdownInto(target, text) {
    const nonce = Math.random().toString(36).slice(2, 10);
    const formulas = [];
    const marker = new RegExp(`zmath${nonce}n(\\d+)z`, 'g');

    const lifted = String(text ?? '').replace(MATH_TOKEN, (match, ...groups) => {
        const display = groups[3], inline = groups[4];
        if ((display === undefined && inline === undefined) || formulas.length >= MAX_FORMULAS) return match;
        formulas.push(display !== undefined ? { source: display, display: true } : { source: inline, display: false });
        return `zmath${nonce}n${formulas.length - 1}z`;
    });

    const fragment = DOMPurify.sanitize(marked.parse(lifted), { RETURN_DOM_FRAGMENT: true });

    if (formulas.length) {
        // A formula inside a link target or title is not maths: put its source back as plain text
        for (const element of fragment.querySelectorAll('*')) {
            for (const attribute of Array.from(element.attributes)) {
                if (!attribute.value.includes(`zmath${nonce}`)) continue;
                element.setAttribute(attribute.name, attribute.value.replace(marker, (all, index) => {
                    const formula = formulas[Number(index)];
                    return formula.display ? `$$${formula.source}$$` : `$${formula.source}$`;
                }));
            }
        }
        const walker = document.createTreeWalker(fragment, NodeFilter.SHOW_TEXT);
        const textNodes = [];
        while (walker.nextNode()) textNodes.push(walker.currentNode);
        for (const node of textNodes) {
            if (!node.nodeValue.includes(`zmath${nonce}`)) continue;
            const pieces = document.createDocumentFragment();
            let last = 0;
            for (const found of node.nodeValue.matchAll(marker)) {
                pieces.append(node.nodeValue.slice(last, found.index));
                const formula = formulas[Number(found[1])];
                const holder = document.createElement('template');
                holder.innerHTML = renderMath(formula.source, formula.display);
                pieces.append(holder.content);
                last = found.index + found[0].length;
            }
            pieces.append(node.nodeValue.slice(last));
            node.replaceWith(pieces);
        }
    }
    target.replaceChildren(fragment);
}
