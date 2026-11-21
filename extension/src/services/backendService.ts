import * as vscode from 'vscode';

export interface OllamaStatus {
    connected: boolean;
    base_url: string;
    version?: string;
    active_model: string;
    active_model_installed: boolean;
    error?: string;
}

export interface HealthResponse {
    status: string;
    app_name: string;
    version: string;
    timestamp: string;
    python_version: string;
    workspace_root?: string;
    ollama: OllamaStatus;
    memory?: {
        pid: number;
        process_rss_mb?: number;
        host_total_ram_gb?: number;
        host_available_ram_gb?: number;
    };
}

export interface ModelInfo {
    name: string;
    size_bytes?: number;
    modified_at?: string;
    format?: string;
    family?: string;
    parameter_size?: string;
    quantization_level?: string;
    is_active: boolean;
}

export interface ModelListResponse {
    connected: boolean;
    active_model: string;
    models: ModelInfo[];
    total_count: int;
    error?: string;
}

export interface ProjectSummary {
    workspace_root: string;
    languages: Record<string, number>;
    primary_language?: string;
    frameworks: string[];
    entry_points: string[];
    config_files: string[];
    test_directories: string[];
    total_files_scanned: number;
    structure_tree: string;
}

export interface ApprovalRequest {
    id: string;
    operation: string;
    file_path?: string;
    relative_path?: string;
    command?: string;
    diff?: string;
    original_content?: string;
    proposed_content?: string;
    additions: number;
    deletions: number;
    status: string;
    description: string;
    created_at: string;
}

export interface FileOperationResult {
    success: boolean;
    operation: string;
    file_path: string;
    relative_path: string;
    message: string;
    approval_required: boolean;
    approval_request?: ApprovalRequest;
    diff?: string;
}

type int = number;

export class BackendService {
    private backendUrl: string;

    constructor() {
        const config = vscode.workspace.getConfiguration('localforge');
        this.backendUrl = config.get<string>('backendUrl', 'http://127.0.0.1:8765').replace(/\/+$/, '');
    }

    public updateBackendUrl(url: string) {
        this.backendUrl = url.replace(/\/+$/, '');
    }

    public async checkHealth(): Promise<{ ok: boolean; data?: HealthResponse; error?: string }> {
        try {
            const controller = new AbortController();
            const timeout = setTimeout(() => controller.abort(), 4000);

            const res = await fetch(`${this.backendUrl}/health`, {
                signal: controller.signal
            });
            clearTimeout(timeout);

            if (!res.ok) {
                return { ok: false, error: `Backend responded with HTTP ${res.status}` };
            }

            const data = (await res.json()) as HealthResponse;
            return { ok: true, data };
        } catch (err: any) {
            const msg = err.name === 'AbortError' ? 'Connection timed out' : err.message || 'Cannot reach backend';
            return { ok: false, error: msg };
        }
    }

    public async listModels(): Promise<ModelListResponse> {
        try {
            const controller = new AbortController();
            const timeout = setTimeout(() => controller.abort(), 5000);

            const res = await fetch(`${this.backendUrl}/api/models`, {
                signal: controller.signal
            });
            clearTimeout(timeout);

            if (!res.ok) {
                return {
                    connected: false,
                    active_model: '',
                    models: [],
                    total_count: 0,
                    error: `HTTP ${res.status}`
                };
            }

            return (await res.json()) as ModelListResponse;
        } catch (err: any) {
            return {
                connected: false,
                active_model: '',
                models: [],
                total_count: 0,
                error: err.message || 'Cannot reach backend'
            };
        }
    }

