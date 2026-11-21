import * as vscode from 'vscode';
import { BackendService } from './services/backendService';
import { DiffContentProvider } from './providers/diffContentProvider';
import { SidebarViewProvider } from './providers/sidebarViewProvider';

export function activate(context: vscode.ExtensionContext) {
    const backendService = new BackendService();
    const diffProvider = new DiffContentProvider();

    context.subscriptions.push(
        vscode.workspace.registerTextDocumentContentProvider(
            DiffContentProvider.scheme,
            diffProvider
        )
    );

    const sidebarProvider = new SidebarViewProvider(context.extensionUri, backendService, diffProvider);

    context.subscriptions.push(
        vscode.window.registerWebviewViewProvider(
            SidebarViewProvider.viewType,
            sidebarProvider,
            { webviewOptions: { retainContextWhenHidden: true } }
        )
    );

    // Register commands
    context.subscriptions.push(
        vscode.commands.registerCommand('localforge.checkStatus', async () => {
            await sidebarProvider.updateStatus();
            const res = await backendService.checkHealth();
            if (res.ok && res.data) {
                if (res.data.status === 'healthy') {
                    vscode.window.showInformationMessage(`LocalForge-AI: Backend online (${res.data.version}), Ollama connected.`);
                } else {
                    vscode.window.showWarningMessage(`LocalForge-AI: Backend online, but Ollama is offline: ${res.data.ollama.error}`);
                }
            } else {
                vscode.window.showErrorMessage(`LocalForge-AI: Backend offline at 127.0.0.1:8765. Please start the backend service.`);
            }
        })
    );

    context.subscriptions.push(
        vscode.commands.registerCommand('localforge.refreshModels', async () => {
            await sidebarProvider.updateStatus();
            vscode.window.showInformationMessage('LocalForge-AI: Models refreshed.');
        })
    );

    context.subscriptions.push(
        vscode.commands.registerCommand('localforge.newChat', () => {
            vscode.window.showInformationMessage('LocalForge-AI: New conversation initialized.');
        })
    );
}

export function deactivate() {
    // Clean up resources if necessary
}
