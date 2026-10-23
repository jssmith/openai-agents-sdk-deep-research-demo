// Report rendering and download, shared by the conversation and report pages.
// Requires marked (vendored in public/vendor, so it loads without internet).

function escapeHTML(value = '') {
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

// Link and image targets the report may use. Anything else (javascript:,
// data:, ...) could run code in this page.
function isSafeUrl(href = '') {
    return /^(https?:|mailto:|#|\/(?!\/))/i.test(href.trim())
        || /^temp_images\//.test(href.trim());
}

// The report is model-generated, and marked does not sanitize: show raw HTML
// as text, and render links and images with unsafe targets as plain text.
// Returning false falls back to marked's own renderer.
marked.use({
    renderer: {
        html(token) {
            return escapeHTML(token.text);
        },
        link(token) {
            return isSafeUrl(token.href) ? false : this.parser.parseInline(token.tokens);
        },
        image(token) {
            return isSafeUrl(token.href) ? false : escapeHTML(token.text);
        },
    },
});

function renderMarkdown(markdown = '') {
    // "~5" reads as strikethrough-prone shorthand; spell it out.
    return marked.parse(markdown.replace(/~(?=\d)/g, 'approximately '));
}

function titleFromMarkdown(markdown = '', fallback = 'Research report') {
    const heading = markdown.match(/^\s*#\s+(.+?)\s*$/m);
    return heading ? heading[1].trim() : fallback;
}

function downloadMarkdown(content, workflowId) {
    const blob = new Blob([content], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `research-report-${workflowId}.md`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}
