import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';

export class SidebarProvider implements vscode.WebviewViewProvider {
    private _view?: vscode.WebviewView;
    private readonly _context: vscode.ExtensionContext;

    constructor(context: vscode.ExtensionContext) {
        this._context = context;
    }

    public resolveWebviewView(
        webviewView: vscode.WebviewView,
        _context: vscode.WebviewViewResolveContext,
        _token: vscode.CancellationToken
    ) {
        this._view = webviewView;

        webviewView.webview.options = {
            enableScripts: true,
            localResourceRoots: [
                vscode.Uri.joinPath(this._context.extensionUri, 'webview')
            ]
        };

        webviewView.webview.html = this._getHtml(webviewView.webview);

        // ── Handle messages from the webview ──────────────────────────────
        webviewView.webview.onDidReceiveMessage(async (message) => {
            switch (message.type) {
                case 'getActiveFile':
                    await this._sendActiveFile();
                    break;
                case 'getActiveFileInfo': {
                    // Returns the full content of the active file for use as file-scope context.
                    // Posts back as 'activeFileForScope' so the webview can distinguish it from
                    // the chip-based 'activeFile' response.
                    const editor = vscode.window.activeTextEditor;
                    if (editor) {
                        const doc = editor.document;
                        this.postMessage({
                            type: 'activeFileForScope',
                            file: {
                                path: doc.uri.fsPath,
                                relativePath: vscode.workspace.asRelativePath(doc.uri),
                                language: doc.languageId,
                                content: doc.getText(),
                                lineCount: doc.lineCount
                            }
                        });
                    } else {
                        this.postMessage({ type: 'activeFileForScope', file: null });
                    }
                    break;
                }
                case 'getSelection':
                    await this._sendSelection();
                    break;
                case 'applyToFile':
                    await this._applyToFile(message.filePath, message.content);
                    break;
                case 'openFile':
                    await this._openFile(message.filePath, message.line);
                    break;
                case 'getWorkspacePath':
                    this.postMessage({
                        type: 'workspacePath',
                        path: vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || ''
                    });
                    break;
                case 'loadCustomModels':
                    // Read persisted custom models from globalState
                    this.postMessage({
                        type: 'customModels',
                        models: this._context.globalState.get<string[]>('customModels', [])
                    });
                    break;
                case 'saveCustomModels':
                    // Persist custom models list across sessions
                    await this._context.globalState.update('customModels', message.models || []);
                    break;
                case 'startServer':
                    vscode.commands.executeCommand('contextEngine.startServer');
                    break;
                case 'stopServer':
                    vscode.commands.executeCommand('contextEngine.stopServer');
                    break;
                case 'showError':
                    vscode.window.showErrorMessage(`Context Engine: ${message.text}`);
                    break;
                case 'showInfo':
                    vscode.window.showInformationMessage(`Context Engine: ${message.text}`);
                    break;
                case 'copyToClipboard':
                    await vscode.env.clipboard.writeText(message.text);
                    break;
            }
        });

        // ── Send editor context when active editor changes ─────────────────
        vscode.window.onDidChangeActiveTextEditor(() => {
            this._sendActiveFileInfo();
        });

        vscode.window.onDidChangeTextEditorSelection(() => {
            this._sendSelectionInfo();
        });
    }

    public postMessage(message: object) {
        this._view?.webview.postMessage(message);
    }

    // ── Active file / selection helpers ───────────────────────────────────

    private async _sendActiveFile() {
        const editor = vscode.window.activeTextEditor;
        if (!editor) {
            this.postMessage({ type: 'activeFile', file: null });
            return;
        }
        const doc = editor.document;
        this.postMessage({
            type: 'activeFile',
            file: {
                path: doc.uri.fsPath,
                relativePath: vscode.workspace.asRelativePath(doc.uri),
                language: doc.languageId,
                content: doc.getText(),
                lineCount: doc.lineCount
            }
        });
    }

    private async _sendSelection() {
        const editor = vscode.window.activeTextEditor;
        if (!editor || editor.selection.isEmpty) {
            this.postMessage({ type: 'selection', selection: null });
            return;
        }
        const sel = editor.selection;
        this.postMessage({
            type: 'selection',
            selection: {
                path: editor.document.uri.fsPath,
                relativePath: vscode.workspace.asRelativePath(editor.document.uri),
                language: editor.document.languageId,
                text: editor.document.getText(sel),
                startLine: sel.start.line + 1,
                endLine: sel.end.line + 1
            }
        });
    }

    private _sendActiveFileInfo() {
        const editor = vscode.window.activeTextEditor;
        if (!editor) { return; }
        this.postMessage({
            type: 'editorChanged',
            file: {
                relativePath: vscode.workspace.asRelativePath(editor.document.uri),
                language: editor.document.languageId
            }
        });
    }

    private _sendSelectionInfo() {
        const editor = vscode.window.activeTextEditor;
        if (!editor || editor.selection.isEmpty) {
            this.postMessage({ type: 'selectionChanged', selection: null });
            return;
        }
        const sel = editor.selection;
        this.postMessage({
            type: 'selectionChanged',
            selection: {
                relativePath: vscode.workspace.asRelativePath(editor.document.uri),
                startLine: sel.start.line + 1,
                endLine: sel.end.line + 1
            }
        });
    }

    private async _applyToFile(filePath: string, content: string) {
        if (!filePath) {
            vscode.window.showErrorMessage('Context Engine: No file path specified for apply.');
            return;
        }
        const uri = vscode.Uri.file(filePath);
        const doc = await vscode.workspace.openTextDocument(uri);
        const editor = await vscode.window.showTextDocument(doc);
        const edit = new vscode.WorkspaceEdit();
        const fullRange = new vscode.Range(
            doc.positionAt(0),
            doc.positionAt(doc.getText().length)
        );
        edit.replace(uri, fullRange, content);
        await vscode.workspace.applyEdit(edit);
        vscode.window.showInformationMessage('Context Engine: Changes applied to file.');
    }

    private async _openFile(filePath: string, line?: number) {
        const uri = vscode.Uri.file(filePath);
        const doc = await vscode.workspace.openTextDocument(uri);
        const editor = await vscode.window.showTextDocument(doc);
        if (line !== undefined) {
            const pos = new vscode.Position(line - 1, 0);
            editor.selection = new vscode.Selection(pos, pos);
            editor.revealRange(new vscode.Range(pos, pos), vscode.TextEditorRevealType.InCenter);
        }
    }

    // ── HTML ───────────────────────────────────────────────────────────────

    private _getHtml(webview: vscode.Webview): string {
        const webviewDir = vscode.Uri.joinPath(this._context.extensionUri, 'webview');

        const styleUri = webview.asWebviewUri(vscode.Uri.joinPath(webviewDir, 'style.css'));
        const scriptUri = webview.asWebviewUri(vscode.Uri.joinPath(webviewDir, 'main.js'));

        const nonce = getNonce();

        return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta http-equiv="Content-Security-Policy"
    content="default-src 'none';
             style-src ${webview.cspSource} 'unsafe-inline';
             script-src 'nonce-${nonce}';
             connect-src http://127.0.0.1:* http://localhost:*;
             img-src ${webview.cspSource} data:;" />
  <link rel="stylesheet" href="${styleUri}" />
  <title>Context Engine</title>
</head>
<body>
  <div id="app"></div>
  <script nonce="${nonce}" src="${scriptUri}"></script>
</body>
</html>`;
    }
}

function getNonce(): string {
    let text = '';
    const possible = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
    for (let i = 0; i < 32; i++) {
        text += possible.charAt(Math.floor(Math.random() * possible.length));
    }
    return text;
}
