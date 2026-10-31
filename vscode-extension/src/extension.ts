import * as vscode from 'vscode';
import * as path from 'path';
import * as cp from 'child_process';
import * as net from 'net';
import * as http from 'http';
import { SidebarProvider } from './sidebarProvider';

let serverProcess: cp.ChildProcess | null = null;
let statusBarItem: vscode.StatusBarItem;
let logger: vscode.LogOutputChannel;

export function activate(context: vscode.ExtensionContext) {
    // ── Native LogOutputChannel ─────────────────────────────────────────────
    logger = vscode.window.createOutputChannel('Context Engine', { log: true });
    context.subscriptions.push(logger);
    logger.info('Activating Context Engine extension…');

    // Initialize dynamic context keys for title bar actions
    void setServerRunningContext(false);
    void setIndexingContext(false);

    // ── Status Bar ──────────────────────────────────────────────────────────
    statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
    statusBarItem.command = 'contextEngine.openSidebar';
    setStatusBarDisconnected();
    statusBarItem.show();
    context.subscriptions.push(statusBarItem);

    // ── Sidebar WebviewView Provider ────────────────────────────────────────
    const sidebarProvider = new SidebarProvider(context, logger);
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
        vscode.commands.registerCommand('contextEngine.indexingInProgress', () => {
            // No-op command used for disabled spinning state in view/title
        }),
        vscode.commands.registerCommand('contextEngine.openSidebar', () => {
            void vscode.commands.executeCommand('workbench.view.extension.context-engine-container');
        })
    );

    // ── Probe server on startup ─────────────────────────────────────────────
    void probeServer(sidebarProvider);

    // ── Auto-start if configured ────────────────────────────────────────────
    const config = vscode.workspace.getConfiguration('contextEngine');
    if (config.get<boolean>('autoStartServer')) {
        void startServer(context, sidebarProvider);
    }

    // ── Ollama preflight ────────────────────────────────────────────────────
    void checkOllama();
}

// ── Dynamic Context Key Helpers ──────────────────────────────────────────────

export async function setServerRunningContext(running: boolean): Promise<void> {
    await vscode.commands.executeCommand('setContext', 'contextEngine.serverRunning', running);
}

export async function setIndexingContext(indexing: boolean): Promise<void> {
    await vscode.commands.executeCommand('setContext', 'contextEngine.isIndexing', indexing);
}

// ── Preflight helpers ────────────────────────────────────────────────────────

const OLLAMA_URL = 'http://localhost:11434';
let ollamaWarnedAt = 0;

/** Resolves true if something already accepts TCP connections on host:port. */
function isPortInUse(port: number, host = '127.0.0.1'): Promise<boolean> {
    return new Promise((resolve) => {
        const socket = new net.Socket();
        const done = (inUse: boolean) => { socket.destroy(); resolve(inUse); };
        socket.setTimeout(500);
        socket.once('connect', () => done(true));
        socket.once('timeout', () => done(false));
        socket.once('error', () => done(false));
        socket.connect(port, host);
    });
}

/** Non-blocking check; shows an actionable warning (at most once per 60s) if Ollama is unreachable. */
export async function checkOllama(): Promise<boolean> {
    const reachable = await new Promise<boolean>((resolve) => {
        const req = http.get(`${OLLAMA_URL}/api/tags`, (res) => {
            res.resume();
            resolve(!!res.statusCode && res.statusCode < 500);
        });
        req.on('error', () => resolve(false));
        req.setTimeout(1500, () => { req.destroy(); resolve(false); });
    });
    if (!reachable && Date.now() - ollamaWarnedAt > 60_000) {
        ollamaWarnedAt = Date.now();
        logger.warn(`Ollama unreachable at ${OLLAMA_URL}`);
        void vscode.window.showWarningMessage(
            'Ollama is not running. Start Ollama to enable local model responses.',
            'Retry Connection'
        ).then((selection) => {
            if (selection === 'Retry Connection') {
                ollamaWarnedAt = 0;
                void checkOllama();
            }
        });
    }
    return reachable;
}

