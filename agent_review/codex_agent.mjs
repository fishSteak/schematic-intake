/** Codex CLI transport. Each Agent owns a session; subsequent turns resume its ID. */
import { spawn, spawnSync } from "node:child_process";
import { createInterface } from "node:readline";
import { LocalWorkspace } from "./local_workspace.mjs";

export class AgentError extends Error { isRetryable = false; }

export const Codex = {
  auth: {
    async status() {
      const p = spawnSync(process.env.SCHEMATIC_CODEX || "codex", ["login", "status"], { encoding: "utf8", windowsHide: true });
      if (p.error) throw new AgentError(p.error.message);
      return { status: p.status === 0 ? "logged-in" : "logged-out" };
    },
  },
};

export class Agent {
  static async create(options) {
    const agent = new Agent(options);
    if (options.outputs?.length) agent.workspace = await LocalWorkspace.create(options.local.cwd, options.outputs, options.analysisCheckpointRoot,options.allowPriorAnalysis===true,options.inputs||[]);
    return agent;
  }
  constructor(options) { this.options = options; this.session = null; this.active = null; }
  async [Symbol.asyncDispose]() {
    if (this.active) { await this.active.cancel(); await this.active.wait(); }
    if (this.workspace) await this.workspace.dispose();
  }
  async send(prompt) {
    const args = ["--ask-for-approval", "never", "--sandbox", "workspace-write", "exec"];
    if(this.options.autonomousResearch)args.unshift('--config','sandbox_workspace_write.network_access=true','--search');
    const effort = process.env.SCHEMATIC_CODEX_REASONING_EFFORT;
    if (effort && !["low", "medium", "high", "xhigh", "max", "ultra"].includes(effort)) throw new AgentError("Invalid SCHEMATIC_CODEX_REASONING_EFFORT");
    if (this.session) args.push("resume");
    args.push("--skip-git-repo-check", "--json");
    if (effort) args.push("--config", `model_reasoning_effort="${effort}"`);
    const cwd = this.workspace?.root || this.options.local.cwd;
    if (!this.session) args.push("-C", cwd);
    const model = this.options.model?.id;
    if (model && !["auto", "default"].includes(model)) args.push("--model", model);
    if (this.session) args.push(this.session);
    args.push("-");
    const child = spawn(process.env.SCHEMATIC_CODEX || "codex", args, {
      cwd, env: process.env, windowsHide: true, stdio: ["pipe", "pipe", "pipe"],
    });
    const events = []; let wake; let done = false; let error = ""; let stderr = ""; let cancelled = false;
    child.stderr.on("data", b => { stderr = (stderr + b.toString()).slice(-6000); });
    const emit = e => { events.push(e); if (wake) { wake(); wake = null; } };
    const lines = createInterface({ input: child.stdout });
    lines.on("line", line => {
      let e; try { e = JSON.parse(line); } catch { return; }
      if (e.type === "thread.started") this.session = e.thread_id;
      if (["error", "turn.failed"].includes(e.type)) error = e.message || e.error?.message || JSON.stringify(e);
      if (e.type === "item.completed" && e.item?.type === "agent_message") emit({ type: "assistant", message: { content: [{ type: "text", text: e.item.text }] } });
      if (e.type === "item.started" && e.item?.type === "command_execution") emit({ type: "tool_call", status: "running", name: "exec_command", args: { command: e.item.command } });
      if (e.type === 'item.completed' && e.item?.type === 'web_search') emit({type:'tool_call',status:'completed',name:'web_search',args:e.item});
    });
    const completion = new Promise(resolve => {
      child.once("error", e => { error = e.message; });
      child.once("close", async code => {
        if (code === 0 && !error && this.workspace) {
          try { await this.workspace.collect(); } catch (e) { error = `artifact collection: ${e.message}`; }
        }
        if ((code !== 0 || error || cancelled) && this.workspace) {
          try {
            const checkpoint = await this.workspace.saveAnalysisCheckpoint();
            if (checkpoint) error = `${error || "review interrupted"}; analysis checkpoint: ${checkpoint}`;
          } catch (e) { error = `${error || "review interrupted"}; checkpoint save: ${e.message}`; }
        }
        done = true; emit({ type: "status", status: code === 0 && !error ? "COMPLETED" : "ERROR", message: error || (code ? stderr : "") });
        resolve({ id: this.session, status: cancelled ? "cancelled" : code === 0 && !error ? "completed" : "error", error: error || (code ? stderr : undefined) });
      });
    });
    child.stdin.on("error", () => {});
    child.stdin.end(this.workspace ? this.workspace.prompt(prompt) : prompt);
    const run = {
      wait: () => completion,
      cancel: async () => {
        cancelled = true;
        if (!done && child.pid) {
          if (process.platform === "win32") spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], { windowsHide: true });
          else child.kill("SIGTERM");
        }
      },
      async *stream() {
        while (!done || events.length) {
          if (events.length) yield events.shift();
          else await new Promise(resolve => { wake = resolve; });
        }
      },
    };
    this.active = run;
    return run;
  }
}
