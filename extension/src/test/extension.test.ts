import './vscodeMock';
import test from 'node:test';
import assert from 'node:assert/strict';
import { mockState, resetMockState, vscodeMock } from './vscodeMock';
import { activate } from '../extension';
import { BackendService } from '../services/backendService';
import { DiffContentProvider } from '../providers/diffContentProvider';
import { SidebarViewProvider } from '../providers/sidebarViewProvider';

function createFakeStream(chunks: string[]) {
    const encoder = new TextEncoder();
    let idx = 0;
    return new ReadableStream({
        pull(controller) {
            if (idx < chunks.length) {
                controller.enqueue(encoder.encode(chunks[idx++]));
            } else {
                controller.close();
            }
        }
    });
}

test('Extension activation & sidebar/command registration', () => {
    resetMockState();
    const fakeContext: any = {
        extensionUri: vscodeMock.Uri.file('c:/dev2/extension'),
        subscriptions: [],
    };

    activate(fakeContext);

    // 1. Sidebar registered
    assert.equal(mockState.registeredProviders.length, 1);
    assert.equal(mockState.registeredProviders[0].viewId, 'localforge-ai-sidebar');

    // 2. Diff content provider registered
    assert.ok(mockState.textDocumentProviders.has('localforge-diff'));

    // 3. Commands registered
    assert.ok(mockState.registeredCommands.has('localforge.checkStatus'));
    assert.ok(mockState.registeredCommands.has('localforge.refreshModels'));
    assert.ok(mockState.registeredCommands.has('localforge.newChat'));
    assert.ok(fakeContext.subscriptions.length >= 5);
});

test('BackendService health check & model discovery', async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = (async (url: string) => {
        if (url.endsWith('/health')) {
            return new Response(JSON.stringify({
                status: 'healthy',
                app_name: 'LocalForge-AI',
                version: '2.0.0',
                timestamp: new Date().toISOString(),
                python_version: '3.13.17',
                ollama: { connected: true, base_url: 'http://127.0.0.1:11434', active_model: 'qwen2.5-coder:7b', active_model_installed: true }
            }), { status: 200 });
        }
        if (url.endsWith('/api/models')) {
            return new Response(JSON.stringify({
                connected: true,
                active_model: 'qwen2.5-coder:7b',
                total_count: 1,
                models: [{ name: 'qwen2.5-coder:7b', is_active: true }]
            }), { status: 200 });
        }
        return new Response('Not Found', { status: 404 });
    }) as any;

    try {
        const svc = new BackendService();
        const health = await svc.checkHealth();
        assert.equal(health.ok, true);
        assert.equal(health.data?.status, 'healthy');
        assert.equal(health.data?.ollama.connected, true);

        const models = await svc.listModels();
        assert.equal(models.connected, true);
        assert.equal(models.models.length, 1);
        assert.equal(models.models[0].name, 'qwen2.5-coder:7b');
    } finally {
        globalThis.fetch = originalFetch;
    }
});

test('BackendService streaming SSE chat responses & cancellation', async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = (async (url: string, init?: RequestInit) => {
        if (url.endsWith('/api/chat')) {
            return new Response(
                createFakeStream([
                    'event: start\ndata: {"model":"qwen2.5-coder:7b"}\n\n',
                    'event: token\ndata: {"token":"Hello"}\n\n',
                    'event: token\ndata: {"token":" LocalForge"}\n\n',
                    'event: done\ndata: {"full_text":"Hello LocalForge"}\n\n',
                ]),
                { status: 200, headers: { 'Content-Type': 'text/event-stream' } }
            );
        }
        if (url.endsWith('/api/chat/stop')) {
            return new Response(JSON.stringify({ status: 'stopped' }), { status: 200 });
        }
        return new Response('', { status: 404 });
    }) as any;

    try {
        const svc = new BackendService();
        const receivedTokens: string[] = [];
        let startModel = '';
        let finalText = '';

        await svc.streamChat('sess1', 'Say hi', {
            onStart: (m) => { startModel = m; },
            onToken: (t) => { receivedTokens.push(t); },
            onDone: (full) => { finalText = full; },
            onError: (err) => { assert.fail(`Unexpected error: ${err}`); }
        });

        assert.equal(startModel, 'qwen2.5-coder:7b');
        assert.deepEqual(receivedTokens, ['Hello', ' LocalForge']);
        assert.equal(finalText, 'Hello LocalForge');

        // Test stop/cancellation API call
        const stopped = await svc.stopChat('sess1');
        assert.equal(stopped, true);
    } finally {
        globalThis.fetch = originalFetch;
    }
});

