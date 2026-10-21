import * as vscode from 'vscode';
import * as path from 'path';
import * as cp from 'child_process';
import { SidebarProvider } from './sidebarProvider';

let serverProcess: cp.ChildProcess | null = null;
let statusBarItem: vscode.StatusBarItem;

export function activate(context: vscode.ExtensionContext) {
    console.log('Context Engine extension activating...');

    // ── Status Bar ──────────────────────────────────────────────────────────
    statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
    statusBarItem.command = 'contextEngine.openSidebar';
    setStatusBarDisconnected();
    statusBarItem.show();
    context.subscriptions.push(statusBarItem);

    // ── Sidebar WebviewView Provider ────────────────────────────────────────
    const sidebarProvider = new SidebarProvider(context);
    context.subscriptions.push(
        vscode.window.registerWebviewViewProvider('contextEngine.sidebar', sidebarProvider, {
            webviewOptions: { retainContextWhenHidden: true }
        })
    );

    // ── Commands ────────────────────────────────────────────────────────────
    context.subscriptions.push(
        vscode.commands.registerCommand('contextEngine.startServer', () => startServer(context, sidebarProvider)),
        vscode.commands.registerCommand('contextEngine.stopServer', () => stopServer(sidebarProvider)),
        vscode.commands.registerCommand('contextEngine.indexWorkspace', () => {
            sidebarProvider.postMessage({ type: 'triggerIndex' });
        }),
        vscode.commands.registerCommand('contextEngine.openSidebar', () => {
            vscode.commands.executeCommand('workbench.view.extension.context-engine-container');
        })
    );

    // ── Probe server on startup ─────────────────────────────────────────────
    probeServer(sidebarProvider);

    // ── Auto-start if configured ────────────────────────────────────────────
    const config = vscode.workspace.getConfiguration('contextEngine');
    if (config.get<boolean>('autoStartServer')) {
        startServer(context, sidebarProvider);
    }
}

export function deactivate() {
    stopServer();
}

// ── Server Management ────────────────────────────────────────────────────────

function getBackendDir(): string {
    const workspaceFolders = vscode.workspace.workspaceFolders;
    if (workspaceFolders && workspaceFolders.length > 0) {
        return path.join(workspaceFolders[0].uri.fsPath, 'backend');
    }
    return process.cwd();
}

async function startServer(context: vscode.ExtensionContext, provider: SidebarProvider) {
    if (serverProcess) {
        vscode.window.showInformationMessage('Context Engine server is already running.');
        return;
    }

    const config = vscode.workspace.getConfiguration('contextEngine');
    const pythonPath = config.get<string>('pythonPath') || 'python';
    const port = config.get<number>('backendPort') || 8000;
    const backendDir = getBackendDir();

    setStatusBarStarting();
    provider.postMessage({ type: 'serverStatus', status: 'starting' });

    const args = ['-m', 'uvicorn', 'mcp_server.server:app', '--host', '127.0.0.1', '--port', String(port)];

    try {
        serverProcess = cp.spawn(pythonPath, args, {
            cwd: backendDir,
            env: { ...process.env, PYTHONPATH: backendDir },
            stdio: ['ignore', 'pipe', 'pipe']
        });

        serverProcess.stdout?.on('data', (data: Buffer) => {
            const text = data.toString();
            console.log('[Context Engine]', text);
            if (text.includes('Application startup complete')) {
                setStatusBarConnected(port);
                provider.postMessage({ type: 'serverStatus', status: 'running', port });
            }
        });

        serverProcess.stderr?.on('data', (data: Buffer) => {
            const text = data.toString();
            console.error('[Context Engine]', text);
            // uvicorn also logs to stderr
            if (text.includes('Application startup complete') || text.includes('Uvicorn running')) {
                setStatusBarConnected(port);
                provider.postMessage({ type: 'serverStatus', status: 'running', port });
            }
        });

        serverProcess.on('close', (code) => {
            console.log(`[Context Engine] Server exited with code ${code}`);
            serverProcess = null;
            setStatusBarDisconnected();
            provider.postMessage({ type: 'serverStatus', status: 'stopped' });
        });

        serverProcess.on('error', (err) => {
            vscode.window.showErrorMessage(`Context Engine: Failed to start server — ${err.message}`);
            serverProcess = null;
            setStatusBarDisconnected();
            provider.postMessage({ type: 'serverStatus', status: 'stopped' });
        });

    } catch (err) {
        vscode.window.showErrorMessage(`Context Engine: ${err}`);
        setStatusBarDisconnected();
    }
}

function stopServer(provider?: SidebarProvider) {
    if (serverProcess) {
        serverProcess.kill();
        serverProcess = null;
        setStatusBarDisconnected();
        provider?.postMessage({ type: 'serverStatus', status: 'stopped' });
        vscode.window.showInformationMessage('Context Engine server stopped.');
    }
}

async function probeServer(provider: SidebarProvider) {
    const config = vscode.workspace.getConfiguration('contextEngine');
    const port = config.get<number>('backendPort') || 8000;

    // Retry with back-off: 0ms → 1s → 3s
    // This ensures we detect a server that started before VS Code loaded the extension.
    const delays = [0, 1000, 3000];
    for (const delay of delays) {
        if (delay > 0) { await new Promise(r => setTimeout(r, delay)); }
        if (serverProcess) { break; } // our own process just started
        try {
            const http = await import('http');
            await new Promise<void>((resolve, reject) => {
                const req = http.get(`http://127.0.0.1:${port}/`, (res) => {
                    if (res.statusCode && res.statusCode < 500) { resolve(); }
                    else { reject(new Error(`HTTP ${res.statusCode}`)); }
                });
                req.on('error', reject);
                req.setTimeout(2000, () => { req.destroy(); reject(new Error('timeout')); });
            });
            setStatusBarConnected(port);
            provider.postMessage({ type: 'serverStatus', status: 'running', port });
            return; // found the server — stop probing
        } catch {
            // Not up yet — continue to next retry
        }
    }
    // All retries exhausted and no owned process started
    if (!serverProcess) {
        setStatusBarDisconnected();
        provider.postMessage({ type: 'serverStatus', status: 'stopped' });
    }
}

// ── Status Bar Helpers ───────────────────────────────────────────────────────

function setStatusBarDisconnected() {
    statusBarItem.text = '$(circle-slash) Context Engine';
    statusBarItem.tooltip = 'Context Engine: Server not running — click to open panel';
    statusBarItem.backgroundColor = undefined;
    statusBarItem.color = new vscode.ThemeColor('statusBarItem.warningForeground');
}

function setStatusBarStarting() {
    statusBarItem.text = '$(loading~spin) Context Engine';
    statusBarItem.tooltip = 'Context Engine: Starting server…';
    statusBarItem.color = undefined;
}

function setStatusBarConnected(port: number) {
    statusBarItem.text = '$(pass-filled) Context Engine';
    statusBarItem.tooltip = `Context Engine: Running on http://127.0.0.1:${port}`;
    statusBarItem.color = new vscode.ThemeColor('statusBarItem.prominentForeground');
}