export function deactivate() {
    terminateServerProcessTree();
}

// ── Process Hygiene & Tree Termination ───────────────────────────────────────

function terminateServerProcessTree() {
    if (!serverProcess) {
        return;
    }
    const pid = serverProcess.pid;
    logger?.info(`Stopping Context Engine service (PID: ${pid ?? 'unknown'})…`);

    if (pid) {
        try {
            if (process.platform === 'win32') {
                cp.exec(`taskkill /pid ${pid} /T /F`, (err) => {
                    if (err) {
                        logger?.debug(`taskkill exited with: ${err.message}`);
                    }
                });
            } else {
                // Kill entire POSIX process group (-pid) to prevent orphaned Uvicorn workers
                process.kill(-pid, 'SIGTERM');
                setTimeout(() => {
                    try {
                        process.kill(-pid, 0);
                        process.kill(-pid, 'SIGKILL');
                    } catch {
                        // Process group already exited cleanly
                    }
                }, 1500);
            }
        } catch (err) {
            logger?.warn(`Process group kill fallback: ${err instanceof Error ? err.message : String(err)}`);
            try {
                serverProcess.kill('SIGTERM');
            } catch {
                // Ignore if already dead
            }
        }
    } else {
        serverProcess.kill('SIGTERM');
    }

    serverProcess = null;
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
        vscode.window.setStatusBarMessage('$(check) Context Engine is already running', 3000);
        return;
    }

    const config = vscode.workspace.getConfiguration('contextEngine');
    const pythonPath = config.get<string>('pythonPath') || 'python';
    const port = config.get<number>('backendPort') || 8000;
    const backendDir = getBackendDir();

    // ── Port-in-use preflight with actionable recovery ──
    if (await isPortInUse(port)) {
        logger.warn(`Port ${port} is already in use prior to engine launch.`);
        const connectAction = 'Connect to Existing';
        const changePortAction = 'Change Port';
        const cancelAction = 'Cancel';
        const choice = await vscode.window.showWarningMessage(
            `Port ${port} is already in use. Connect to the existing Context Engine instance or choose another port?`,
            connectAction,
            changePortAction,
            cancelAction
        );
        if (choice === connectAction) {
            setStatusBarConnected(port);
            await setServerRunningContext(true);
            provider.postMessage({ type: 'serverStatus', status: 'running', port });
            vscode.window.setStatusBarMessage(`$(plug) Connected to Context Engine on port ${port}`, 4000);
        } else if (choice === changePortAction) {
            await vscode.commands.executeCommand(
                'workbench.action.openSettings', 'contextEngine.backendPort'
            );
        }
        return;
    }

    setStatusBarStarting();
    provider.postMessage({ type: 'serverStatus', status: 'starting' });
    logger.info(`Starting Context Engine on 127.0.0.1:${port} using '${pythonPath}'…`);

    const args = ['-m', 'uvicorn', 'mcp_server.server:app', '--host', '127.0.0.1', '--port', String(port)];

    try {
        serverProcess = cp.spawn(pythonPath, args, {
            cwd: backendDir,
            env: { ...process.env, PYTHONPATH: backendDir },
            stdio: ['ignore', 'pipe', 'pipe'],
            detached: process.platform !== 'win32' // Required for POSIX process group (-pid) termination
        });

        const handleEngineReady = () => {
            setStatusBarConnected(port);
            void setServerRunningContext(true);
            provider.postMessage({ type: 'serverStatus', status: 'running', port });
            vscode.window.setStatusBarMessage('$(pass-filled) Context Engine ready', 3500);
        };

        serverProcess.stdout?.on('data', (data: Buffer) => {
            const text = data.toString().trim();
            if (text) {
                logger.info(text);
            }
            if (text.includes('Application startup complete')) {
                handleEngineReady();
            }
        });

        serverProcess.stderr?.on('data', (data: Buffer) => {
            const text = data.toString().trim();
            if (text) {
                // Uvicorn emits standard lifecycle logs on stderr
                if (text.includes('ERROR') || text.includes('Traceback')) {
                    logger.error(text);
                } else {
                    logger.info(text);
                }
            }
            if (text.includes('Application startup complete') || text.includes('Uvicorn running')) {
                handleEngineReady();
            }
        });

        serverProcess.on('close', (code) => {
            logger.info(`Context Engine process exited (code ${code ?? 'null'}).`);
            serverProcess = null;
            setStatusBarDisconnected();
            void setServerRunningContext(false);
            void setIndexingContext(false);
            provider.postMessage({ type: 'serverStatus', status: 'stopped' });
        });

        serverProcess.on('error', (err) => {
            logger.error(`Failed to launch Context Engine: ${err.message}`);
            serverProcess = null;
            setStatusBarDisconnected();
            void setServerRunningContext(false);
            provider.postMessage({ type: 'serverStatus', status: 'stopped' });

            void vscode.window.showErrorMessage(
                `Could not start Context Engine with Python interpreter '${pythonPath}'.`,
                'Configure Python Path',
                'Show Logs'
            ).then((choice) => {
                if (choice === 'Configure Python Path') {
                    void vscode.commands.executeCommand('workbench.action.openSettings', 'contextEngine.pythonPath');
                } else if (choice === 'Show Logs') {
                    logger.show();
                }
            });
        });

    } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        logger.error(`Engine spawn exception: ${msg}`);
        setStatusBarDisconnected();
        void setServerRunningContext(false);
    }
}