test('Sidebar webview interaction: Chat messaging, Approvals, Diff Display, Cancellation', async () => {
    resetMockState();
    const originalFetch = globalThis.fetch;
    const postedToWebview: any[] = [];
    let messageHandler: ((data: any) => Promise<void>) | null = null;
    let resolvedCalls: any[] = [];
    let stopAgentCalled = false;

    globalThis.fetch = (async (url: string, init?: RequestInit) => {
        if (url.endsWith('/health')) {
            return new Response(JSON.stringify({
                status: 'healthy', app_name: 'LocalForge-AI', version: '2.0.0', timestamp: '', python_version: '3.13',
                ollama: { connected: true, base_url: 'http://127.0.0.1:11434', active_model: 'qwen2.5-coder:7b', active_model_installed: true }
            }), { status: 200 });
        }
        if (url.endsWith('/api/models')) {
            return new Response(JSON.stringify({ connected: true, active_model: 'qwen2.5-coder:7b', models: [], total_count: 0 }), { status: 200 });
        }
        if (url.endsWith('/api/context/summary')) {
            return new Response(JSON.stringify({ workspace_root: 'c:/dev2', languages: { Python: 5 }, total_files_scanned: 5, frameworks: [], entry_points: [], config_files: [], test_directories: [], structure_tree: '' }), { status: 200 });
        }
        if (url.includes('/api/context/workspace')) {
            return new Response(JSON.stringify({ status: 'success' }), { status: 200 });
        }
        if (url.endsWith('/api/approvals')) {
            return new Response(JSON.stringify([{
                id: 'req-123',
                operation: 'EDIT_FILE',
                relative_path: 'app.py',
                original_content: 'x = 1',
                proposed_content: 'x = 2',
                diff: '--- a/app.py\n+++ b/app.py\n-x = 1\n+x = 2',
                additions: 1,
                deletions: 1,
                status: 'PENDING',
                description: 'Update x to 2',
                created_at: new Date().toISOString(),
            }]), { status: 200 });
        }
        if (url.endsWith('/api/approvals/req-123/resolve')) {
            resolvedCalls.push(JSON.parse(init?.body as string));
            return new Response(JSON.stringify({ success: true, operation: 'EDIT_FILE', file_path: 'c:/dev2/app.py', relative_path: 'app.py', message: 'Successfully applied edit to app.py.', approval_required: false }), { status: 200 });
        }
        if (url.endsWith('/api/agent/stop')) {
            stopAgentCalled = true;
            return new Response(JSON.stringify({ status: 'stopped' }), { status: 200 });
        }
        if (url.endsWith('/api/chat/stop')) {
            return new Response(JSON.stringify({ status: 'stopped' }), { status: 200 });
        }
        return new Response(JSON.stringify({}), { status: 200 });
    }) as any;

    try {
        const svc = new BackendService();
        const diffProvider = new DiffContentProvider();
        const sidebar = new SidebarViewProvider(vscodeMock.Uri.file('c:/dev2/extension') as any, svc, diffProvider);

        const mockWebviewView: any = {
            webview: {
                options: {},
                html: '',
                postMessage: (msg: any) => { postedToWebview.push(msg); },
                onDidReceiveMessage: (cb: any) => { messageHandler = cb; }
            }
        };

        sidebar.resolveWebviewView(mockWebviewView, {} as any, {} as any);

        // Allow async updateStatus() to complete
        await new Promise((r) => setTimeout(r, 30));

        // Verify sidebar HTML contains chat & approval elements
        assert.ok(mockWebviewView.webview.html.includes('approvalPanel'));
        assert.ok(mockWebviewView.webview.html.includes('chatContainer'));
        assert.ok(postedToWebview.some((m) => m.type === 'approvalsUpdate' && m.approvals.length === 1));

        // Test Diff Display interaction
        assert.ok(messageHandler !== null);
        await (messageHandler as any)({ type: 'openNativeDiff', requestId: 'req-123' });
        const diffCmd = mockState.executedCommands.find((c) => c.command === 'vscode.diff');
        assert.ok(diffCmd, 'Expected vscode.diff command execution');
        assert.equal(diffProvider.provideTextDocumentContent(diffCmd.args[0]), 'x = 1');
        assert.equal(diffProvider.provideTextDocumentContent(diffCmd.args[1]), 'x = 2');

        // Test Approval Resolution
        await (messageHandler as any)({ type: 'resolveApproval', requestId: 'req-123', approved: true });
        assert.equal(resolvedCalls.length, 1);
        assert.equal(resolvedCalls[0].approved, true);
        assert.ok(mockState.infoMessages.some((m) => m.includes('Successfully applied edit')));

        // Test Cancellation
        await (messageHandler as any)({ type: 'stopGeneration' });
        assert.equal(stopAgentCalled, true);
        assert.ok(postedToWebview.some((m) => m.type === 'streamStopped'));
    } finally {
        globalThis.fetch = originalFetch;
    }
});
