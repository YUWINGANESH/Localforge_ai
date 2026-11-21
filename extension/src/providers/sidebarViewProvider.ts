import * as vscode from 'vscode';
import { ApprovalRequest, BackendService, HealthResponse, ModelInfo } from '../services/backendService';
import { DiffContentProvider } from './diffContentProvider';

export class SidebarViewProvider implements vscode.WebviewViewProvider {
    public static readonly viewType = 'localforge-ai-sidebar';
    private _view?: vscode.WebviewView;
    private _activeSessionId: string = 'default';
    private _currentAbortController: AbortController | null = null;
    private _pendingApprovals: ApprovalRequest[] = [];

    constructor(
        private readonly _extensionUri: vscode.Uri,
        private readonly _backendService: BackendService,
        private readonly _diffProvider?: DiffContentProvider
    ) {}

    public resolveWebviewView(
        webviewView: vscode.WebviewView,
        _context: vscode.WebviewViewResolveContext,
        _token: vscode.CancellationToken
    ) {
        this._view = webviewView;

        webviewView.webview.options = {
            enableScripts: true,
            localResourceRoots: [this._extensionUri]
        };

        webviewView.webview.html = this._getHtmlForWebview(webviewView.webview);

        // Handle messages from the webview
        webviewView.webview.onDidReceiveMessage(async (data) => {
            switch (data.type) {
                case 'refreshStatus': {
                    await this.updateStatus();
                    break;
                }
                case 'selectModel': {
                    const result = await this._backendService.setActiveModel(data.model);
                    if (result.success) {
                        vscode.window.showInformationMessage(result.message);
                    } else {
                        vscode.window.showErrorMessage(result.message);
                    }
                    await this.updateStatus();
                    break;
                }
                case 'sendMessage': {
                    await this._handleSendMessage(data.text, data.mode || 'chat');
                    break;
                }
                case 'stopGeneration': {
                    await this._handleStopGeneration();
                    break;
                }
                case 'newChat': {
                    await this._handleNewChat();
                    break;
                }
                case 'resolveApproval': {
                    await this._handleResolveApproval(data.requestId, data.approved);
                    break;
                }
                case 'openNativeDiff': {
                    await this._handleOpenNativeDiff(data.requestId);
                    break;
                }
                case 'clearMemory': {
                    const choice = await vscode.window.showWarningMessage(
                        'Clear all LocalForge-AI conversation and project memory?',
                        { modal: true },
                        'Clear Memory'
                    );
                    if (choice === 'Clear Memory') {
                        const ok = await this._backendService.clearAllMemory();
                        if (ok) {
                            this._view?.webview.postMessage({ type: 'clearMessages' });
                            vscode.window.showInformationMessage('LocalForge-AI: Memory cleared.');
                        } else {
                            vscode.window.showErrorMessage('LocalForge-AI: Failed to clear backend memory.');
                        }
                    }
                    break;
                }
                case 'saveSettings': {
                    await this._handleSaveSettings(data.settings);
                    break;
                }
                case 'loadSettings': {
                    this._sendCurrentSettings();
                    break;
                }
                case 'resetSettings': {
                    await this._handleResetSettings();
                    break;
                }
                case 'clientError': {
                    vscode.window.showErrorMessage(`LocalForge UI Error: ${data.message}`);
                    break;
                }
            }
        });

        // Initial status update
        this.updateStatus();
        this._sendCurrentSettings();
    }

    private async _handleResolveApproval(requestId: string, approved: boolean) {
        const result = await this._backendService.resolveApproval(requestId, approved);
        if (result) {
            if (result.success) {
                vscode.window.showInformationMessage(`LocalForge-AI: ${result.message}`);
            } else {
                vscode.window.showWarningMessage(`LocalForge-AI: ${result.message}`);
            }
        }
        await this.refreshApprovals();
    }

    private async _handleOpenNativeDiff(requestId: string) {
        if (!this._diffProvider) {
            return;
        }
        const approval = this._pendingApprovals.find((a) => a.id === requestId);
        if (!approval) {
            return;
        }

        const relPath = approval.relative_path || 'proposed_change';
        const leftUri = vscode.Uri.parse(`${DiffContentProvider.scheme}://original/${requestId}/${relPath}`);
        const rightUri = vscode.Uri.parse(`${DiffContentProvider.scheme}://proposed/${requestId}/${relPath}`);

        this._diffProvider.setContent(leftUri, approval.original_content || '');
        this._diffProvider.setContent(rightUri, approval.proposed_content || '');

        await vscode.commands.executeCommand(
            'vscode.diff',
            leftUri,
            rightUri,
            `LocalForge Review: ${relPath} (${approval.operation})`
        );
    }

    public async refreshApprovals() {
        if (!this._view) {
            return;
        }
        this._pendingApprovals = await this._backendService.getPendingApprovals();
        this._view.webview.postMessage({
            type: 'approvalsUpdate',
            approvals: this._pendingApprovals
        });
    }

    private async _handleSendMessage(text: string, mode: string = 'chat') {
        if (!text || !text.trim() || !this._view) {
            return;
        }

        // Cancel any existing stream first
        if (this._currentAbortController) {
            this._currentAbortController.abort();
            this._currentAbortController = null;
        }

        this._currentAbortController = new AbortController();

        this._view.webview.postMessage({
            type: 'streamStart',
            userText: text,
            mode: mode
        });

        if (mode === 'agent') {
            await this._backendService.runAgentTaskStream(
                this._activeSessionId,
                text,
                {
                    onEvent: async (eventType, data) => {
                        if (eventType === 'approval_required') {
                            await this.refreshApprovals();
                        }
                        this._view?.webview.postMessage({
                            type: 'agentEvent',
                            eventType: eventType,
                            data: data
                        });
                    },
                    onError: (error) => {
                        this._view?.webview.postMessage({ type: 'streamError', error });
                        this._currentAbortController = null;
                    },
                    onDone: () => {
                        this._view?.webview.postMessage({ type: 'streamDone', fullText: '' });
                        this._currentAbortController = null;
                        this.refreshApprovals();
                    }
                },
                this._currentAbortController.signal
            );
            return;
        }

        await this._backendService.streamChat(
            this._activeSessionId,
            text,
            {
                onStart: (model) => {
                    this._view?.webview.postMessage({ type: 'streamModelInfo', model });
                },
                onToken: (token) => {
                    this._view?.webview.postMessage({ type: 'streamToken', token });
                },
                onDone: (fullText) => {
                    this._view?.webview.postMessage({ type: 'streamDone', fullText });
                    this._currentAbortController = null;
                },
                onError: (error) => {
                    this._view?.webview.postMessage({ type: 'streamError', error });
                    this._currentAbortController = null;
                }
            },
            this._currentAbortController.signal
        );
    }

    private async _handleStopGeneration() {
        if (this._currentAbortController) {
            this._currentAbortController.abort();
            this._currentAbortController = null;
        }
        await this._backendService.stopChat(this._activeSessionId);
        await this._backendService.stopAgentTask(this._activeSessionId);
        this._view?.webview.postMessage({ type: 'streamStopped' });
    }

    private async _handleNewChat() {
        if (this._currentAbortController) {
            this._currentAbortController.abort();
            this._currentAbortController = null;
        }
        this._activeSessionId = 'session_' + Date.now();
        await this._backendService.clearChatHistory(this._activeSessionId);
        this._view?.webview.postMessage({ type: 'clearMessages' });
        vscode.window.showInformationMessage('LocalForge-AI: New conversation initialized.');
    }

    private _sendCurrentSettings() {
        const config = vscode.workspace.getConfiguration('localforge');
        const settings = {
            backendUrl: config.get<string>('backendUrl', 'http://127.0.0.1:8765'),
            ollamaUrl: config.get<string>('ollamaUrl', 'http://127.0.0.1:11434'),
            defaultModel: config.get<string>('defaultModel', 'qwen2.5-coder:7b'),
            contextWindow: config.get<number>('contextWindow', 4096),
            maxIterations: config.get<number>('maxIterations', 16),
            maxRepairAttempts: config.get<number>('maxRepairAttempts', 4),
            autoApprove: config.get<boolean>('autoApprove', false),
            requestTimeout: config.get<number>('requestTimeout', 180),
            maxObservationChars: config.get<number>('maxObservationChars', 1800),
            streamingEnabled: config.get<boolean>('streamingEnabled', true),
        };
        this._view?.webview.postMessage({ type: 'settingsLoaded', settings });
    }