function stopServer(provider?: SidebarProvider) {
    if (serverProcess) {
        terminateServerProcessTree();
        setStatusBarDisconnected();
        void setServerRunningContext(false);
        void setIndexingContext(false);
        provider?.postMessage({ type: 'serverStatus', status: 'stopped' });
        vscode.window.setStatusBarMessage('$(circle-slash) Context Engine stopped', 3000);
    }
}

async function probeServer(provider: SidebarProvider) {
    const config = vscode.workspace.getConfiguration('contextEngine');
    const port = config.get<number>('backendPort') || 8000;

    const delays = [0, 1000, 3000];
    for (const delay of delays) {
        if (delay > 0) { await new Promise(r => setTimeout(r, delay)); }
        if (serverProcess) { break; }
        try {
            await new Promise<void>((resolve, reject) => {
                const req = http.get(`http://127.0.0.1:${port}/`, (res) => {
                    res.resume();
                    if (res.statusCode && res.statusCode < 500) { resolve(); }
                    else { reject(new Error(`HTTP ${res.statusCode}`)); }
                });
                req.on('error', reject);
                req.setTimeout(2000, () => { req.destroy(); reject(new Error('timeout')); });
            });
            logger.info(`Connected to existing Context Engine service on port ${port}.`);
            setStatusBarConnected(port);
            await setServerRunningContext(true);
            provider.postMessage({ type: 'serverStatus', status: 'running', port });
            return;
        } catch {
            // Not reachable yet — continue to next retry
        }
    }
    if (!serverProcess) {
        setStatusBarDisconnected();
        await setServerRunningContext(false);
        provider.postMessage({ type: 'serverStatus', status: 'stopped' });
    }
}

// ── Status Bar Helpers ───────────────────────────────────────────────────────

function setStatusBarDisconnected() {
    statusBarItem.text = '$(circle-slash) Context Engine';
    statusBarItem.tooltip = 'Context Engine: Offline — Click to open agent panel';
    statusBarItem.backgroundColor = undefined;
    statusBarItem.color = new vscode.ThemeColor('statusBarItem.warningForeground');
}

function setStatusBarStarting() {
    statusBarItem.text = '$(loading~spin) Context Engine';
    statusBarItem.tooltip = 'Context Engine: Starting local service…';
    statusBarItem.color = undefined;
}

function setStatusBarConnected(port: number) {
    statusBarItem.text = '$(pass-filled) Context Engine';
    statusBarItem.tooltip = `Context Engine: Connected (127.0.0.1:${port})`;
    statusBarItem.color = new vscode.ThemeColor('statusBarItem.prominentForeground');
}
