import * as vscode from 'vscode';

/**
 * Provides in-memory content for virtual URIs so VS Code's native side-by-side
 * diff editor can preview proposed file creations and edits prior to disk write.
 */
export class DiffContentProvider implements vscode.TextDocumentContentProvider {
    public static readonly scheme = 'localforge-diff';
    private _contents = new Map<string, string>();
    private _onDidChange = new vscode.EventEmitter<vscode.Uri>();

    public get onDidChange(): vscode.Event<vscode.Uri> {
        return this._onDidChange.event;
    }

    public setContent(uri: vscode.Uri, content: string) {
        this._contents.set(uri.toString(), content);
        this._onDidChange.fire(uri);
    }

    public provideTextDocumentContent(uri: vscode.Uri): string {
        return this._contents.get(uri.toString()) ?? '';
    }
}
