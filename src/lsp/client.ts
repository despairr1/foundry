import { spawn, ChildProcessWithoutNullStreams } from "child_process";
import { EventEmitter } from "events";
import * as fs from "fs";
import * as path from "path";
import { fileURLToPath, pathToFileURL } from "url";
import { RawLSPDiagnostic } from "./diagnostic_parser";
import { logger } from "../utils/logger";

interface PendingRequest {
  resolve: (result: unknown) => void;
  reject: (error: Error) => void;
  timer: NodeJS.Timeout;
}

/** Canonical key for a file URI so client-built and server-published URIs compare equal on every OS. */
export function uriKey(uri: string): string {
  try {
    const resolved = path.resolve(fileURLToPath(uri));
    return process.platform === "win32" ? resolved.toLowerCase() : resolved;
  } catch {
    return uri;
  }
}

/**
 * Finds an executable on PATH. On Windows, npm-installed servers are `.cmd`
 * shims, which `spawn` cannot launch without a shell, so the resolved
 * extension decides whether a shell is needed.
 */
function resolveExecutable(command: string): { file: string; needsShell: boolean } | null {
  const exts = process.platform === "win32" ? (process.env.PATHEXT ?? ".EXE;.CMD;.BAT;.COM").split(";") : [""];
  const dirs = (process.env.PATH ?? "").split(path.delimiter).filter(Boolean);
  for (const dir of dirs) {
    for (const ext of exts) {
      const candidate = path.join(dir, command + ext.toLowerCase());
      try {
        if (fs.statSync(candidate).isFile()) {
          return { file: candidate, needsShell: /\.(cmd|bat)$/i.test(candidate) };
        }
      } catch {
        // Not in this directory.
      }
    }
  }
  return null;
}

export class LSPClient extends EventEmitter {
  private command: string;
  private args: string[];
  private rootPath: string;
  private languageId: string;
  private proc: ChildProcessWithoutNullStreams | null = null;
  private nextRequestId = 1;
  private pendingRequests = new Map<number, PendingRequest>();
  private receiveBuffer: Buffer = Buffer.alloc(0);
  private initialized = false;
  private available = true;
  private documentVersions = new Map<string, number>();

  constructor(command: string, args: string[], rootPath: string, languageId: string) {
    super();
    this.command = command;
    this.args = args;
    this.rootPath = path.resolve(rootPath);
    this.languageId = languageId;
  }

  public isAvailable(): boolean {
    return this.available;
  }

  public async start(timeoutMs = 3000): Promise<boolean> {
    if (this.initialized) return true;
    if (!this.available) return false;

    const executable = resolveExecutable(this.command);
    if (!executable) {
      this.available = false;
      logger.warn("LSPClient", `Language server "${this.command}" not found on PATH; relying on AST structural diagnostics.`);
      return false;
    }

    try {
      // With a shell, Node joins the command line unquoted, so quote paths containing spaces.
      const file = executable.needsShell ? `"${executable.file}"` : executable.file;
      this.proc = spawn(file, this.args, {
        cwd: this.rootPath,
        stdio: ["pipe", "pipe", "pipe"],
        shell: executable.needsShell,
        windowsHide: true
      });
    } catch (err) {
      this.available = false;
      logger.warn("LSPClient", `Unable to spawn ${this.command}: ${String(err)}`);
      return false;
    }

    const proc = this.proc;
    // Persistent handlers: an unhandled 'error' on the process or its stdin (EPIPE after the
    // server dies) would otherwise crash the whole engine.
    proc.on("error", (err) => {
      this.available = false;
      this.initialized = false;
      logger.warn("LSPClient", `${this.command} process error: ${err.message}`);
    });
    proc.stdin.on("error", () => {
      this.initialized = false;
    });
    proc.stdout.on("data", (chunk: Buffer) => this.onStdoutData(chunk));
    proc.stderr.on("data", () => {});
    proc.on("exit", () => {
      this.initialized = false;
      this.documentVersions.clear();
      if (this.proc === proc) this.proc = null;
    });

    try {
      const rootUri = pathToFileURL(this.rootPath).href;
      await this.sendRequest(
        "initialize",
        {
          processId: process.pid,
          rootUri,
          capabilities: {
            textDocument: {
              synchronization: {
                didSave: true,
                dynamicRegistration: false
              },
              publishDiagnostics: {
                relatedInformation: true,
                versionSupport: true
              }
            }
          },
          workspaceFolders: [{ uri: rootUri, name: path.basename(this.rootPath) }]
        },
        timeoutMs
      );
      this.sendNotification("initialized", {});
      this.initialized = true;
      return true;
    } catch (err) {
      logger.warn("LSPClient", `LSP initialize handshake failed for ${this.command}: ${String(err)}`);
      this.available = false;
      await this.shutdown();
      return false;
    }
  }

