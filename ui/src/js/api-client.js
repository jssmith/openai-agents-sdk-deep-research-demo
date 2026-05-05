// Research API Client
// Handles communication with FastAPI backend

class ResearchClient {
    constructor(baseUrl = 'http://localhost:8234') {
        this.baseUrl = baseUrl;
        this.workflowId = null;
    }

    async startResearch(query) {
        const response = await fetch(`${this.baseUrl}/api/start-research`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ query })
        });

        if (!response.ok) {
            throw new Error('Failed to start research');
        }

        const data = await response.json();
        this.workflowId = data.workflow_id;
        return data;
    }

    async getStatus(workflowId = null) {
        const id = workflowId || this.workflowId;
        if (!id) {
            throw new Error('No workflow ID available');
        }

        const response = await fetch(`${this.baseUrl}/api/status/${id}`);
        
        if (!response.ok) {
            throw new Error('Failed to get status');
        }

        return await response.json();
    }

    async submitElicitationResponse(answer, elicitationId, workflowId = null) {
        const id = workflowId || this.workflowId;
        if (!id) {
            throw new Error('No workflow ID available');
        }
        if (!elicitationId) {
            throw new Error('Elicitation id required');
        }

        const response = await fetch(
            `${this.baseUrl}/api/elicitation/${id}/${encodeURIComponent(elicitationId)}`,
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ answer }),
            }
        );

        if (!response.ok) {
            throw new Error('Failed to submit elicitation response');
        }

        return await response.json();
    }

    async getResult(workflowId = null) {
        const id = workflowId || this.workflowId;
        if (!id) {
            throw new Error('No workflow ID available');
        }

        const response = await fetch(`${this.baseUrl}/api/result/${id}`);
        
        if (!response.ok) {
            throw new Error('Result not ready or failed');
        }

        return await response.json();
    }

}

// Export for use in HTML
if (typeof window !== 'undefined') {
    window.ResearchClient = ResearchClient;
}
