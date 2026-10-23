// Research API client for the FastAPI backend, which serves this page, so
// every URL is relative to the page's origin.

class ApiError extends Error {
    constructor(message, status) {
        super(message);
        this.status = status;
    }
}

async function requestJSON(url, options = {}) {
    const response = await fetch(url, options);
    if (!response.ok) {
        throw new ApiError(`${options.method || 'GET'} ${url} failed`, response.status);
    }
    return response.json();
}

const researchApi = {
    startResearch(query) {
        return requestJSON('/api/start-research', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query }),
        });
    },

    getStatus(workflowId) {
        return requestJSON(`/api/status/${encodeURIComponent(workflowId)}`);
    },

    submitElicitationResponse(workflowId, elicitationId, answer) {
        return requestJSON(
            `/api/elicitation/${encodeURIComponent(workflowId)}/${encodeURIComponent(elicitationId)}`,
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ answer }),
            }
        );
    },

    getResult(workflowId) {
        return requestJSON(`/api/result/${encodeURIComponent(workflowId)}`);
    },
};

window.ApiError = ApiError;
window.researchApi = researchApi;