  public async syncDocumentAndAwaitDiagnostics(
    filePath: string,
    content: string,
    timeoutMs = 800
  ): Promise<RawLSPDiagnostic[]> {
    if (!this.initialized) {
      const started = await this.start(Math.max(timeoutMs, 3000));
      if (!started) return [];
    }

    const absPath = path.isAbsolute(filePath) ? filePath : path.resolve(this.rootPath, filePath);
    const uri = pathToFileURL(absPath).href;
    const key = uriKey(uri);
    const previousVersion = this.documentVersions.get(key);
    const version = (previousVersion ?? 0) + 1;
    this.documentVersions.set(key, version);

    return new Promise<RawLSPDiagnostic[]>((resolve) => {
      const eventName = `diagnostics:${key}`;
      const handler = (diags: RawLSPDiagnostic[], publishedVersion?: number): void => {
        // Ignore diagnostics published for an older revision of the document.
        if (typeof publishedVersion === "number" && publishedVersion < version) return;
        clearTimeout(timer);
        this.removeListener(eventName, handler);
        resolve(diags);
      };
      const timer = setTimeout(() => {
        this.removeListener(eventName, handler);
        resolve([]);
      }, timeoutMs);

      this.on(eventName, handler);

      if (previousVersion === undefined) {
        this.sendNotification("textDocument/didOpen", {
          textDocument: { uri, languageId: this.languageId, version, text: content }
        });
      } else {
        this.sendNotification("textDocument/didChange", {
          textDocument: { uri, version },
          contentChanges: [{ text: content }]
        });
      }
    });
  }

  public async shutdown(): Promise<void> {
    const proc = this.proc;
    if (proc && this.initialized) {
      try {
        await this.sendRequest("shutdown", undefined, 500);
        this.sendNotification("exit", undefined);
      } catch {
        // Fall through to a hard kill.
      }
    }

    for (const [, pending] of this.pendingRequests) {
      clearTimeout(pending.timer);
      pending.reject(new Error("LSP client shutting down"));
    }
    this.pendingRequests.clear();

    if (proc && proc.exitCode === null && !proc.killed) {
      try {
        proc.kill();
      } catch {
        // Already gone.
      }
    }
    this.proc = null;
    this.initialized = false;
    this.documentVersions.clear();
  }

  private writeFrame(message: Record<string, unknown>): void {
    if (!this.proc || !this.proc.stdin.writable) return;
    const payload = JSON.stringify({ jsonrpc: "2.0", ...message });
    this.proc.stdin.write(`Content-Length: ${Buffer.byteLength(payload, "utf8")}\r\n\r\n${payload}`, "utf8");
  }

  private sendRequest(method: string, params: unknown, timeoutMs: number): Promise<unknown> {
    if (!this.proc || !this.proc.stdin.writable) {
      return Promise.reject(new Error(`LSP process ${this.command} stdin not writable`));
    }

    const id = this.nextRequestId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pendingRequests.delete(id);
        reject(new Error(`LSP request ${method} timed out after ${timeoutMs}ms`));
      }, timeoutMs);

      this.pendingRequests.set(id, { resolve, reject, timer });
      this.writeFrame({ id, method, params });
    });
  }

  private sendNotification(method: string, params: unknown): void {
    this.writeFrame({ method, params });
  }

  private onStdoutData(chunk: Buffer): void {
    this.receiveBuffer = Buffer.concat([this.receiveBuffer, chunk]);

    while (true) {
      const headerSeparator = this.receiveBuffer.indexOf("\r\n\r\n");
      if (headerSeparator === -1) break;

      const headerPart = this.receiveBuffer.subarray(0, headerSeparator).toString("ascii");
      const match = /Content-Length:\s*(\d+)/i.exec(headerPart);
      if (!match) {
        this.receiveBuffer = this.receiveBuffer.subarray(headerSeparator + 4);
        continue;
      }

      const contentLength = parseInt(match[1], 10);
      const messageStart = headerSeparator + 4;
      const messageEnd = messageStart + contentLength;

      if (this.receiveBuffer.length < messageEnd) {
        break;
      }

      const jsonBytes = this.receiveBuffer.subarray(messageStart, messageEnd);
      this.receiveBuffer = this.receiveBuffer.subarray(messageEnd);

      let msg: {
        id?: number | string;
        method?: string;
        params?: { uri?: string; version?: number; diagnostics?: RawLSPDiagnostic[] };
        result?: unknown;
        error?: { message?: string };
      };
      try {
        msg = JSON.parse(jsonBytes.toString("utf8"));
      } catch {
        continue;
      }

      if (msg.method !== undefined && msg.id !== undefined) {
        // Server-to-client request (workspace/configuration, client/registerCapability,
        // window/workDoneProgress/create, ...). Servers stall until these are answered.
        this.writeFrame({ id: msg.id, result: null });
      } else if (typeof msg.id === "number" && this.pendingRequests.has(msg.id)) {
        const pending = this.pendingRequests.get(msg.id)!;
        clearTimeout(pending.timer);
        this.pendingRequests.delete(msg.id);
        if (msg.error) {
          pending.reject(new Error(msg.error.message ?? "Unknown JSON-RPC error"));
        } else {
          pending.resolve(msg.result);
        }
      } else if (msg.method === "textDocument/publishDiagnostics" && msg.params?.uri) {
        this.emit(`diagnostics:${uriKey(msg.params.uri)}`, msg.params.diagnostics ?? [], msg.params.version);
      }
    }
  }
}