    private async _handleSaveSettings(settings: Record<string, any>) {
        const config = vscode.workspace.getConfiguration('localforge');
        const target = vscode.ConfigurationTarget.Global;

        try {
            if (settings.backendUrl !== undefined) {
                await config.update('backendUrl', settings.backendUrl, target);
                this._backendService.updateBackendUrl(settings.backendUrl);
            }
            if (settings.ollamaUrl !== undefined) await config.update('ollamaUrl', settings.ollamaUrl, target);
            if (settings.defaultModel !== undefined) await config.update('defaultModel', settings.defaultModel, target);
            if (settings.contextWindow !== undefined) await config.update('contextWindow', Number(settings.contextWindow), target);
            if (settings.maxIterations !== undefined) await config.update('maxIterations', Number(settings.maxIterations), target);
            if (settings.maxRepairAttempts !== undefined) await config.update('maxRepairAttempts', Number(settings.maxRepairAttempts), target);
            if (settings.autoApprove !== undefined) await config.update('autoApprove', Boolean(settings.autoApprove), target);
            if (settings.requestTimeout !== undefined) await config.update('requestTimeout', Number(settings.requestTimeout), target);
            if (settings.maxObservationChars !== undefined) await config.update('maxObservationChars', Number(settings.maxObservationChars), target);
            if (settings.streamingEnabled !== undefined) await config.update('streamingEnabled', Boolean(settings.streamingEnabled), target);

            // Push runtime settings to backend
            await this._backendService.updateRuntimeSettings({
                default_model: settings.defaultModel,
                context_window: settings.contextWindow ? Number(settings.contextWindow) : undefined,
                max_reasoning_turns: settings.maxIterations ? Number(settings.maxIterations) : undefined,
                max_repair_attempts: settings.maxRepairAttempts ? Number(settings.maxRepairAttempts) : undefined,
                request_timeout_seconds: settings.requestTimeout ? Number(settings.requestTimeout) : undefined,
            });

            vscode.window.showInformationMessage('LocalForge-AI: Settings saved successfully.');
            await this.updateStatus();
        } catch (err: any) {
            vscode.window.showErrorMessage('LocalForge-AI: Failed to save settings — ' + (err.message || String(err)));
        }
    }

    private async _handleResetSettings() {
        const config = vscode.workspace.getConfiguration('localforge');
        const target = vscode.ConfigurationTarget.Global;
        await config.update('backendUrl', undefined, target);
        await config.update('ollamaUrl', undefined, target);
        await config.update('defaultModel', undefined, target);
        await config.update('contextWindow', undefined, target);
        await config.update('maxIterations', undefined, target);
        await config.update('maxRepairAttempts', undefined, target);
        await config.update('autoApprove', undefined, target);
        await config.update('requestTimeout', undefined, target);
        await config.update('maxObservationChars', undefined, target);
        await config.update('streamingEnabled', undefined, target);
        vscode.window.showInformationMessage('LocalForge-AI: Settings reset to defaults.');
        this._sendCurrentSettings();
    }

    public async updateStatus() {
        if (!this._view) {
            return;
        }

        const health = await this._backendService.checkHealth();
        let models: ModelInfo[] = [];
        let summary = null;

        if (health.ok) {
            // Notify backend of active workspace if workspace is open
            const folders = vscode.workspace.workspaceFolders;
            if (folders && folders.length > 0) {
                await this._backendService.setWorkspacePath(folders[0].uri.fsPath);
            }
            const modelsRes = await this._backendService.listModels();
            models = modelsRes.models;
            summary = await this._backendService.getProjectSummary();
            await this.refreshApprovals();
        }

        await this._view.webview.postMessage({
            type: 'statusUpdate',
            health: health.data,
            error: health.error,
            models: models,
            summary: summary
        });
    }

