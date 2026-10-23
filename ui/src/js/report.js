// Report rendering and download, shared by the conversation and report pages.
// Requires marked (loaded from the CDN before this script).

function escapeHTML(value = '') {
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

// The report is model-generated: render its markdown, but show any raw HTML
// in it as text rather than letting it into the page.
marked.use({
    renderer: {
        html(token) {
            return escapeHTML(token.text);
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