    public async setActiveModel(modelName: string): Promise<{ success: boolean; message: string }> {
        try {
            const res = await fetch(`${this.backendUrl}/api/models/active`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ model_name: modelName })
            });

            if (!res.ok) {
                return { success: false, message: `Failed to set model: HTTP ${res.status}` };
            }

            const data: any = await res.json();
            return {
                success: true,
                message: data.warning ? `${data.active_model} activated. (Warning: ${data.warning})` : `Active model set to ${data.active_model}`
            };
        } catch (err: any) {
            return { success: false, message: err.message || 'Failed to communicate with backend' };
        }
    }

    public async streamChat(
        sessionId: string,
        message: string,
        callbacks: {
            onStart?: (model: string) => void;
            onToken: (token: string) => void;
            onDone: (fullText: string) => void;
            onError: (error: string) => void;
        },
        signal?: AbortSignal
    ): Promise<void> {
        try {
            const res = await fetch(`${this.backendUrl}/api/chat`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'text/event-stream'
                },
                body: JSON.stringify({
                    session_id: sessionId,
                    message: message
                }),
                signal: signal
            });

            if (!res.ok) {
                callbacks.onError(`Backend returned HTTP ${res.status}: ${res.statusText}`);
                return;
            }

            if (!res.body) {
                callbacks.onError('Response body is null');
                return;
            }

            const reader = res.body.getReader();
            const decoder = new TextDecoder('utf-8');
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) {
                    break;
                }

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n\n');
                // Keep the trailing incomplete block in buffer
                buffer = lines.pop() || '';

                for (const block of lines) {
                    if (!block.trim()) {
                        continue;
                    }

                    let eventType = 'message';
                    let dataStr = '';

                    const sublines = block.split('\n');
                    for (const subline of sublines) {
                        if (subline.startsWith('event: ')) {
                            eventType = subline.slice(7).trim();
                        } else if (subline.startsWith('data: ')) {
                            dataStr = subline.slice(6).trim();
                        }
                    }

                    if (eventType === 'start' && dataStr) {
                        try {
                            const parsed = JSON.parse(dataStr);
                            if (callbacks.onStart) {
                                callbacks.onStart(parsed.model || '');
                            }
                        } catch {}
                    } else if (eventType === 'token' && dataStr) {
                        try {
                            const parsed = JSON.parse(dataStr);
                            callbacks.onToken(parsed.token || '');
                        } catch {}
                    } else if (eventType === 'done' && dataStr) {
                        try {
                            const parsed = JSON.parse(dataStr);
                            callbacks.onDone(parsed.full_text || '');
                        } catch {}
                    } else if (eventType === 'error' && dataStr) {
                        try {
                            const parsed = JSON.parse(dataStr);
                            callbacks.onError(parsed.error || 'Unknown error');
                        } catch {
                            callbacks.onError(dataStr);
                        }
                    }
                }
            }
        } catch (err: any) {
            if (err.name === 'AbortError') {
                callbacks.onDone(''); // Aborted intentionally by user
            } else {
                callbacks.onError(err.message || 'Stream connection failed');
            }
        }
    }

    public async stopChat(sessionId: string): Promise<boolean> {
        try {
            const res = await fetch(`${this.backendUrl}/api/chat/stop`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_id: sessionId })
            });
            return res.ok;
        } catch {
            return false;
        }
    }

    public async clearChatHistory(sessionId: string): Promise<boolean> {
        try {
            const res = await fetch(`${this.backendUrl}/api/chat/history?session_id=${encodeURIComponent(sessionId)}`, {
                method: 'DELETE'
            });
            return res.ok;
        } catch {
            return false;
        }
    }

    public async getProjectSummary(): Promise<ProjectSummary | null> {
        try {
            const controller = new AbortController();
            const timeout = setTimeout(() => controller.abort(), 4000);
            const res = await fetch(`${this.backendUrl}/api/context/summary`, { signal: controller.signal });
            clearTimeout(timeout);
            if (res.ok) {
                return (await res.json()) as ProjectSummary;
            }
            return null;
        } catch {
            return null;
        }
    }

    public async setWorkspacePath(path: string): Promise<boolean> {
        try {
            const controller = new AbortController();
            const timeout = setTimeout(() => controller.abort(), 4000);
            const res = await fetch(`${this.backendUrl}/api/context/workspace?path=${encodeURIComponent(path)}`, {
                method: 'POST',
                signal: controller.signal
            });
            clearTimeout(timeout);
            return res.ok;
        } catch {
            return false;
        }
    }

    public async getPendingApprovals(): Promise<ApprovalRequest[]> {
        try {
            const controller = new AbortController();
            const timeout = setTimeout(() => controller.abort(), 4000);
            const res = await fetch(`${this.backendUrl}/api/approvals`, { signal: controller.signal });
            clearTimeout(timeout);
            if (res.ok) {
                return (await res.json()) as ApprovalRequest[];
            }
            return [];
        } catch {
            return [];
        }
    }

    public async resolveApproval(
        requestId: string,
        approved: boolean,
        rejectionReason?: string
    ): Promise<FileOperationResult | null> {
        try {
            const res = await fetch(`${this.backendUrl}/api/approvals/${encodeURIComponent(requestId)}/resolve`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    approved: approved,
                    rejection_reason: rejectionReason
                })
            });
            if (res.ok) {
                return (await res.json()) as FileOperationResult;
            }
            return null;
        } catch {
            return null;
        }
    }

    public async runAgentTaskStream(
        sessionId: string,
        prompt: string,
        callbacks: {
            onEvent: (eventType: string, data: any) => void;
            onError: (error: string) => void;
            onDone: () => void;
        },
        signal?: AbortSignal
    ): Promise<void> {
        const config = vscode.workspace.getConfiguration('localforge');
        const maxIterations = config.get<number>('maxIterations', 16);
        const maxRepairAttempts = config.get<number>('maxRepairAttempts', 4);
        const autoApprove = config.get<boolean>('autoApprove', false);

        try {
            const res = await fetch(`${this.backendUrl}/api/agent/run`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'text/event-stream'
                },
                body: JSON.stringify({
                    session_id: sessionId,
                    prompt: prompt,
                    max_iterations: maxIterations,
                    max_repair_attempts: maxRepairAttempts,
                    auto_approve: autoApprove
                }),
                signal: signal
            });

            if (!res.ok) {
                let errDetail = `Backend returned HTTP ${res.status}`;
                try {
                    const errJson: any = await res.json();
                    if (errJson.detail) errDetail = errJson.detail;
                } catch {}
                callbacks.onError(errDetail);
                return;
            }

            if (!res.body) {
                callbacks.onError('Response stream body is null');
                return;
            }

            const reader = res.body.getReader();
            const decoder = new TextDecoder('utf-8');
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) {
                    break;
                }

                buffer += decoder.decode(value, { stream: true });
                const blocks = buffer.split('\n\n');
                buffer = blocks.pop() || '';

                for (const block of blocks) {
                    if (!block.trim()) {
                        continue;
                    }

                    let eventType = 'message';
                    let dataStr = '';

                    for (const subline of block.split('\n')) {
                        if (subline.startsWith('event: ')) {
                            eventType = subline.slice(7).trim();
                        } else if (subline.startsWith('data: ')) {
                            dataStr = subline.slice(6).trim();
                        }
                    }

                    if (dataStr) {
                        try {
                            const parsed = JSON.parse(dataStr);
                            callbacks.onEvent(eventType, parsed);
                        } catch {
                            callbacks.onEvent(eventType, { raw: dataStr });
                        }
                    }
                }
            }
            callbacks.onDone();
        } catch (err: any) {
            if (err.name === 'AbortError') {
                callbacks.onDone();
            } else {
                callbacks.onError(err.message || 'Agent stream failed');
            }
        }
    }

    public async stopAgentTask(sessionId: string): Promise<boolean> {
        try {
            const res = await fetch(`${this.backendUrl}/api/agent/stop`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_id: sessionId })
            });
            return res.ok;
        } catch {
            return false;
        }
    }

    public async clearAllMemory(): Promise<boolean> {
        try {
            const r1 = await fetch(`${this.backendUrl}/api/memory/conversations`, { method: 'DELETE' });
            const r2 = await fetch(`${this.backendUrl}/api/memory/project`, { method: 'DELETE' });
            return r1.ok && r2.ok;
        } catch {
            return false;
        }
    }

    public async updateRuntimeSettings(settings: {
        default_model?: string;
        context_window?: number;
        max_reasoning_turns?: number;
        max_repair_attempts?: number;
        request_timeout_seconds?: number;
    }): Promise<boolean> {
        try {
            const res = await fetch(`${this.backendUrl}/api/settings`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(settings)
            });
            return res.ok;
        } catch {
            return false;
        }
    }
}