            private _getHtmlForWebview(_webview: vscode.Webview): string {
        return `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="Content-Security-Policy" content="default-src * 'unsafe-inline' 'unsafe-eval' data: blob:; script-src * 'unsafe-inline' 'unsafe-eval'; style-src * 'unsafe-inline';">
    <title>LocalForge-AI</title>
    <style>
        :root {
            --lf-bg: var(--vscode-sideBar-background, #1e1e1e);
            --lf-fg: var(--vscode-sideBar-foreground, #cccccc);
            --lf-border: var(--vscode-sideBar-border, #333333);
            --lf-accent: var(--vscode-button-background, #0e639c);
            --lf-accent-fg: var(--vscode-button-foreground, #ffffff);
            --lf-accent-hover: var(--vscode-button-hoverBackground, #1177bb);
            --lf-danger: #e51400;
            --lf-danger-hover: #c71000;
            --lf-input-bg: var(--vscode-input-background, #252526);
            --lf-input-fg: var(--vscode-input-foreground, #cccccc);
            --lf-card-bg: var(--vscode-editor-inactiveSelectionBackground, rgba(255, 255, 255, 0.04));
            --lf-user-bubble-bg: var(--vscode-button-secondaryBackground, #3a3d41);
            --lf-user-bubble-fg: var(--vscode-button-secondaryForeground, #ffffff);
            --lf-assistant-bubble-bg: var(--vscode-editor-background, #1e1e1e);
            --lf-code-bg: var(--vscode-textCodeBlock-background, #161616);
            --lf-success: #4ec9b0;
            --lf-warning: #cca700;
            --lf-error: #f14c4c;
        }

        * {
            box-sizing: border-box;
        }

        html {
            height: 100%;
        }

        body {
            margin: 0;
            padding: 8px 10px;
            font-family: var(--vscode-font-family, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif);
            font-size: var(--vscode-font-size, 13px);
            color: var(--lf-fg);
            background-color: var(--lf-bg);
            display: flex;
            flex-direction: column;
            height: 100%;
            max-height: 100vh;
            overflow: hidden;
            box-sizing: border-box;
        }

        /* Scrollbar styles */
        ::-webkit-scrollbar {
            width: 8px;
            height: 8px;
        }
        ::-webkit-scrollbar-track {
            background: rgba(0, 0, 0, 0.2);
            border-radius: 4px;
        }
        ::-webkit-scrollbar-thumb {
            background: rgba(255, 255, 255, 0.25);
            border-radius: 4px;
        }
        ::-webkit-scrollbar-thumb:hover {
            background: rgba(255, 255, 255, 0.45);
        }

        /* Header */
        .header {
            display: flex;
            flex-direction: column;
            gap: 6px;
            padding-bottom: 8px;
            border-bottom: 1px solid var(--lf-border);
            flex-shrink: 0;
        }

        .title-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
        }

        .app-title {
            font-size: 13.5px;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 6px;
        }

        .header-actions {
            display: flex;
            align-items: center;
            gap: 5px;
        }

        .status-pill {
            display: inline-flex;
            align-items: center;
            gap: 5px;
            font-size: 11px;
            padding: 2px 7px;
            border-radius: 10px;
            font-weight: 500;
        }

        .status-pill.connected {
            background-color: rgba(78, 201, 176, 0.15);
            color: var(--lf-success);
            border: 1px solid rgba(78, 201, 176, 0.4);
        }

        .status-pill.degraded {
            background-color: rgba(204, 167, 0, 0.15);
            color: var(--lf-warning);
            border: 1px solid rgba(204, 167, 0, 0.4);
        }

        .status-pill.offline {
            background-color: rgba(241, 76, 76, 0.15);
            color: var(--lf-error);
            border: 1px solid rgba(241, 76, 76, 0.4);
        }

        .icon-btn {
            background: none;
            border: 1px solid var(--lf-border);
            color: var(--lf-fg);
            cursor: pointer;
            padding: 3px 6px;
            border-radius: 4px;
            font-size: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
            line-height: 1;
        }

        .icon-btn:hover {
            background: var(--vscode-toolbar-hoverBackground, rgba(90, 93, 94, 0.31));
        }

        .icon-btn.active {
            background: var(--lf-accent);
            color: var(--lf-accent-fg);
            border-color: var(--lf-accent);
        }

        .model-row {
            display: flex;
            align-items: center;
            gap: 6px;
        }

        .model-select {
            flex: 1;
            background: var(--lf-input-bg);
            color: var(--lf-input-fg);
            border: 1px solid var(--lf-border);
            padding: 4px 6px;
            border-radius: 4px;
            font-size: 11.5px;
        }

        .workspace-row {
            display: flex;
            align-items: center;
            font-size: 11px;
            color: var(--vscode-descriptionForeground, #858585);
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid var(--lf-border);
            padding: 3px 6px;
            border-radius: 4px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }

        .diagnostics-banner {
            padding: 6px 8px;
            border-radius: 4px;
            font-size: 11px;
            line-height: 1.4;
            display: none;
        }

        .diagnostics-banner.warning {
            display: block;
            background: rgba(204, 167, 0, 0.1);
            color: var(--lf-warning);
            border-left: 3px solid var(--lf-warning);
        }

        .diagnostics-banner.error {
            display: block;
            background: rgba(241, 76, 76, 0.1);
            color: var(--lf-error);
            border-left: 3px solid var(--lf-error);
        }

        .activity-bar {
            display: none;
            align-items: center;
            gap: 6px;
            padding: 4px 8px;
            background: rgba(14, 99, 156, 0.15);
            border: 1px solid var(--lf-accent);
            border-radius: 4px;
            font-size: 11px;
        }

        .activity-bar.active {
            display: flex;
        }

        .phase-badge {
            background: var(--lf-accent);
            color: var(--lf-accent-fg);
            padding: 1px 5px;
            border-radius: 3px;
            font-size: 10px;
            font-weight: 600;
        }

        /* ── THINKING VISUALIZATION BOX ── */
        .thinking-box {
            margin: 6px 0;
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-left: 3px solid var(--lf-accent);
            border-radius: 6px;
            overflow: hidden;
            font-size: 11.5px;
            transition: all 0.2s ease;
        }

        .thinking-header {
            display: flex;
            align-items: center;
            gap: 6px;
            padding: 5px 8px;
            cursor: pointer;
            user-select: none;
            background: rgba(14, 99, 156, 0.08);
            font-weight: 500;
        }

        .thinking-header:hover {
            background: rgba(14, 99, 156, 0.18);
        }

        .thinking-icon {
            font-size: 12px;
        }

        .thinking-label {
            color: var(--lf-accent);
            font-weight: 600;
            font-size: 10.5px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .thinking-pulse {
            width: 7px;
            height: 7px;
            border-radius: 50%;
            background: var(--lf-accent);
            animation: lf-pulse 1.4s infinite ease-in-out;
            margin-left: 2px;
        }

        @keyframes lf-pulse {
            0%, 100% { transform: scale(0.8); opacity: 0.4; }
            50% { transform: scale(1.3); opacity: 1; box-shadow: 0 0 6px var(--lf-accent); }
        }

        .thinking-chevron {
            margin-left: auto;
            font-size: 9px;
            opacity: 0.6;
            transition: transform 0.2s ease;
        }

        .thinking-box.collapsed .thinking-chevron {
            transform: rotate(-90deg);
        }

        .thinking-box.collapsed .thinking-body {
            display: none;
        }

        .thinking-body {
            padding: 6px 10px;
            max-height: 200px;
            overflow-y: auto;
            border-top: 1px solid rgba(255, 255, 255, 0.05);
            background: rgba(0, 0, 0, 0.15);
        }

        .thinking-text {
            color: var(--vscode-descriptionForeground, #a0a0a0);
            white-space: pre-wrap;
            line-height: 1.45;
            font-family: var(--vscode-editor-font-family, monospace);
            font-size: 11px;
        }

        /* ── VIEW WRAPPERS (CHAT VS SETTINGS) ── */
        .view-container {
            display: flex;
            flex-direction: column;
            flex: 1;
            min-height: 0;
            overflow: hidden;
        }

        /* ── CHAT VIEW ── */
        .chat-container {
            flex: 1;
            min-height: 0;
            overflow-y: auto;
            padding: 8px 0;
            display: flex;
            flex-direction: column;
            gap: 10px;
        }

        .welcome-card {
            background: var(--lf-card-bg);
            border: 1px solid var(--lf-border);
            border-radius: 6px;
            padding: 10px 12px;
            font-size: 12px;
            line-height: 1.5;
        }

        .welcome-card h3 {
            margin: 0 0 6px 0;
            font-size: 13px;
            color: var(--vscode-textLink-foreground, #3794ff);
        }

        .welcome-card ul {
            margin: 6px 0 0 0;
            padding-left: 18px;
        }

        .msg-row {
            display: flex;
            flex-direction: column;
            gap: 4px;
            width: 100%;
        }

        .msg-role-tag {
            font-size: 10px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: var(--vscode-descriptionForeground, #858585);
        }

        .msg-bubble {
            border-radius: 6px;
            padding: 8px 10px;
            font-size: 12.5px;
            line-height: 1.5;
            word-wrap: break-word;
            overflow-wrap: break-word;
        }

        .user-msg .msg-bubble {
            background-color: var(--lf-user-bubble-bg);
            color: var(--lf-user-bubble-fg);
            border: 1px solid rgba(255, 255, 255, 0.1);
        }

        .assistant-msg .msg-bubble {
            background-color: var(--lf-assistant-bubble-bg);
            border: 1px solid var(--lf-border);
        }

        .code-block-container {
            margin: 8px 0;
            border: 1px solid var(--lf-border);
            border-radius: 4px;
            overflow: hidden;
            background: var(--lf-code-bg);
        }

        .code-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: rgba(255, 255, 255, 0.04);
            border-bottom: 1px solid var(--lf-border);
            padding: 3px 8px;
            font-size: 11px;
            color: var(--vscode-descriptionForeground, #858585);
            font-family: monospace;
        }

        .copy-btn {
            background: none;
            border: 1px solid var(--lf-border);
            color: var(--lf-fg);
            cursor: pointer;
            font-size: 10px;
            padding: 2px 6px;
            border-radius: 3px;
        }

        pre {
            margin: 0;
            padding: 8px 10px;
            overflow-x: auto;
            font-family: var(--vscode-editor-font-family, Consolas, monospace);
            font-size: 12px;
        }

        .inline-code {
            background: rgba(255, 255, 255, 0.08);
            padding: 1px 4px;
            border-radius: 3px;
            font-size: 11.5px;
        }

        .cursor-blink {
            display: inline-block;
            width: 6px;
            height: 13px;
            background-color: var(--lf-fg);
            margin-left: 2px;
            vertical-align: middle;
            animation: blink 0.9s infinite;
        }

        @keyframes blink {
            0%, 50% { opacity: 1; }
            51%, 100% { opacity: 0; }
        }

        /* Approvals */
        .approval-panel {
            display: none;
            flex-direction: column;
            gap: 8px;
            max-height: 220px;
            overflow-y: auto;
            padding: 6px 0;
            border-top: 1px solid var(--lf-border);
            flex-shrink: 0;
        }

        .approval-panel.active {
            display: flex;
        }

        .approval-card {
            background: var(--lf-card-bg);
            border: 1px solid var(--lf-warning);
            border-radius: 6px;
            padding: 8px;
            display: flex;
            flex-direction: column;
            gap: 6px;
        }

        .approval-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            font-size: 11px;
            font-weight: 600;
        }

        .op-badge {
            background: rgba(204, 167, 0, 0.2);
            color: var(--lf-warning);
            padding: 1px 6px;
            border-radius: 3px;
            font-size: 10px;
            font-family: monospace;
        }

        .diff-stats {
            font-family: monospace;
            font-size: 11px;
        }
        .stat-add { color: var(--lf-success); }
        .stat-del { color: var(--lf-error); }

        .approval-diff {
            background: var(--lf-code-bg);
            border: 1px solid var(--lf-border);
            border-radius: 4px;
            padding: 6px;
            max-height: 110px;
            overflow: auto;
            font-family: monospace;
            font-size: 11px;
            white-space: pre;
        }

        .diff-line-add { color: #4ec9b0; background: rgba(78, 201, 176, 0.1); display: block; }
        .diff-line-del { color: #f14c4c; background: rgba(241, 76, 76, 0.1); display: block; }

        .approval-actions {
            display: flex;
            gap: 6px;
        }

        .btn-approve {
            flex: 1;
            background: #2e7d32;
            color: #fff;
            border: none;
            border-radius: 4px;
            padding: 4px 8px;
            cursor: pointer;
            font-size: 11px;
        }
        .btn-reject {
            flex: 1;
            background: var(--lf-danger);
            color: #fff;
            border: none;
            border-radius: 4px;
            padding: 4px 8px;
            cursor: pointer;
            font-size: 11px;
        }
        .btn-diff {
            background: none;
            border: 1px solid var(--lf-border);
            color: var(--lf-fg);
            border-radius: 4px;
            padding: 4px 8px;
            cursor: pointer;
            font-size: 11px;
        }

        /* Footer */
        .footer {
            padding-top: 6px;
            border-top: 1px solid var(--lf-border);
            display: flex;
            flex-direction: column;
            gap: 6px;
            flex-shrink: 0;
        }

        .input-row {
            display: flex;
            gap: 6px;
            align-items: flex-end;
        }

        .chat-input {
            flex: 1;
            resize: none;
            height: 46px;
            background: var(--lf-input-bg);
            color: var(--lf-input-fg);
            border: 1px solid var(--lf-border);
            border-radius: 4px;
            padding: 6px 8px;
            font-family: inherit;
            font-size: 12px;
            line-height: 1.4;
        }

        .chat-input:focus {
            outline: 1px solid var(--lf-accent);
        }

        .primary-btn {
            background: var(--lf-accent);
            color: var(--lf-accent-fg);
            border: none;
            border-radius: 4px;
            padding: 0 14px;
            height: 46px;
            cursor: pointer;
            font-weight: 500;
            font-size: 12px;
        }

        .primary-btn.stop {
            background: var(--lf-danger);
        }

        .action-row {
            display: flex;
            justify-content: space-between;
            font-size: 11px;
            color: var(--vscode-descriptionForeground, #858585);
            padding-bottom: 2px;
        }

        .mode-toggle {
            display: flex;
            border: 1px solid var(--lf-border);
            border-radius: 4px;
            overflow: hidden;
        }

        .mode-btn {
            background: none;
            border: none;
            color: var(--lf-fg);
            padding: 2px 8px;
            font-size: 11px;
            cursor: pointer;
            opacity: 0.7;
        }

        .mode-btn.active {
            background: var(--lf-accent);
            color: var(--lf-accent-fg);
            opacity: 1;
            font-weight: 500;
        }

        .link-btn {
            background: none;
            border: none;
            color: var(--vscode-textLink-foreground, #3794ff);
            cursor: pointer;
            padding: 0;
            font-size: inherit;
        }
        .link-btn:hover {
            text-decoration: underline;
        }

        .plan-card, .report-card {
            background: var(--lf-card-bg);
            border: 1px solid var(--lf-border);
            border-left: 3px solid var(--lf-accent);
            border-radius: 4px;
            padding: 8px 10px;
            font-size: 12px;
            margin: 4px 0;
        }

        .report-card {
            border-left-color: var(--lf-success);
        }

        .plan-card h4, .report-card h4 {
            margin: 0 0 6px 0;
            font-size: 12px;
            color: var(--vscode-textLink-foreground, #3794ff);
        }

        .tool-pill {
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: rgba(255, 255, 255, 0.04);
            border: 1px solid var(--lf-border);
            border-radius: 4px;
            padding: 4px 8px;
            font-size: 11px;
            font-family: monospace;
            margin: 2px 0;
        }

        .tool-pill.ok { border-left: 3px solid var(--lf-success); }
        .tool-pill.fail { border-left: 3px solid var(--lf-error); }

        /* ══════════════════════════════════════════════════════════════ */
        /* ── SETTINGS VIEW (FIXED PROPER SCROLLING + NO SHRINK CARDS) ── */
        /* ══════════════════════════════════════════════════════════════ */
        #settingsView {
            display: none;
            flex-direction: column;
            flex: 1 1 0;
            min-height: 0;
            height: 100%;
            overflow: hidden;
        }

        #settingsView.active {
            display: flex;
        }

        .settings-nav-bar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 6px 0 8px 0;
            border-bottom: 1px solid var(--lf-border);
            flex-shrink: 0;
            background: var(--lf-bg);
            z-index: 10;
        }

        .btn-back-chat {
            background: none;
            border: 1px solid var(--lf-border);
            color: var(--lf-fg);
            border-radius: 4px;
            padding: 4px 8px;
            font-size: 11px;
            font-weight: 500;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 4px;
        }

        .btn-back-chat:hover {
            background: var(--vscode-toolbar-hoverBackground, rgba(90, 93, 94, 0.31));
            color: var(--vscode-textLink-foreground, #3794ff);
        }

        .settings-page-title {
            font-size: 12px;
            font-weight: 600;
            color: var(--lf-fg);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        /* 
           Crucial scrolling fix:
           1) display: block (NOT flex) so children cards DO NOT SHRINK!
           2) flex: 1 1 0 and min-height: 0 so container takes exactly available space
           3) overflow-y: auto so any content overflowing causes natural scroll
        */
        .settings-scroll-container {
            flex: 1 1 0;
            min-height: 0;
            overflow-y: auto !important;
            overflow-x: hidden;
            padding: 8px 6px 16px 0;
            display: block;
            scrollbar-width: thin;
            scrollbar-color: rgba(255, 255, 255, 0.35) rgba(0, 0, 0, 0.2);
        }

        .settings-scroll-container::-webkit-scrollbar {
            width: 8px;
            display: block;
        }
        .settings-scroll-container::-webkit-scrollbar-track {
            background: rgba(0, 0, 0, 0.25);
            border-radius: 4px;
        }
        .settings-scroll-container::-webkit-scrollbar-thumb {
            background: rgba(255, 255, 255, 0.35);
            border-radius: 4px;
        }
        .settings-scroll-container::-webkit-scrollbar-thumb:hover {
            background: rgba(255, 255, 255, 0.55);
        }

        .cfg-card {
            background: var(--lf-card-bg);
            border: 1px solid var(--lf-border);
            border-radius: 6px;
            margin-bottom: 12px;
            display: block;
            flex-shrink: 0;
        }

        .cfg-card:last-child {
            margin-bottom: 24px;
        }

        .cfg-card-header {
            padding: 6px 10px;
            background: rgba(255, 255, 255, 0.04);
            border-bottom: 1px solid var(--lf-border);
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            color: var(--lf-fg);
        }

        .cfg-card-body {
            padding: 8px 10px;
            display: flex;
            flex-direction: column;
            gap: 10px;
        }

        .cfg-item {
            display: flex;
            flex-direction: column;
            gap: 3px;
        }

        .cfg-item-inline {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
        }

        .cfg-label {
            font-size: 11.5px;
            font-weight: 500;
            color: var(--lf-fg);
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }

        .badge-tag {
            font-size: 9px;
            padding: 1px 4px;
            border-radius: 3px;
            font-weight: 600;
            text-transform: uppercase;
        }

        .badge-tag.adv {
            background: rgba(14, 99, 156, 0.25);
            color: #3794ff;
            border: 1px solid rgba(55, 148, 255, 0.4);
        }

        .badge-tag.danger {
            background: rgba(229, 20, 0, 0.2);
            color: #ff6b6b;
            border: 1px solid rgba(229, 20, 0, 0.4);
        }

        .badge-tag.rec {
            background: rgba(78, 201, 176, 0.2);
            color: var(--lf-success);
            border: 1px solid rgba(78, 201, 176, 0.4);
        }

        .cfg-help {
            font-size: 10.5px;
            color: var(--vscode-descriptionForeground, #858585);
            line-height: 1.35;
        }

        .cfg-input-text {
            background: var(--lf-input-bg);
            color: var(--lf-input-fg);
            border: 1px solid var(--lf-border);
            border-radius: 4px;
            padding: 5px 7px;
            font-size: 11.5px;
            font-family: inherit;
            width: 100%;
        }

        .cfg-input-text:focus {
            outline: 1px solid var(--lf-accent);
        }

        .range-box {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-top: 2px;
        }

        .range-box input[type="range"] {
            flex: 1;
            height: 4px;
            accent-color: var(--lf-accent);
            cursor: pointer;
        }

        .range-box input[type="number"] {
            width: 65px;
            background: var(--lf-input-bg);
            color: var(--lf-input-fg);
            border: 1px solid var(--lf-border);
            border-radius: 3px;
            padding: 3px 5px;
            font-size: 11px;
            text-align: right;
            font-family: monospace;
        }

        .range-box input[type="number"]:focus {
            outline: 1px solid var(--lf-accent);
        }

        .range-unit {
            font-size: 10px;
            color: var(--vscode-descriptionForeground, #858585);
            min-width: 25px;
        }

        /* Switch toggle */
        .switch {
            position: relative;
            display: inline-block;
            width: 32px;
            height: 17px;
            flex-shrink: 0;
        }

        .switch input {
            opacity: 0;
            width: 0;
            height: 0;
        }

        .slider-round {
            position: absolute;
            cursor: pointer;
            top: 0; left: 0; right: 0; bottom: 0;
            background-color: rgba(255, 255, 255, 0.16);
            transition: 0.2s;
            border-radius: 17px;
        }

        .slider-round:before {
            position: absolute;
            content: "";
            height: 11px;
            width: 11px;
            left: 3px;
            bottom: 3px;
            background-color: white;
            transition: 0.2s;
            border-radius: 50%;
        }

        input:checked + .slider-round {
            background-color: var(--lf-accent);
        }

        input:checked + .slider-round:before {
            transform: translateX(15px);
        }

        /* Fixed bottom action footer in settings */
        .settings-bottom-bar {
            padding-top: 8px;
            border-top: 1px solid var(--lf-border);
            display: flex;
            flex-direction: column;
            gap: 6px;
            flex-shrink: 0;
            background: var(--lf-bg);
            z-index: 10;
        }

        .settings-btn-row {
            display: flex;
            gap: 6px;
        }

        .btn-save-cfg {
            flex: 2;
            background: var(--lf-accent);
            color: var(--lf-accent-fg);
            border: none;
            border-radius: 4px;
            padding: 6px 10px;
            font-size: 11.5px;
            font-weight: 600;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 5px;
        }

        .btn-save-cfg:hover {
            background: var(--lf-accent-hover);
        }

        .btn-reset-cfg {
            flex: 1;
            background: none;
            border: 1px solid var(--lf-border);
            color: var(--lf-fg);
            border-radius: 4px;
            padding: 6px 10px;
            font-size: 11px;
            cursor: pointer;
        }

        .btn-reset-cfg:hover {
            background: rgba(255, 255, 255, 0.07);
        }

        .cfg-feedback {
            font-size: 11px;
            text-align: center;
            height: 16px;
            color: var(--lf-success);
            font-weight: 500;
        }
    </style>
</head>
<body>
    <!-- ── TOP GLOBAL HEADER ── -->
    <div class="header">
        <div class="title-row">
            <div class="app-title">
                <span>⚡</span> LocalForge-AI 2.0
            </div>
            <div class="header-actions">
                <div id="statusPill" class="status-pill offline">
                    <span>●</span> <span id="statusText">Checking...</span>
                </div>
                <button id="settingsNavBtn" class="icon-btn" title="Configure Settings">⚙</button>
            </div>
        </div>

        <div class="model-row" id="headerModelRow">
            <select id="modelSelector" class="model-select">
                <option value="qwen2.5-coder:7b">qwen2.5-coder:7b</option>
                <option value="qwen3:4b">qwen3:4b</option>
            </select>
            <button id="refreshBtn" class="icon-btn" title="Refresh Status">↻</button>
        </div>

        <div id="workspaceRow" class="workspace-row" title="Active Workspace">
            <span id="workspaceBadge">📁 Scanning workspace...</span>
        </div>

        <div id="diagnosticsBanner" class="diagnostics-banner"></div>
        <div id="activityBar" class="activity-bar">
            <span id="activityPhase" class="phase-badge">PLAN</span>
            <span id="activityMsg">Initializing...</span>
        </div>
    </div>

    <!-- ── CHAT VIEW ── -->
    <div id="chatView" class="view-container">
        <div class="chat-container" id="chatContainer">
            <div class="welcome-card" id="welcomeCard">
                <h3>Local Autonomous Coding Agent</h3>
                <div>LocalForge-AI 2.0 is connected to your local Ollama runtime.</div>
                <ul>
                    <li><strong>Local Inference:</strong> Qwen 2.5 Coder 7B / Qwen3 4B</li>
                    <li><strong>Privacy:</strong> 100% offline, zero cloud API leakage</li>
                    <li><strong>Autonomous Tasks:</strong> Multi-step planning, coding &amp; testing</li>
                </ul>
            </div>
        </div>

        <div id="approvalPanel" class="approval-panel"></div>

        <div class="footer" id="mainFooter">
            <div class="input-row">
                <textarea id="promptInput" class="chat-input" placeholder="Ask LocalForge-AI to code, debug, or explain..."></textarea>
                <button id="actionBtn" class="primary-btn">Send</button>
            </div>
            <div class="action-row">
                <div class="mode-toggle">
                    <button id="modeChatBtn" class="mode-btn">Chat</button>
                    <button id="modeAgentBtn" class="mode-btn active">Agent (Auto)</button>
                </div>
                <div style="display:flex;gap:10px;">
                    <button id="clearMemoryBtn" class="link-btn" title="Clear persisted SQLite memory">Clear Mem</button>
                    <button id="newChatBtn" class="link-btn">+ New Chat</button>
                </div>
            </div>
        </div>
    </div>

    <!-- ── SETTINGS VIEW (SCROLLABLE & FIXED FOOTER) ── -->
    <div id="settingsView" class="view-container">
        <div class="settings-nav-bar">
            <button id="btnBackToChat" class="btn-back-chat">&larr; Back to Chat</button>
            <span class="settings-page-title">Configuration</span>
        </div>

        <div class="settings-scroll-container" id="settingsScrollContainer">
            <!-- CARD 1: GENERAL -->
            <div class="cfg-card">
                <div class="cfg-card-header">
                    <span>General Settings</span>
                </div>
                <div class="cfg-card-body">
                    <div class="cfg-item">
                        <label class="cfg-label" for="set_backendUrl">FastAPI Backend URL</label>
                        <div class="cfg-help">Local agent microservice endpoint (default: http://127.0.0.1:8765)</div>
                        <input type="text" id="set_backendUrl" class="cfg-input-text" placeholder="http://127.0.0.1:8765">
                    </div>
                    <div class="cfg-item">
                        <label class="cfg-label" for="set_ollamaUrl">Ollama Server URL</label>
                        <div class="cfg-help">Local Ollama HTTP inference daemon (default: http://127.0.0.1:11434)</div>
                        <input type="text" id="set_ollamaUrl" class="cfg-input-text" placeholder="http://127.0.0.1:11434">
                    </div>
                    <div class="cfg-item">
                        <label class="cfg-label" for="set_defaultModel">Default Model Name</label>
                        <div class="cfg-help">Model identifier loaded in Ollama for autonomous execution</div>
                        <input type="text" id="set_defaultModel" class="cfg-input-text" placeholder="qwen2.5-coder:7b">
                    </div>
                    <div class="cfg-item-inline">
                        <div>
                            <div class="cfg-label">Streaming Responses</div>
                            <div class="cfg-help">Stream tokens dynamically during Chat mode</div>
                        </div>
                        <label class="switch">
                            <input type="checkbox" id="set_streamingEnabled" checked>
                            <span class="slider-round"></span>
                        </label>
                    </div>
                </div>
            </div>

            <!-- CARD 2: AGENT LOOP CONTROL -->
            <div class="cfg-card">
                <div class="cfg-card-header">
                    <span>Agent Loop &amp; ReAct Control</span>
                </div>
                <div class="cfg-card-body">
                    <div class="cfg-item">
                        <div class="cfg-item-inline">
                            <label class="cfg-label" for="set_maxIterations">
                                Max Iterations
                                <span class="badge-tag adv">ADVANCED</span>
                            </label>
                            <span class="badge-tag rec" id="iter_badge">16 cycles</span>
                        </div>
                        <div class="cfg-help">Max reasoning and tool iterations per task before finalizing (4-64)</div>
                        <div class="range-box">
                            <input type="range" id="set_maxIterations_slider" min="4" max="64" step="1" value="16">
                            <input type="number" id="set_maxIterations" min="4" max="64" value="16">
                            <span class="range-unit">steps</span>
                        </div>
                    </div>

                    <div class="cfg-item">
                        <div class="cfg-item-inline">
                            <label class="cfg-label" for="set_maxRepairAttempts">
                                Max Self-Repair Attempts
                                <span class="badge-tag adv">ADVANCED</span>
                            </label>
                            <span class="badge-tag rec" id="repair_badge">4 attempts</span>
                        </div>
                        <div class="cfg-help">Max automated code diagnoses and edits after test failures (1-10)</div>
                        <div class="range-box">
                            <input type="range" id="set_maxRepairAttempts_slider" min="1" max="10" step="1" value="4">
                            <input type="number" id="set_maxRepairAttempts" min="1" max="10" value="4">
                            <span class="range-unit">runs</span>
                        </div>
                    </div>

                    <div class="cfg-item-inline" style="padding-top: 4px;">
                        <div>
                            <div class="cfg-label">
                                Auto-Approve File Ops
                                <span class="badge-tag danger">DANGER</span>
                            </div>
                            <div class="cfg-help">Skip user approval prompts. Changes will be written directly.</div>
                        </div>
                        <label class="switch">
                            <input type="checkbox" id="set_autoApprove">
                            <span class="slider-round"></span>
                        </label>
                    </div>
                </div>
            </div>

            <!-- CARD 3: CONTEXT & TOKENS -->
            <div class="cfg-card">
                <div class="cfg-card-header">
                    <span>Context &amp; Token Budget</span>
                </div>
                <div class="cfg-card-body">
                    <div class="cfg-item">
                        <div class="cfg-item-inline">
                            <label class="cfg-label" for="set_contextWindow">Context Window Size</label>
                            <span class="badge-tag rec" id="ctx_badge">4096 tokens</span>
                        </div>
                        <div class="cfg-help">Total token budget sent to Ollama (≤4096 recommended for 4GB VRAM)</div>
                        <div class="range-box">
                            <input type="range" id="set_contextWindow_slider" min="512" max="16384" step="512" value="4096">
                            <input type="number" id="set_contextWindow" min="512" max="32768" step="512" value="4096">
                            <span class="range-unit">tok</span>
                        </div>
                    </div>

                    <div class="cfg-item">
                        <div class="cfg-item-inline">
                            <label class="cfg-label" for="set_maxObservationChars">
                                Max Observation Chars
                                <span class="badge-tag adv">ADVANCED</span>
                            </label>
                            <span class="badge-tag rec" id="obs_badge">1800 chars</span>
                        </div>
                        <div class="cfg-help">Max stdout/stderr output passed back to agent per step (500-8000)</div>
                        <div class="range-box">
                            <input type="range" id="set_maxObservationChars_slider" min="500" max="8000" step="100" value="1800">
                            <input type="number" id="set_maxObservationChars" min="500" max="8000" step="100" value="1800">
                            <span class="range-unit">chr</span>
                        </div>
                    </div>
                </div>
            </div>

            <!-- CARD 4: PERFORMANCE -->
            <div class="cfg-card">
                <div class="cfg-card-header">
                    <span>Performance &amp; Timeouts</span>
                </div>
                <div class="cfg-card-body">
                    <div class="cfg-item">
                        <div class="cfg-item-inline">
                            <label class="cfg-label" for="set_requestTimeout">Request Timeout</label>
                            <span class="badge-tag rec" id="timeout_badge">300s</span>
                        </div>
                        <div class="cfg-help">Max seconds to wait for Ollama before terminating the call (10-600s, 300s recommended for 7B-9B)</div>
                        <div class="range-box">
                            <input type="range" id="set_requestTimeout_slider" min="10" max="600" step="10" value="300">
                            <input type="number" id="set_requestTimeout" min="10" max="600" step="10" value="300">
                            <span class="range-unit">sec</span>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- FIXED FOOTER ACTIONS -->
        <div class="settings-bottom-bar">
            <div class="settings-btn-row">
                <button class="btn-save-cfg" id="btnSaveSettings">&#10003; Save &amp; Apply</button>
                <button class="btn-reset-cfg" id="btnResetSettings">&#8634; Reset</button>
            </div>
            <div class="cfg-feedback" id="settingsFeedback"></div>
        </div>
    </div>

    <script>
        const vscode = acquireVsCodeApi();

        window.onerror = function(msg, url, line, col, err) {
            vscode.postMessage({ type: 'clientError', message: String(msg) + ' (line ' + line + ')' });
        };
        window.onunhandledrejection = function(e) {
            vscode.postMessage({ type: 'clientError', message: 'Unhandled Promise: ' + String(e.reason) });
        };

        // Elements - Header & Views
        const statusPill = document.getElementById('statusPill');
        const statusText = document.getElementById('statusText');
        const modelSelector = document.getElementById('modelSelector');
        const refreshBtn = document.getElementById('refreshBtn');
        const diagnosticsBanner = document.getElementById('diagnosticsBanner');
        const activityBar = document.getElementById('activityBar');
        const activityPhase = document.getElementById('activityPhase');
        const activityMsg = document.getElementById('activityMsg');
        const workspaceBadge = document.getElementById('workspaceBadge');

        const chatView = document.getElementById('chatView');
        const settingsView = document.getElementById('settingsView');
        const settingsNavBtn = document.getElementById('settingsNavBtn');
        const btnBackToChat = document.getElementById('btnBackToChat');

        // Chat Elements
        const chatContainer = document.getElementById('chatContainer');
        const welcomeCard = document.getElementById('welcomeCard');
        const approvalPanel = document.getElementById('approvalPanel');
        const promptInput = document.getElementById('promptInput');
        const actionBtn = document.getElementById('actionBtn');
        const newChatBtn = document.getElementById('newChatBtn');
        const clearMemoryBtn = document.getElementById('clearMemoryBtn');
        const modeChatBtn = document.getElementById('modeChatBtn');
        const modeAgentBtn = document.getElementById('modeAgentBtn');

        // Settings Elements
        const set_backendUrl = document.getElementById('set_backendUrl');
        const set_ollamaUrl = document.getElementById('set_ollamaUrl');
        const set_defaultModel = document.getElementById('set_defaultModel');
        const set_streamingEnabled = document.getElementById('set_streamingEnabled');

        const set_maxIterations = document.getElementById('set_maxIterations');
        const set_maxIterations_slider = document.getElementById('set_maxIterations_slider');
        const iter_badge = document.getElementById('iter_badge');

        const set_maxRepairAttempts = document.getElementById('set_maxRepairAttempts');
        const set_maxRepairAttempts_slider = document.getElementById('set_maxRepairAttempts_slider');
        const repair_badge = document.getElementById('repair_badge');

        const set_autoApprove = document.getElementById('set_autoApprove');

        const set_contextWindow = document.getElementById('set_contextWindow');
        const set_contextWindow_slider = document.getElementById('set_contextWindow_slider');
        const ctx_badge = document.getElementById('ctx_badge');

        const set_maxObservationChars = document.getElementById('set_maxObservationChars');
        const set_maxObservationChars_slider = document.getElementById('set_maxObservationChars_slider');
        const obs_badge = document.getElementById('obs_badge');

        const set_requestTimeout = document.getElementById('set_requestTimeout');
        const set_requestTimeout_slider = document.getElementById('set_requestTimeout_slider');
        const timeout_badge = document.getElementById('timeout_badge');

        const btnSaveSettings = document.getElementById('btnSaveSettings');
        const btnResetSettings = document.getElementById('btnResetSettings');
        const settingsFeedback = document.getElementById('settingsFeedback');

        let currentMode = 'agent';
        let isGenerating = false;
        let currentAssistantBubble = null;
        let currentAssistantText = '';
        let isSettingsOpen = false;

        // View Toggling
        function switchView(showSettings) {
            isSettingsOpen = showSettings;
            if (isSettingsOpen) {
                chatView.style.display = 'none';
                settingsView.classList.add('active');
                settingsNavBtn.classList.add('active');
                settingsNavBtn.title = 'Back to Chat';
                vscode.postMessage({ type: 'loadSettings' });
            } else {
                settingsView.classList.remove('active');
                chatView.style.display = 'flex';
                settingsNavBtn.classList.remove('active');
                settingsNavBtn.title = 'Configure Settings';
            }
        }

        settingsNavBtn.addEventListener('click', () => switchView(!isSettingsOpen));
        btnBackToChat.addEventListener('click', () => switchView(false));

        // Slider-input sync helpers
        function bindRange(slider, numberInput, badge, suffix) {
            if (!slider || !numberInput) return;
            slider.addEventListener('input', () => {
                numberInput.value = slider.value;
                if (badge) badge.innerText = slider.value + ' ' + suffix;
            });
            numberInput.addEventListener('input', () => {
                slider.value = numberInput.value;
                if (badge) badge.innerText = numberInput.value + ' ' + suffix;
            });
        }

        bindRange(set_maxIterations_slider, set_maxIterations, iter_badge, 'cycles');
        bindRange(set_maxRepairAttempts_slider, set_maxRepairAttempts, repair_badge, 'attempts');
        bindRange(set_contextWindow_slider, set_contextWindow, ctx_badge, 'tokens');
        bindRange(set_maxObservationChars_slider, set_maxObservationChars, obs_badge, 'chars');
        bindRange(set_requestTimeout_slider, set_requestTimeout, timeout_badge, 'sec');

        // Settings Actions
        if (btnSaveSettings) {
            btnSaveSettings.addEventListener('click', () => {
                const newSettings = {
                    backendUrl: set_backendUrl ? set_backendUrl.value.trim() : undefined,
                    ollamaUrl: set_ollamaUrl ? set_ollamaUrl.value.trim() : undefined,
                    defaultModel: set_defaultModel ? set_defaultModel.value.trim() : undefined,
                    streamingEnabled: set_streamingEnabled ? set_streamingEnabled.checked : true,
                    maxIterations: set_maxIterations ? parseInt(set_maxIterations.value, 10) : 16,
                    maxRepairAttempts: set_maxRepairAttempts ? parseInt(set_maxRepairAttempts.value, 10) : 4,
                    autoApprove: set_autoApprove ? set_autoApprove.checked : false,
                    contextWindow: set_contextWindow ? parseInt(set_contextWindow.value, 10) : 4096,
                    maxObservationChars: set_maxObservationChars ? parseInt(set_maxObservationChars.value, 10) : 1800,
                    requestTimeout: set_requestTimeout ? parseInt(set_requestTimeout.value, 10) : 300,
                };
                vscode.postMessage({ type: 'saveSettings', settings: newSettings });
                if (settingsFeedback) {
                    settingsFeedback.innerText = '✓ Settings saved and applied to backend!';
                    setTimeout(() => { if (settingsFeedback) settingsFeedback.innerText = ''; }, 3000);
                }
            });
        }

        if (btnResetSettings) {
            btnResetSettings.addEventListener('click', () => {
                vscode.postMessage({ type: 'resetSettings' });
                if (settingsFeedback) {
                    settingsFeedback.innerText = '↺ Restored default settings';
                    setTimeout(() => { if (settingsFeedback) settingsFeedback.innerText = ''; }, 3000);
                }
            });
        }

        // Mode Toggles
        modeChatBtn.addEventListener('click', () => {
            currentMode = 'chat';
            modeChatBtn.classList.add('active');
            modeAgentBtn.classList.remove('active');
            promptInput.placeholder = 'Ask a question in Chat mode...';
        });

        modeAgentBtn.addEventListener('click', () => {
            currentMode = 'agent';
            modeAgentBtn.classList.add('active');
            modeChatBtn.classList.remove('active');
            promptInput.placeholder = 'Describe an autonomous coding task...';
        });

        function setGeneratingState(generating) {
            isGenerating = generating;
            if (generating) {
                actionBtn.innerText = '■ Stop';
                actionBtn.classList.add('stop');
            } else {
                actionBtn.innerText = 'Send';
                actionBtn.classList.remove('stop');
                promptInput.focus();
            }
        }

        function escapeHtml(unsafe) {
            if (unsafe === undefined || unsafe === null) return '';
            return String(unsafe)
                .split('&').join('&amp;')
                .split('<').join('&lt;')
                .split('>').join('&gt;')
                .split('"').join('&quot;')
                .split("'").join('&#039;');
        }

        function toggleThinking(iterId) {
            const box = document.getElementById('thinking_' + iterId);
            if (box) {
                box.classList.toggle('collapsed');
            }
        }
        window.toggleThinking = toggleThinking;

        function renderMarkdown(rawText) {
            if (!rawText) return '';
            const B3 = String.fromCharCode(96, 96, 96);
            const B1 = String.fromCharCode(96);
            const LF = String.fromCharCode(10);
            const parts = rawText.split(B3);
            let html = '';

            for (let i = 0; i < parts.length; i++) {
                if (i % 2 === 1) {
                    const block = parts[i];
                    const firstNewline = block.indexOf(LF);
                    let lang = 'code';
                    let codeContent = block;
                    if (firstNewline !== -1) {
                        lang = block.slice(0, firstNewline).trim() || 'code';
                        codeContent = block.slice(firstNewline + 1);
                    }
                    const escapedCode = escapeHtml(codeContent);
                    html += '<div class="code-block-container">' +
                        '<div class="code-header">' +
                            '<span>' + escapeHtml(lang) + '</span>' +
                            '<button class="copy-btn" onclick="copyCode(this)">Copy</button>' +
                        '</div>' +
                        '<pre><code>' + escapedCode + '</code></pre>' +
                    '</div>';
                } else {
                    let text = escapeHtml(parts[i]);
                    // Inline code
                    text = text.split(B1).reduce((acc, chunk, idx) => acc + (idx % 2 === 1 ? '<code class="inline-code">' + chunk + '</code>' : chunk), '');
                    // Bold
                    text = text.split('**').reduce((acc, chunk, idx) => acc + (idx % 2 === 1 ? '<strong>' + chunk + '</strong>' : chunk), '');
                    // Newlines
                    text = text.split(LF).join('<br>');
                    html += text;
                }
            }
            return html;
        }

        window.copyCode = function(btn) {
            const container = btn ? btn.closest('.code-block-container') : null;
            const codeEl = container ? container.querySelector('code') : null;
            if (codeEl) {
                navigator.clipboard.writeText(codeEl.innerText).then(() => {
                    const original = btn.innerText;
                    btn.innerText = 'Copied!';
                    setTimeout(() => { btn.innerText = original; }, 1800);
                });
            }
        };

        function appendUserMessage(text) {
            if (welcomeCard) welcomeCard.style.display = 'none';
            const row = document.createElement('div');
            row.className = 'msg-row user-msg';
            row.innerHTML = '<div class=\"msg-role-tag\">👤 You</div>' +
                '<div class=\"msg-bubble\">' + renderMarkdown(text) + '</div>';
            chatContainer.appendChild(row);
            chatContainer.scrollTop = chatContainer.scrollHeight;
        }

        function createAssistantMessage() {
            if (welcomeCard) welcomeCard.style.display = 'none';
            const row = document.createElement('div');
            row.className = 'msg-row assistant-msg';
            row.innerHTML = '<div class=\"msg-role-tag\">⚡ LocalForge-AI</div>' +
                '<div class=\"msg-bubble\"><span class=\"content\"></span><span class=\"cursor-blink\"></span></div>';
            chatContainer.appendChild(row);
            chatContainer.scrollTop = chatContainer.scrollHeight;

            currentAssistantBubble = row.querySelector('.content');
            currentAssistantText = '';
            return row;
        }

        function submitPrompt() {
            if (isGenerating) {
                vscode.postMessage({ type: 'stopGeneration' });
                return;
            }

            const text = promptInput.value.trim();
            if (!text) return;

            promptInput.value = '';
            setGeneratingState(true);
            appendUserMessage(text);
            createAssistantMessage();

            vscode.postMessage({
                type: 'sendMessage',
                text: text,
                mode: currentMode
            });
        }

        actionBtn.addEventListener('click', submitPrompt);

        promptInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                submitPrompt();
            }
        });

        refreshBtn.addEventListener('click', () => {
            statusText.innerText = 'Checking...';
            vscode.postMessage({ type: 'refreshStatus' });
        });

        if (statusPill) {
            statusPill.style.cursor = 'pointer';
            statusPill.title = 'Click to re-check LocalForge status';
            statusPill.addEventListener('click', () => {
                statusText.innerText = 'Checking...';
                vscode.postMessage({ type: 'refreshStatus' });
            });
        }

        modelSelector.addEventListener('change', (e) => {
            vscode.postMessage({ type: 'selectModel', model: e.target.value });
        });

        newChatBtn.addEventListener('click', () => {
            if (isGenerating) vscode.postMessage({ type: 'stopGeneration' });
            vscode.postMessage({ type: 'newChat' });
        });

        clearMemoryBtn.addEventListener('click', () => {
            vscode.postMessage({ type: 'clearMemory' });
        });

        // Backend message handling
        window.addEventListener('message', (event) => {
            const message = event.data;

            switch (message.type) {
                case 'statusUpdate': {
                    const health = message.health;
                    const error = message.error;
                    const models = message.models || [];

                    diagnosticsBanner.className = 'diagnostics-banner';
                    diagnosticsBanner.style.display = 'none';

                    if (health) {
                        if (health.status === 'healthy') {
                            statusPill.className = 'status-pill connected';
                            statusText.innerText = 'Online';
                        } else {
                            statusPill.className = 'status-pill degraded';
                            statusText.innerText = 'Ollama Offline';
                            diagnosticsBanner.className = 'diagnostics-banner warning';
                            diagnosticsBanner.innerText = health.ollama.error || 'Ollama daemon is not reachable at ' + health.ollama.base_url;
                        }

                        if (models.length > 0) {
                            modelSelector.innerHTML = '';
                            models.forEach(m => {
                                const opt = document.createElement('option');
                                opt.value = m.name;
                                opt.innerText = m.name + (m.parameter_size ? ' (' + m.parameter_size + ')' : '');
                                if (m.is_active || m.name === health.ollama.active_model) {
                                    opt.selected = true;
                                }
                                modelSelector.appendChild(opt);
                            });
                        }

                        if (workspaceBadge) {
                            if (message.summary && message.summary.workspace_root) {
                                const sum = message.summary;
                                const langs = Object.keys(sum.languages || {}).slice(0, 2).join(', ') || 'General';
                                const folder = (sum.workspace_root || '').split(String.fromCharCode(92)).join('/').split('/').pop() || 'workspace';
                                workspaceBadge.innerText = '📁 ' + folder + ' • ' + langs + ' (' + sum.total_files_scanned + ' files)';
                            } else if (health.workspace_root) {
                                const folder = (health.workspace_root || '').split(String.fromCharCode(92)).join('/').split('/').pop() || 'workspace';
                                workspaceBadge.innerText = '📁 ' + folder;
                            } else {
                                workspaceBadge.innerText = '📁 No workspace open';
                            }
                        }
                    } else {
                        statusPill.className = 'status-pill offline';
                        statusText.innerText = 'Backend Offline';
                        diagnosticsBanner.className = 'diagnostics-banner error';
                        diagnosticsBanner.innerText = error || 'Cannot connect to LocalForge backend at 127.0.0.1:8765';
                        if (workspaceBadge) workspaceBadge.innerText = '📁 Backend Offline';
                    }
                    break;
                }

                case 'settingsLoaded': {
                    const s = message.settings || {};
                    if (set_backendUrl && s.backendUrl !== undefined) set_backendUrl.value = s.backendUrl;
                    if (set_ollamaUrl && s.ollamaUrl !== undefined) set_ollamaUrl.value = s.ollamaUrl;
                    if (set_defaultModel && s.defaultModel !== undefined) set_defaultModel.value = s.defaultModel;
                    if (set_streamingEnabled && s.streamingEnabled !== undefined) set_streamingEnabled.checked = !!s.streamingEnabled;

                    if (s.maxIterations !== undefined) {
                        if (set_maxIterations) set_maxIterations.value = s.maxIterations;
                        if (set_maxIterations_slider) set_maxIterations_slider.value = s.maxIterations;
                        if (iter_badge) iter_badge.innerText = s.maxIterations + ' cycles';
                    }
                    if (s.maxRepairAttempts !== undefined) {
                        if (set_maxRepairAttempts) set_maxRepairAttempts.value = s.maxRepairAttempts;
                        if (set_maxRepairAttempts_slider) set_maxRepairAttempts_slider.value = s.maxRepairAttempts;
                        if (repair_badge) repair_badge.innerText = s.maxRepairAttempts + ' attempts';
                    }
                    if (set_autoApprove && s.autoApprove !== undefined) set_autoApprove.checked = !!s.autoApprove;

                    if (s.contextWindow !== undefined) {
                        if (set_contextWindow) set_contextWindow.value = s.contextWindow;
                        if (set_contextWindow_slider) set_contextWindow_slider.value = s.contextWindow;
                        if (ctx_badge) ctx_badge.innerText = s.contextWindow + ' tokens';
                    }
                    if (s.maxObservationChars !== undefined) {
                        if (set_maxObservationChars) set_maxObservationChars.value = s.maxObservationChars;
                        if (set_maxObservationChars_slider) set_maxObservationChars_slider.value = s.maxObservationChars;
                        if (obs_badge) obs_badge.innerText = s.maxObservationChars + ' chars';
                    }
                    if (s.requestTimeout !== undefined) {
                        if (set_requestTimeout) set_requestTimeout.value = s.requestTimeout;
                        if (set_requestTimeout_slider) set_requestTimeout_slider.value = s.requestTimeout;
                        if (timeout_badge) timeout_badge.innerText = s.requestTimeout + ' sec';
                    }
                    break;
                }

                case 'streamToken': {
                    currentAssistantText += message.token;
                    if (currentAssistantBubble) {
                        currentAssistantBubble.innerHTML = renderMarkdown(currentAssistantText);
                        chatContainer.scrollTop = chatContainer.scrollHeight;
                    }
                    break;
                }

                case 'streamDone': {
                    if (currentAssistantBubble) {
                        const text = message.fullText || currentAssistantText;
                        if (text) currentAssistantBubble.innerHTML = renderMarkdown(text);
                        const cursor = currentAssistantBubble.parentElement.querySelector('.cursor-blink');
                        if (cursor) cursor.remove();
                    }
                    if (activityBar) activityBar.classList.remove('active');
                    setGeneratingState(false);
                    currentAssistantBubble = null;
                    break;
                }

                case 'streamStopped': {
                    if (currentAssistantBubble) {
                        const cursor = currentAssistantBubble.parentElement.querySelector('.cursor-blink');
                        if (cursor) cursor.remove();
                    }
                    if (activityBar) activityBar.classList.remove('active');
                    setGeneratingState(false);
                    currentAssistantBubble = null;
                    break;
                }

                case 'streamError': {
                    if (currentAssistantBubble) {
                        currentAssistantBubble.innerHTML += '<div style="color: var(--lf-error); margin-top: 6px;">⚠️ ' + escapeHtml(message.error) + '</div>';
                        const cursor = currentAssistantBubble.parentElement.querySelector('.cursor-blink');
                        if (cursor) cursor.remove();
                    }
                    if (activityBar) activityBar.classList.remove('active');
                    setGeneratingState(false);
                    currentAssistantBubble = null;
                    break;
                }

                case 'clearMessages': {
                    chatContainer.innerHTML = '';
                    if (welcomeCard) {
                        welcomeCard.style.display = 'block';
                        chatContainer.appendChild(welcomeCard);
                    }
                    if (activityBar) activityBar.classList.remove('active');
                    setGeneratingState(false);
                    currentAssistantBubble = null;
                    break;
                }

                case 'approvalsUpdate': {
                    renderApprovals(message.approvals || []);
                    break;
                }

                case 'agentEvent': {
                    handleAgentEvent(message.eventType, message.data);
                    break;
                }
            }
        });

        function handleAgentEvent(eventType, data) {
            if (!data) return;

            if (eventType === 'activity') {
                if (activityBar && activityPhase && activityMsg) {
                    activityBar.classList.add('active');
                    activityPhase.innerText = data.phase || 'WORKING';
                    activityMsg.innerText = data.message || '';
                }
                return;
            }

            if (!currentAssistantBubble) return;

            if (eventType === 'plan') {
                const stepsHtml = (data.required_changes || []).map(s => '<li>' + escapeHtml(s) + '</li>').join('');
                const filesCreate = (data.files_to_create || []).join(', ') || 'None';
                const filesMod = (data.files_to_modify || []).join(', ') || 'None';
                currentAssistantBubble.innerHTML += '<div class="plan-card">' +
                    '<h4>📋 Execution Plan</h4>' +
                    '<div><strong>Objective:</strong> ' + escapeHtml(data.objective || '') + '</div>' +
                    '<div><strong>Files to Create:</strong> ' + escapeHtml(filesCreate) + '</div>' +
                    '<div><strong>Files to Modify:</strong> ' + escapeHtml(filesMod) + '</div>' +
                    (stepsHtml ? '<ul style="margin:4px 0;padding-left:16px;">' + stepsHtml + '</ul>' : '') +
                    '<div><strong>Test Strategy:</strong> ' + escapeHtml(data.testing_strategy || '') + '</div>' +
                '</div>';
            } else if (eventType === 'thinking') {
                const iterId = data.iteration !== undefined ? data.iteration : 'plan';
                let box = document.getElementById('thinking_' + iterId);
                if (!box) {
                    const newBox = document.createElement('div');
                    newBox.className = 'thinking-box';
                    newBox.id = 'thinking_' + iterId;
                    newBox.innerHTML =
                        '<div class="thinking-header" data-iter="' + iterId + '" onclick="toggleThinking(this.dataset.iter)">' +
                            '<span class="thinking-icon">💭</span>' +
                            '<span class="thinking-label">Thinking</span>' +
                            '<span class="thinking-pulse"></span>' +
                            '<span class="thinking-chevron">▲</span>' +
                        '</div>' +
                        '<div class="thinking-body" id="thinking_body_' + iterId + '">' +
                            '<div class="thinking-text" id="thinking_text_' + iterId + '"></div>' +
                        '</div>';
                    currentAssistantBubble.appendChild(newBox);
                }
                const textElem = document.getElementById('thinking_text_' + iterId);
                if (textElem && data.thought) {
                    textElem.innerText = data.thought;
                    const bodyElem = document.getElementById('thinking_body_' + iterId);
                    if (bodyElem) bodyElem.scrollTop = bodyElem.scrollHeight;
                }
                if (activityMsg && data.thought) {
                    const snippet = data.thought.length > 55 ? data.thought.slice(-55) : data.thought;
                    activityMsg.innerText = snippet.split(String.fromCharCode(10)).join(' ').split(String.fromCharCode(13)).join(' ');
                }
            } else if (eventType === 'thought') {
                const iterId = data.iteration !== undefined ? data.iteration : 'plan';
                const box = document.getElementById('thinking_' + iterId);
                if (box) {
                    const pulse = box.querySelector('.thinking-pulse');
                    if (pulse) pulse.remove();
                    const label = box.querySelector('.thinking-label');
                    if (label) label.innerText = 'Thought Process';
                    const textElem = document.getElementById('thinking_text_' + iterId);
                    if (textElem && data.thought) {
                        textElem.innerText = data.thought;
                    }
                } else if (data.thought) {
                    currentAssistantBubble.innerHTML += '<div style="font-size:11.5px;opacity:0.85;margin:4px 0;">💭 ' + escapeHtml(data.thought) + '</div>';
                }
            } else if (eventType === 'tool_call') {
                const argStr = JSON.stringify(data.arguments || {});
                const shortArg = argStr.length > 60 ? argStr.slice(0, 60) + '...' : argStr;
                currentAssistantBubble.innerHTML += '<div class="tool-pill" id="tool_' + data.iteration + '_' + escapeHtml(data.tool) + '">' +
                    '<span>🔧 <strong>' + escapeHtml(data.tool) + '</strong> ' + escapeHtml(shortArg) + '</span>' +
                    '<span>⏳</span>' +
                '</div>';
            } else if (eventType === 'tool_result') {
                const pill = document.getElementById('tool_' + data.iteration + '_' + data.tool);
                if (pill) {
                    pill.classList.add(data.success ? 'ok' : 'fail');
                    const spans = pill.querySelectorAll('span');
                    if (spans.length > 1) spans[1].innerText = data.success ? '✓' : '✕';
                }
            } else if (eventType === 'report') {
                const created = (data.files_created || []).join(', ') || 'None';
                const modified = (data.files_modified || []).join(', ') || 'None';
                const tests = (data.tests_performed || []).join(', ') || 'None';
                currentAssistantBubble.innerHTML += '<div class="report-card">' +
                    '<h4>✅ Completion Report</h4>' +
                    '<div><strong>Summary:</strong> ' + escapeHtml(data.summary || '') + '</div>' +
                    '<div><strong>Files Created:</strong> ' + escapeHtml(created) + '</div>' +
                    '<div><strong>Files Modified:</strong> ' + escapeHtml(modified) + '</div>' +
                    '<div><strong>Tests Run:</strong> ' + escapeHtml(tests) + ' (' + escapeHtml(data.test_results || '') + ')</div>' +
                    '<div><strong>Remaining Issues:</strong> ' + escapeHtml(data.remaining_issues || 'None') + '</div>' +
                '</div>';
            } else if (eventType === 'error') {
                const errText = String(data.error || '');
                const isCudaOrMem = /cuda|vram|out of memory|0xc0000409|llama-server|shared object/i.test(errText);
                let tipMsg = 'Tip: For larger models or files, increase the <strong>Request Timeout</strong> in Settings ⚙.';
                if (isCudaOrMem) {
                    tipMsg = '💡 <strong>GPU Memory Tip:</strong> Ollama encountered GPU VRAM pressure on your 4GB GPU (background 3D apps or Epic Games share VRAM). Try switching to <strong>qwen3:4b</strong> from the top model dropdown, or lower <strong>Context Window</strong> to 2048 in Settings ⚙.';
                }
                currentAssistantBubble.innerHTML += '<div style="color: var(--lf-error); margin-top: 8px; padding: 8px 10px; background: rgba(241, 76, 76, 0.1); border-left: 3px solid var(--lf-error); border-radius: 4px;">' +
                    '<div>⚠️ <strong>Execution Stopped:</strong> ' + escapeHtml(errText || 'Task encountered an error.') + '</div>' +
                    '<div style="font-size:11px;opacity:0.85;margin-top:4px;">' + tipMsg + '</div>' +
                '</div>';
                const cursor = currentAssistantBubble.parentElement.querySelector('.cursor-blink');
                if (cursor) cursor.remove();
                if (activityBar) activityBar.classList.remove('active');
                setGeneratingState(false);
            }

            chatContainer.scrollTop = chatContainer.scrollHeight;
        }

        function formatDiffHtml(diffText) {
            if (!diffText) return '(No diff details)';
            const lines = diffText.split(String.fromCharCode(10));
            return lines.map(line => {
                const esc = escapeHtml(line);
                if (line.startsWith('+') && !line.startsWith('+++')) {
                    return '<span class="diff-line-add">' + esc + '</span>';
                } else if (line.startsWith('-') && !line.startsWith('---')) {
                    return '<span class="diff-line-del">' + esc + '</span>';
                }
                return esc;
            }).join(String.fromCharCode(10));
        }

        window.resolveApproval = function(requestId, approved) {
            vscode.postMessage({ type: 'resolveApproval', requestId: requestId, approved: approved });
        };

        window.openNativeDiff = function(requestId) {
            vscode.postMessage({ type: 'openNativeDiff', requestId: requestId });
        };

        function renderApprovals(approvals) {
            if (!approvalPanel) return;
            if (!approvals || approvals.length === 0) {
                approvalPanel.innerHTML = '';
                approvalPanel.classList.remove('active');
                return;
            }

            approvalPanel.classList.add('active');
            let html = '';
            approvals.forEach(req => {
                const target = req.relative_path || req.command || 'Operation';
                const diffHtml = formatDiffHtml(req.diff || '');
                const hasFileDiff = req.operation !== 'EXECUTE_COMMAND' && req.operation !== 'INSTALL_DEPENDENCY';

                html += '<div class="approval-card">' +
                    '<div class="approval-header">' +
                        '<span><span class="op-badge">' + escapeHtml(req.operation) + '</span> ' + escapeHtml(target) + '</span>' +
                        '<span class="diff-stats"><span class="stat-add">+' + req.additions + '</span> / <span class="stat-del">-' + req.deletions + '</span></span>' +
                    '</div>' +
                    '<div class="approval-desc">' + escapeHtml(req.description || '') + '</div>' +
                    '<div class="approval-diff">' + diffHtml + '</div>' +
                    '<div class="approval-actions">' +
                        '<button class="btn-approve" data-id="' + escapeHtml(req.id) + '" onclick="resolveApproval(this.dataset.id, true)">✓ Approve</button>' +
                        '<button class="btn-reject" data-id="' + escapeHtml(req.id) + '" onclick="resolveApproval(this.dataset.id, false)">✕ Reject</button>' +
                        (hasFileDiff ? '<button class="btn-diff" data-id="' + escapeHtml(req.id) + '" onclick="openNativeDiff(this.dataset.id)">◫ Diff</button>' : '') +
                    '</div>' +
                '</div>';
            });
            approvalPanel.innerHTML = html;
        }

        // Initial settings and status requests on load
        vscode.postMessage({ type: 'loadSettings' });
        vscode.postMessage({ type: 'refreshStatus' });

        // Auto-refresh status periodically so UI auto-connects
        setInterval(() => {
            vscode.postMessage({ type: 'refreshStatus' });
        }, 8000);
    </script>
</body>
</html>`;
    }
}
