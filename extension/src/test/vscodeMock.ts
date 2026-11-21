/**
 * Lightweight VS Code API mock enabling fast deterministic unit/integration testing
 * of the compiled extension under Node's built-in test runner.
 */
const Module = require('module');

export interface MockState {
    registeredProviders: Array<{ viewId: string; provider: any }>;
    registeredCommands: Map<string, (...args: any[]) => any>;
    executedCommands: Array<{ command: string; args: any[] }>;
    infoMessages: string[];
    warningMessages: string[];
    errorMessages: string[];
    textDocumentProviders: Map<string, any>;
}

export const mockState: MockState = {
    registeredProviders: [],
    registeredCommands: new Map(),
    executedCommands: [],
    infoMessages: [],
    warningMessages: [],
    errorMessages: [],
    textDocumentProviders: new Map(),
};

export function resetMockState() {
    mockState.registeredProviders = [];
    mockState.registeredCommands.clear();
    mockState.executedCommands = [];
    mockState.infoMessages = [];
    mockState.warningMessages = [];
    mockState.errorMessages = [];
    mockState.textDocumentProviders.clear();
}

class MockEventEmitter<T> {
    private listeners: Array<(e: T) => any> = [];
    public event = (listener: (e: T) => any) => {
        this.listeners.push(listener);
        return { dispose: () => {} };
    };
    public fire(data: T) {
        this.listeners.forEach((l) => l(data));
    }
}

class MockUri {
    constructor(
        public readonly scheme: string,
        public readonly authority: string,
        public readonly path: string,
        public readonly fsPath: string
    ) {}

    public static parse(value: string): MockUri {
        const idx = value.indexOf('://');
        if (idx === -1) {
            return new MockUri('file', '', value, value);
        }
        const scheme = value.slice(0, idx);
        const rest = value.slice(idx + 3);
        const slashIdx = rest.indexOf('/');
        const authority = slashIdx === -1 ? rest : rest.slice(0, slashIdx);
        const path = slashIdx === -1 ? '' : rest.slice(slashIdx);
        return new MockUri(scheme, authority, path, path);
    }

    public static file(path: string): MockUri {
        return new MockUri('file', '', path, path);
    }

    public toString(): string {
        return `${this.scheme}://${this.authority}${this.path}`;
    }
}

export const vscodeMock = {
    EventEmitter: MockEventEmitter,
    Uri: MockUri,
    workspace: {
        workspaceFolders: [{ uri: MockUri.file('c:/dev2') }],
        getConfiguration: (_section: string) => ({
            get: <T>(_key: string, defaultValue: T): T => defaultValue,
        }),
        registerTextDocumentContentProvider: (scheme: string, provider: any) => {
            mockState.textDocumentProviders.set(scheme, provider);
            return { dispose: () => {} };
        },
    },
    window: {
        registerWebviewViewProvider: (viewId: string, provider: any) => {
            mockState.registeredProviders.push({ viewId, provider });
            return { dispose: () => {} };
        },
        showInformationMessage: (msg: string) => {
            mockState.infoMessages.push(msg);
        },
        showWarningMessage: (msg: string) => {
            mockState.warningMessages.push(msg);
        },
        showErrorMessage: (msg: string) => {
            mockState.errorMessages.push(msg);
        },
    },
    commands: {
        registerCommand: (cmd: string, callback: (...args: any[]) => any) => {
            mockState.registeredCommands.set(cmd, callback);
            return { dispose: () => {} };
        },
        executeCommand: async (command: string, ...args: any[]) => {
            mockState.executedCommands.push({ command, args });
        },
    },
};

// Intercept require('vscode') globally for Node test execution
const originalLoad = Module._load;
Module._load = function (request: string, parent: any, isMain: boolean) {
    if (request === 'vscode') {
        return vscodeMock;
    }
    return originalLoad.call(this, request, parent, isMain);
};
