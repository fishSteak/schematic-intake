/**
 * Codex CLI fanout for agent_review.
 * Modes: context | judge | critique | path_draft | mpn_vr | mpn_param
 * Default: concurrency 4 (Judge 同时 4 路 SDK), model auto
 *
 * node agent_review/orchestrator.mjs --board <boardDir> --mode judge|critique|mpn_vr|mpn_param [--concurrency 4] [--model auto] [--resume]
 */
import { Agent, Codex, AgentError } from "./codex_agent.mjs";
import { appendFile, mkdir, readFile, writeFile, access } from "node:fs/promises";
import { constants } from "node:fs";
import {spawnSync} from "node:child_process";
import {runDeliveryJob} from "./delivery_worker.mjs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const DEFAULT_CONCURRENCY = 4;
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");

function argValue(flag) {
  const i = process.argv.indexOf(flag);
  return i >= 0 ? process.argv[i + 1] : null;
}
function hasFlag(flag) {
  return process.argv.includes(flag);
}
async function exists(p) {
  try {
    await access(p, constants.F_OK);
    return true;
  } catch {
    return false;
  }
}
async function readJson(p) {
  return JSON.parse(await readFile(p, "utf8"));
}
async function writeJson(p, data) {
  await mkdir(path.dirname(p), { recursive: true });
  await writeFile(p, JSON.stringify(data, null, 2) + "\n", "utf8");
}

function safeName(s) {
  // Keep in sync with agent_review/batching.py::safe_object_name
  return String(s)
    .replaceAll("/", "_")
    .replaceAll("\\", "_")
    .replaceAll(":", "_")
    .replaceAll("+", "p")
    .replaceAll(" ", "_")
    .slice(0, 180);
}

async function mapPool(items, concurrency, fn, onResult=null) {
  const ret = new Array(items.length);
  let i = 0;
  let done = 0;
  async function worker() {
    while (i < items.length) {
      const idx = i++;
      const started = Date.now();
      ret[idx] = await fn(items[idx], idx);
      if(onResult)await onResult(ret[idx]);
      done += 1;
      const r = ret[idx] || {};
      console.log(
        `JOB\t${done}/${items.length}\t${r.ok ? "ok" : "bad"}\t${r.job_id || ""}\t${Date.now() - started}ms\t${r.error || ""}`
      );
    }
  }
  const n = Math.min(concurrency, Math.max(1, items.length));
  await Promise.all(Array.from({ length: n }, () => worker()));
  return ret;
}

function resolveModel(model) {
  // SDK expects { id }; "auto" is invalid and resolves to undefined.
  return { id: !model || model === "auto" ? "default" : model };
}

const JOB_TIMEOUT_MS = Number(process.env.AGENT_JOB_TIMEOUT_MS || 180000);
// P11 三轮（分析→自检→落卡）单轮默认 10min（复杂件写透+读手册可能更久）；可用 AGENT_P11_TURN_TIMEOUT_MS 覆盖
const P11_TURN_TIMEOUT_MS = Number(process.env.AGENT_P11_TURN_TIMEOUT_MS || 600000);

function clipText(value, n = 200) {
  const t = String(value ?? "").replace(/\s+/g, " ").trim();
  return t.length > n ? t.slice(0, n) : t;
}

function toolTarget(args) {
  if (args == null) return "";
  if (typeof args !== "object") return clipText(args);
  const t =
    args.path ||
    args.file ||
    args.target_file ||
    args.command ||
    args.cmd ||
    args.pattern ||
    args.query ||
    args.glob_pattern ||
    "";
  return clipText(t);
}

async function appendEvents(logPaths, row) {
  const paths = (logPaths || []).filter(Boolean);
  if (!paths.length) return;
  const line = JSON.stringify({ ts: new Date().toISOString(), ...row }) + "\n";
  for (const p of paths) {
    await mkdir(path.dirname(p), { recursive: true });
    await appendFile(p, line, "utf8");
  }
}

async function consumeStream(run, logPaths, turn, bucket) {
  try {
    for await (const msg of run.stream()) {
      const type = msg?.type;
      if (type === "thinking") {
        bucket.thinking += String(msg.text || "").length;
        continue;
      }
      if (type === "assistant") {
        for (const block of msg.message?.content || []) {
          if (block?.type === "text" && block.text) {
            await appendEvents(logPaths, { turn, type: "assistant", text: block.text });
          }
        }
        continue;
      }
      if (type === "tool_call" && msg.status === "running") {
        await appendEvents(logPaths, {
          turn,
          type: "tool",
          tool: msg.name || "",
          target: toolTarget(msg.args),
        });
        continue;
      }
      if (type === "tool_call" && msg.status === "error") {
        await appendEvents(logPaths, {
          turn,
          type: "tool_error",
          tool: msg.name || "",
          error: clipText(typeof msg.result === "string" ? msg.result : JSON.stringify(msg.result || "")),
        });
        continue;
      }
      if (type === "status" && (msg.status === "ERROR" || msg.status === "CANCELLED")) {
        await appendEvents(logPaths, {
          turn,
          type: "status",
          status: msg.status,
          error: clipText(msg.message || ""),
        });
      }
    }
  } catch (e) {
    await appendEvents(logPaths, { turn, type: "stream_error", error: clipText(e?.message || e) });
  } finally {
    if (bucket.thinking) {
      await appendEvents(logPaths, { turn, type: "thinking", chars: bucket.thinking });
      bucket.thinking = 0;
    }
  }
}

async function sendTurn(agent, prompt, timeoutMs, logPaths, turn) {
  const run = await agent.send(prompt);
  const bucket = { thinking: 0 };
  const streaming = consumeStream(run, logPaths, turn, bucket);
  try {
    const result = await waitRun(run, timeoutMs);
    await Promise.race([streaming, new Promise((resolve) => setTimeout(resolve, 2000))]);
    return result;
  } catch (e) {
    await Promise.race([streaming, new Promise((resolve) => setTimeout(resolve, 2000))]);
    const message = clipText(e?.message || e);
    await appendEvents(logPaths, { turn, type: "timeout", error: message });
    throw new Error(`turn${turn} ${message}`);
  }
}

async function waitRun(run, timeoutMs) {
  let timer;
  try {
    return await Promise.race([
      run.wait(),
      new Promise((_, reject) => {
        timer = setTimeout(async () => {
          try {
            await run.cancel();
          } catch {
            /* ignore */
          }
          reject(new Error(`timeout ${timeoutMs}ms`));
        }, timeoutMs);
      }),
    ]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

async function promptWithTimeout(prompt, agentOpts, timeoutMs = JOB_TIMEOUT_MS) {
  await using agent = await Agent.create(agentOpts);
  const run = await agent.send(prompt);
  return await waitRun(run, timeoutMs);
}

/** 同一 Agent 会话多轮 send（P11：分析→自检→落卡） */
async function promptTurnsWithTimeout(turns, agentOpts, timeoutMs = JOB_TIMEOUT_MS, logPaths = []) {
  const decisionPolicy = agentOpts.contractVersion === "review.v1"
    ? await readFile(path.join(ROOT, "agent_review/prompts/judge/review_v1.power.md"), "utf8")
    : agentOpts.ruleId === "P11-A01"
    ? await readFile(path.join(ROOT, "agent_review/prompts/judge/_power_decision.md"), "utf8") : "";
  await using agent = await Agent.create(agentOpts);
  const results = [];
  const started = Date.now();
  for (let i = 0; i < turns.length; i++) {
    const turn = turns[i];
    let result;
    try {
      result = await sendTurn(
        agent,
        turn.prompt + (decisionPolicy ? `\n\n${decisionPolicy}` : ""),
        turn.timeoutMs || timeoutMs,
        logPaths,
        i + 1
      );
    } catch (e) {
      return {
        ok: false,
        error: String(e?.message || e).slice(0, 240),
        results,
      };
    }
    results.push(result);
    if (result.status === "error" || result.status === "cancelled") {
      const detail =
        result.error?.message || result.error?.code || result.error || result.status;
      return {
        ok: false,
        error: `turn${i + 1} ${detail}`.slice(0, 240),
        run: result.id,
        results,
      };
    }
    if (typeof turn.after === "function") {
      const check = await turn.after();
      if (check && check.ok === false) {
        return { ok: false, error: check.error || `turn${i + 1} check failed`, run: result.id, results };
      }
    }
    if(turn.stage && agentOpts.executionHash && agent.workspace) {
      await agent.workspace.saveStageCheckpoint(agentOpts,turn.stage);
    }
    await appendEvents(logPaths,{type:'phase_completed',phase:turn.stage||'card',elapsed_ms:Date.now()-started,run_id:result.id});
  }
  if(agent.workspace) {
    for(const output of agentOpts.outputs||[]) {
      if(output.endsWith('.json') && !await exists(path.join(agent.workspace.root,agent.workspace.relative(output))))
        return {ok:false,error:`requested JSON was not generated: ${path.basename(output)}`,results};
    }
  }
  return { ok: true, results, run: results[results.length - 1]?.id, elapsed_ms:Date.now()-started };
}

async function ensureAuth() {
  try {
    const st = await Codex.auth.status();
    // SDK returns { status: "logged-in"|"logged-out", ... }
    if (st?.status === "logged-in" || st?.loggedIn) return;
  } catch {
    /* ignore */
  }
  {
    console.error("FAIL\tCodex auth missing: run codex login");
    process.exit(3);
  }
}

async function runContractJob(boardDir, job, model, critique = false) {
  const rid = job.rule_id;
  const common = await readFile(path.join(ROOT, "agent_review/prompts/judge/review_v1.common.md"), "utf8");
  const analysisPrompt = await readFile(path.join(ROOT, `agent_review/prompts/judge/${rid}.analysis.md`), "utf8");
  const selfCheck = await readFile(path.join(ROOT, "agent_review/prompts/judge/review_v1.self_check.md"), "utf8");
  const paths = job.objects.map(obj => path.join(boardDir, "review_out/agent_jobs", rid, safeName(obj)));
  const outputs = paths.flatMap(p => [p+".json", p+".analysis.md"]);
  if(job.autonomous_evidence)outputs.push(...paths.map(p=>p+'.research.json'));
  const location = paths.map(p => `工作台 ${p}.context.json；契约 ${p}.schema.json；分析 ${p}.analysis.md；最终 JSON ${p}.json${job.autonomous_evidence?`；独立研究记录 ${p}.research.json（契约 ${p}.research.schema.json）`:''}`).join("\n");
  const researchCommon=job.autonomous_evidence?await readFile(path.join(ROOT,'agent_review/prompts/autonomous_judge.md'),'utf8'):'';
  const prefix = `${common}\n${researchCommon}\n\n${location}\n先读工作台及其 contract_task。只写指定分析与 JSON，不改材料。`;
  const agentOpts = { model: resolveModel(model), ruleId: rid, contractVersion: "review.v1",
    autonomousResearch:job.autonomous_evidence===true,
    allowPriorAnalysis:critique,
    local: { cwd: boardDir }, outputs,
    executionHash:job.execution_hash,
    analysisCheckpointRoot: job.checkpoint_root || process.env.AGENT_ANALYSIS_CHECKPOINT_ROOT || null };
  const ensureAnalysis = async () => ({ok: (await Promise.all(paths.map(p => exists(p+".analysis.md")))).every(Boolean), error: "analysis artifact missing"});
  const turns = [
    ...(!critique && !job.resume_stage ? [{prompt: `${prefix}\n${analysisPrompt}\n第1轮：只写完整分析。后续落卡只用 review.v1，不用旧卡字段。`, timeoutMs:P11_TURN_TIMEOUT_MS, after:ensureAnalysis,stage:'analysis'}] : []),
    ...(job.resume_stage==='self_check' || (critique && process.env.AGENT_RESUME_CARD_ONLY === "1") ? [] : [{prompt:`${prefix}\n${selfCheck}\n第2轮：自检并修订指定分析，不写 JSON。`,timeoutMs:P11_TURN_TIMEOUT_MS,after:ensureAnalysis,stage:'self_check'}]),
    {prompt:`${prefix}\n第3轮：按已自检分析写最终 review.v1 JSON。必须先读 .schema.json 和 contract_task；仅压缩分析，不能另造答案。所有必填字段按 Schema；无图的 route=null。${job.autonomous_evidence?'同时写独立的 .research.json，不能只交判定卡；即使没有新事实，也记录实际查证来源和结果。':''}若当前工作台附 contract_validation_errors，逐项修复这些结构/一致性问题，禁止通过修改材料或编造工作点来过门禁。`, timeoutMs:P11_TURN_TIMEOUT_MS},
  ];
  try {
    const multi = await promptTurnsWithTimeout(turns, agentOpts, P11_TURN_TIMEOUT_MS, paths.map(p=>p+".run.jsonl"));
    return {job_id:job.job_id,ok:multi.ok,error:multi.error,run:multi.run,mode:"review.v1",elapsed_ms:multi.elapsed_ms};
  } catch (e) { return {job_id:job.job_id,ok:false,error:String(e?.message||e)}; }
}

async function runJudgeJob(boardDir, job, model) {
  if(job.delivery_version)return runDeliveryJob(boardDir,job,model);
  if (job.contract_version === "review.v1") return runContractJob(boardDir, job, model);
  const out = path.join(boardDir, "review_out");
  const rid = job.rule_id;
  const promptPath = path.join(ROOT, "agent_review", "prompts", "judge", `${rid}.md`);
  const analysisPromptPath = path.join(ROOT, "agent_review", "prompts", "judge", `${rid}.analysis.md`);
  const ruleSelfCheckPath = path.join(ROOT, "agent_review", "prompts", "judge", `${rid}.self_check.md`);
  const deepSelfCheckPath = path.join(ROOT, "agent_review", "prompts", "judge", "_deep_self_check.md");
  const legacySelfCheckPath = path.join(ROOT, "agent_review", "prompts", "judge", "_p11_self_check.md");
  const commonPath = path.join(ROOT, "agent_review", "prompts", "_common_judge.md");
  const common = (await exists(commonPath)) ? await readFile(commonPath, "utf8") : "";
  if (!(await exists(promptPath))) {
    return { job_id: job.job_id, ok: false, error: `missing prompt ${rid}` };
  }
  const rulePrompt = await readFile(promptPath, "utf8");
  const objects = job.objects || [];
  if (!objects.length) return { job_id: job.job_id, ok: true, empty: true };

  const outPaths = objects.map(
    (o) => path.join(out, "agent_jobs", rid, `${safeName(o)}.json`)
  );
  for (const p of outPaths) await mkdir(path.dirname(p), { recursive: true });

  // 深审：白名单由 Python 侧过滤 jobs；此处以是否存在 .analysis.md 决定三轮
  const deep = await exists(analysisPromptPath);
  const agentOpts = {
    model: resolveModel(model),
    ruleId: rid,
    executionHash:job.execution_hash,
    analysisCheckpointRoot:job.checkpoint_root || null,
    local: { cwd: boardDir },
    outputs: [...outPaths, ...objects.map(o => path.join(out, "agent_jobs", rid, `${safeName(o)}.analysis.md`)),
      ...(job.autonomous_evidence?objects.map(o=>path.join(out,'agent_jobs',rid,`${safeName(o)}.research.json`)):[])],
    autonomousResearch:job.autonomous_evidence===true,
  };


  try {
    // 深审：同会话三轮 —— 分析 → 无点名自检 → 落 JSON
    if (deep && (await exists(analysisPromptPath))) {
      const analysisPrompt = await readFile(analysisPromptPath, "utf8");
      // 自检优先级：规则专用 → P11-A01/A03 专用长检 → 通用深审短检
      let selfCheckPromptPath = null;
      if (await exists(ruleSelfCheckPath)) selfCheckPromptPath = ruleSelfCheckPath;
      else if (
        (rid === "P11-A01" || rid === "P11-A03") &&
        (await exists(legacySelfCheckPath))
      ) {
        selfCheckPromptPath = legacySelfCheckPath;
      } else if (await exists(deepSelfCheckPath)) {
        selfCheckPromptPath = deepSelfCheckPath;
      } else if (await exists(legacySelfCheckPath)) {
        selfCheckPromptPath = legacySelfCheckPath;
      }
      const selfCheckPrompt = selfCheckPromptPath
        ? await readFile(selfCheckPromptPath, "utf8")
        : "重新阅读分析 Markdown；有自相矛盾则改正文；不要写 JSON。";
      const analysisPaths = objects.map(
        (o) => path.join(out, "agent_jobs", rid, `${safeName(o)}.analysis.md`)
      );
      const ensureAnalysis = async () => {
        const missing = [];
        for (const p of analysisPaths) {
          if (!(await exists(p))) missing.push(p);
        }
        if (missing.length) {
          return { ok: false, error: `analysis not written: ${missing.join("; ")}` };
        }
        return { ok: true };
      };
      const turn1 = `${common}

${analysisPrompt}

【本批任务 · 第 1 轮 · 只写分析】
board: ${boardDir}
rule_id: ${rid}
objects: ${JSON.stringify(objects)}
对每个 object 只写分析 Markdown：
${analysisPaths.map((p, i) => `- ${objects[i]} → ${p}`).join("\n")}
先读：review_out/agent_jobs/${rid}/<object>.context.json（工作台）；需要时再读 context_store / PDF。
禁止写 JSON 判定卡；禁止写 context_store；禁止改其他 rule。
`;
      const turnSelf = `${common}

${selfCheckPrompt}

【本批任务 · 第 2 轮 · 自检】
board: ${boardDir}
rule_id: ${rid}
objects: ${JSON.stringify(objects)}
只修订这些分析文件（若有矛盾）：
${analysisPaths.map((p, i) => `- ${objects[i]} → ${p}`).join("\n")}
禁止写 JSON；禁止写 context_store；禁止改其他 rule。
`;
      const turnCard = `${common}

${rulePrompt}

【本批任务 · 最后一轮 · 落判定卡】
board: ${boardDir}
rule_id: ${rid}
objects: ${JSON.stringify(objects)}
对每个 object：
1. 先读已完成分析：review_out/agent_jobs/${rid}/<object>.analysis.md
2. 再核对工作台 .context.json
3. 每个对象写一个正式判定 JSON（宁少勿凑态）：
${outPaths.map((p, i) => `- ${objects[i]} → ${p}`).join("\n")}
${job.autonomous_evidence?`4. 同时写各对象独立研究记录，不可省略：\n${objects.map(obj=>`- ${obj} → ${path.join(out,'agent_jobs',rid,safeName(obj)+'.research.json')}，按旁边 .research.schema.json。没有新增事实也记录实际查证；待确认必须分类说明未解请求。`).join('\n')}`:''}
禁止写 context_store；禁止改其他 rule。
JSON 必须与分析一致；分析已否定的态不得再编成完好稳态。
`;
      const runLogs = objects.map((o) =>
        path.join(out, "agent_jobs", rid, `${safeName(o)}.run.jsonl`)
      );
      const multi = await promptTurnsWithTimeout(
        [
          ...(!job.resume_stage ? [{ prompt: turn1, timeoutMs: P11_TURN_TIMEOUT_MS, after: ensureAnalysis,stage:'analysis' }] : []),
          ...(job.self_check!==false && job.resume_stage!=='self_check' ? [{ prompt: turnSelf, timeoutMs: P11_TURN_TIMEOUT_MS, after: ensureAnalysis,stage:'self_check' }] : []),
          { prompt: turnCard, timeoutMs: P11_TURN_TIMEOUT_MS },
        ],
        agentOpts,
        P11_TURN_TIMEOUT_MS,
        runLogs
      );
      if (!multi.ok) {
        return { job_id: job.job_id, ok: false, error: multi.error, run: multi.run };
      }
      return { job_id: job.job_id, ok: true, run: multi.run, mode:job.self_check===false?'analyze_card':"analyze_selfcheck_card",elapsed_ms:multi.elapsed_ms };
    }

const readOrder = `先读 review_out/agent_jobs/${rid}/<object>.context.json（若存在）与 review_out/context_store/ 中对应 ready 条目（只读）。`;
    const prompt = `${common}

${rulePrompt}

【本批任务】
board: ${boardDir}
rule_id: ${rid}
objects: ${JSON.stringify(objects)}
对每个 object 只写一个文件到：
${outPaths.map((p, i) => `- ${objects[i]} → ${p}`).join("\n")}
${readOrder}
禁止写 context_store、禁止改其他 rule。
`;
    const result = await promptWithTimeout(prompt, agentOpts);
    if (result.status === "error" || result.status === "cancelled") {
      return { job_id: job.job_id, ok: false, error: `run ${result.status}`, run: result.id };
    }
    return { job_id: job.job_id, ok: true, run: result.id };
  } catch (err) {
    if (err instanceof AgentError) {
      return { job_id: job.job_id, ok: false, error: err.message, retryable: err.isRetryable };
    }
    return { job_id: job.job_id, ok: false, error: String(err?.message || err) };
  }
}

/** 难例续聊：已有 analysis.md 时再追问一轮，并同步 JSON */
async function runCritiqueJob(boardDir, job, model) {
  if (job.contract_version === "review.v1") return runContractJob(boardDir, job, model, true);
  const out = path.join(boardDir, "review_out");
  const rid = job.rule_id;
  const analysisPromptPath = path.join(ROOT, "agent_review", "prompts", "judge", `${rid}.analysis.md`);
  if (!(await exists(analysisPromptPath))) {
    return { job_id: job.job_id, ok: false, error: "critique needs .analysis.md (deep rules only)" };
  }
  const objects = job.objects || [];
  if (!objects.length) return { job_id: job.job_id, ok: true, empty: true };
  const commonPath = path.join(ROOT, "agent_review", "prompts", "_common_judge.md");
  const critiquePath = path.join(ROOT, "agent_review", "prompts", "judge", "_p11_critique.md");
  const cardPromptPath = path.join(ROOT, "agent_review", "prompts", "judge", `${rid}.md`);
  const common = (await exists(commonPath)) ? await readFile(commonPath, "utf8") : "";
  const critiquePrompt = (await exists(critiquePath))
    ? await readFile(critiquePath, "utf8")
    : "把分析写透；改正文；必要时同步 JSON。";
  const rulePrompt = (await exists(cardPromptPath)) ? await readFile(cardPromptPath, "utf8") : "";
  const analysisPaths = objects.map(
    (o) => path.join(out, "agent_jobs", rid, `${safeName(o)}.analysis.md`)
  );
  const outPaths = objects.map((o) => path.join(out, "agent_jobs", rid, `${safeName(o)}.json`));
  const missing = [];
  for (const p of analysisPaths) {
    const checkpoint = process.env.AGENT_ANALYSIS_CHECKPOINT_ROOT;
    const alternative = checkpoint ? path.join(checkpoint, path.relative(boardDir, p)) : null;
    if (!(await exists(p)) && !(alternative && await exists(alternative))) missing.push(p);
  }
  if (missing.length) {
    return { job_id: job.job_id, ok: false, error: `critique needs analysis: ${missing.join("; ")}` };
  }
  const agentOpts = {
    model: resolveModel(model),
    ruleId: rid,
    local: { cwd: boardDir },
    outputs: [...outPaths, ...analysisPaths],
    autonomousResearch:job.autonomous_evidence===true,
    allowPriorAnalysis:true,
    analysisCheckpointRoot: process.env.AGENT_ANALYSIS_CHECKPOINT_ROOT || null,
  };

  if(job.autonomous_evidence)agentOpts.outputs.push(...objects.map(o=>path.join(out,'agent_jobs',rid,`${safeName(o)}.research.json`)));

  const turn1 = `${common}

${critiquePrompt}

【续聊 · 写透】
board: ${boardDir}
rule_id: ${rid}
objects: ${JSON.stringify(objects)}
修订：
${analysisPaths.map((p, i) => `- ${objects[i]} → ${p}`).join("\n")}
可先读同目录 .json / .context.json；禁止写 context_store；禁止改其他 rule。
`;
  const turn2 = `${common}

${rulePrompt}

【续聊 · 同步汇总卡】
board: ${boardDir}
rule_id: ${rid}
objects: ${JSON.stringify(objects)}
按已修订分析重写 JSON（仅汇总，勿另起炉灶）：
${outPaths.map((p, i) => `- ${objects[i]} → ${p}`).join("\n")}
${job.autonomous_evidence?`同时交付独立的研究记录：${objects.map(obj=>path.join(out,'agent_jobs',rid,safeName(obj)+'.research.json')).join('；')}。没有新事实也记录实际来源与查证结果，不能只交判定卡。`:''}
禁止写 context_store；禁止改其他 rule。
`;
  try {
    const runLogs = objects.map((o) =>
      path.join(out, "agent_jobs", rid, `${safeName(o)}.run.jsonl`)
    );
    const multi = await promptTurnsWithTimeout(
      [
        ...(process.env.AGENT_RESUME_CARD_ONLY === "1" ? [] : [{ prompt: turn1, timeoutMs: P11_TURN_TIMEOUT_MS }]),
        { prompt: turn2, timeoutMs: P11_TURN_TIMEOUT_MS },
      ],
      agentOpts,
      P11_TURN_TIMEOUT_MS,
      runLogs
    );
    if (!multi.ok) {
      return { job_id: job.job_id, ok: false, error: multi.error, run: multi.run };
    }
    return { job_id: job.job_id, ok: true, run: multi.run, mode: "critique" };
  } catch (err) {
    if (err instanceof AgentError) {
      return { job_id: job.job_id, ok: false, error: err.message, retryable: err.isRetryable };
    }
    return { job_id: job.job_id, ok: false, error: String(err?.message || err) };
  }
}

async function runPathDraftJob(boardDir, job, model) {
  const out = path.join(boardDir, "review_out");
  const domainId = job.domain_id;
  const promptPath = path.join(ROOT, "agent_review", "prompts", "path_draft.md");
  if (!(await exists(promptPath))) {
    return { job_id: job.job_id, ok: false, error: "missing path_draft.md" };
  }
  const body = await readFile(promptPath, "utf8");
  const outPath = path.join(out, "board_paths", "draft", `${domainId}.json`);
  await mkdir(path.dirname(outPath), { recursive: true });
  const prompt = `${body}

【本域任务】
board: ${boardDir}
domain_id: ${domainId}
page_names: ${JSON.stringify(job.page_names || [])}
先读：${job.slice_path}
只写：${job.out_path}
禁止输出电压/电流/功率数字；禁止改其它文件。
`;
  const agentOpts = {
    model: resolveModel(model),
    local: { cwd: boardDir },
    outputs: [outPath],
  };

  try {
    const result = await promptWithTimeout(prompt, agentOpts);
    const wrote = await exists(outPath);
    return {
      job_id: job.job_id,
      ok: wrote && result.status !== "error" && result.status !== "cancelled",
      run: result.id,
      wrote,
    };
  } catch (err) {
    if (err instanceof AgentError) {
      return { job_id: job.job_id, ok: false, error: err.message };
    }
    return { job_id: job.job_id, ok: false, error: String(err?.message || err) };
  }
}

async function runMpnParamJob(boardDir, job, model) {
  const mpn = job.mpn;
  const field = job.field;
  const promptPath = path.join(ROOT, "agent_review", "prompts", "mpn_param.md");
  if (!(await exists(promptPath))) {
    return { job_id: job.job_id, ok: false, error: "missing mpn_param.md" };
  }
  const body = await readFile(promptPath, "utf8");
  const outRel =
    job.out_path ||
    `review_out/cache/mpn/${safeName(mpn)}/agent_${String(field || "field").replace(/[^A-Za-z0-9_.-]+/g, "_")}.json`;
  const outPath = path.isAbsolute(outRel) ? outRel : path.join(boardDir, outRel);
  await mkdir(path.dirname(outPath), { recursive: true });
  const prompt = `${body}

【本任务】
board: ${boardDir}
mpn: ${mpn}
field: ${field}
field_zh: ${job.field_zh || field}
unit: ${job.unit || ""}
只写 JSON 到：${outPath}
字段：mpn, field, value, unit, cite, confidence（high|low）。
禁止改其它文件。
`;
  const agentOpts = {
    model: resolveModel(model),
    local: { cwd: boardDir },
    outputs: [outPath],
  };

  try {
    const result = await promptWithTimeout(prompt, agentOpts, Math.min(JOB_TIMEOUT_MS, 120000));
    const wrote = await exists(outPath);
    return {
      job_id: job.job_id,
      ok: wrote && result.status !== "error" && result.status !== "cancelled",
      run: result.id,
      wrote,
      mpn,
      field,
    };
  } catch (err) {
    if (err instanceof AgentError) {
      return { job_id: job.job_id, ok: false, error: err.message, mpn, field };
    }
    return { job_id: job.job_id, ok: false, error: String(err?.message || err), mpn, field };
  }
}

async function runMpnVrJob(boardDir, job, model) {
  const out = path.join(boardDir, "review_out");
  const mpn = job.mpn;
  const promptPath = path.join(ROOT, "agent_review", "prompts", "mpn_vr.md");
  if (!(await exists(promptPath))) {
    return { job_id: job.job_id, ok: false, error: "missing mpn_vr.md" };
  }
  const body = await readFile(promptPath, "utf8");
  const outRel = job.out_path || `review_out/cache/mpn/${safeName(mpn)}/vr_agent.json`;
  const outPath = path.isAbsolute(outRel) ? outRel : path.join(boardDir, outRel);
  await mkdir(path.dirname(outPath), { recursive: true });
  const prompt = `${body}

【本任务】
board: ${boardDir}
mpn: ${mpn}
只写 JSON 到：${outPath}
字段：mpn, value_v, cite, confidence（high|low）。
禁止改其它文件。
`;
  const agentOpts = {
    model: resolveModel(model),
    local: { cwd: boardDir },
    outputs: [outPath],
  };

  try {
    const result = await promptWithTimeout(prompt, agentOpts, Math.min(JOB_TIMEOUT_MS, 120000));
    const wrote = await exists(outPath);
    return {
      job_id: job.job_id,
      ok: wrote && result.status !== "error" && result.status !== "cancelled",
      run: result.id,
      wrote,
      mpn,
    };
  } catch (err) {
    if (err instanceof AgentError) {
      return { job_id: job.job_id, ok: false, error: err.message, mpn };
    }
    return { job_id: job.job_id, ok: false, error: String(err?.message || err), mpn };
  }
}

async function runContextJob(boardDir, job, model) {
  const out = path.join(boardDir, "review_out");
  const kind = job.context_kind;
  const key = job.key;
  const promptFile = path.join(ROOT, "agent_review", "prompts", "context", `${kind}.md`);
  if (!(await exists(promptFile))) {
    return { job_id: job.job_id, ok: false, error: `missing context prompt ${kind}` };
  }
  const body = await readFile(promptFile, "utf8");
  const qPath = path.join(out, "context_store", "quarantine", `${safeName(job.job_id)}.json`);
  const prompt = `${body}

【任务】
kind=${kind} key=${key}
只写 quarantine 候选到：${qPath}
字段必须有 provenance；禁止 verdict_zh / 是 / 否。
材料根目录：${boardDir}
`;
  const agentOpts = {
    model: resolveModel(model),
    local: { cwd: boardDir },
    outputs: [qPath],
  };

  try {
    const result = await promptWithTimeout(prompt, agentOpts);
    return {
      job_id: job.job_id,
      ok: result.status !== "error" && result.status !== "cancelled",
      run: result.id,
    };
  } catch (err) {
    if (err instanceof AgentError) {
      return { job_id: job.job_id, ok: false, error: err.message };
    }
    return { job_id: job.job_id, ok: false, error: String(err?.message || err) };
  }
}

async function main() {
  const boardRel = argValue("--board");
  if (!boardRel) {
    console.error(
      "Usage: node orchestrator.mjs --board <boardDir> --mode judge|critique|context|path_draft|mpn_vr|mpn_param"
    );
    process.exit(1);
  }
  const mode = argValue("--mode") || "judge";
  const concurrency = Number(argValue("--concurrency") || DEFAULT_CONCURRENCY);
  const model = argValue("--model") || "auto";
  const resume = hasFlag("--resume");

  const boardDir = path.isAbsolute(boardRel) ? boardRel : path.join(ROOT, boardRel);
  const out = path.join(boardDir, "review_out");
  await ensureAuth();

  if (mode === "mpn_param") {
    const jobsDoc = await readJson(path.join(out, "mpn_param_jobs.json"));
    let jobs = jobsDoc.jobs || [];
    if (resume) {
      const pending = [];
      for (const j of jobs) {
        const rel = j.out_path || "";
        const p = path.isAbsolute(rel) ? rel : path.join(boardDir, rel);
        if (!(await exists(p))) pending.push(j);
      }
      jobs = pending;
    }
    console.log(`MPN_PARAM_JOBS\t${jobs.length}\tconcurrency=${concurrency}\tmodel=${model}`);
    const results = await mapPool(jobs, Math.min(concurrency, 4), (j) =>
      runMpnParamJob(boardDir, j, model)
    );
    await writeJson(path.join(out, "sdk_mpn_param_result.json"), { results });
    const bad = results.filter((r) => r && !r.ok).length;
    console.log(`OK\tmpn_param codex done bad=${bad}`);
    process.exit(bad ? 2 : 0);
  }

  if (mode === "mpn_vr") {
    const jobsDoc = await readJson(path.join(out, "mpn_vr_jobs.json"));
    let jobs = jobsDoc.jobs || [];
    if (resume) {
      const pending = [];
      for (const j of jobs) {
        const rel = j.out_path || "";
        const p = path.isAbsolute(rel) ? rel : path.join(boardDir, rel);
        if (!(await exists(p))) pending.push(j);
      }
      jobs = pending;
    }
    console.log(`MPN_VR_JOBS\t${jobs.length}\tconcurrency=${concurrency}\tmodel=${model}`);
    const results = await mapPool(jobs, Math.min(concurrency, 4), (j) =>
      runMpnVrJob(boardDir, j, model)
    );
    await writeJson(path.join(out, "sdk_mpn_vr_result.json"), { results });
    const bad = results.filter((r) => r && !r.ok).length;
    console.log(`OK\tmpn_vr codex done bad=${bad}`);
    process.exit(bad ? 2 : 0);
  }

  if (mode === "path_draft") {
    const jobsDoc = await readJson(path.join(out, "board_paths", "path_draft_jobs.json"));
    let jobs = jobsDoc.jobs || [];
    if (resume) {
      const pending = [];
      for (const j of jobs) {
        const p = path.join(out, "board_paths", "draft", `${j.domain_id}.json`);
        if (!(await exists(p))) pending.push(j);
      }
      jobs = pending;
    }
    console.log(`PATH_DRAFT_JOBS\t${jobs.length}\tconcurrency=${concurrency}\tmodel=${model}`);
    const results = await mapPool(jobs, Math.min(concurrency, 4), (j) =>
      runPathDraftJob(boardDir, j, model)
    );
    await writeJson(path.join(out, "board_paths", "sdk_path_draft_result.json"), { results });
    const bad = results.filter((r) => r && !r.ok).length;
    console.log(`OK\tpath_draft codex done bad=${bad}`);
    process.exit(bad ? 2 : 0);
  }

  if (mode === "judge" || mode === "critique") {
    const jobsDoc = await readJson(path.join(out, "judge_jobs.json"));
    let jobs = (jobsDoc.jobs || []).filter((j) => !j.empty_rule);
    const label = mode === "critique" ? "CRITIQUE_JOBS" : "JUDGE_JOBS";
    console.log(`${label}\t${jobs.length}\tconcurrency=${concurrency}\tmodel=${model}`);
    const {prepareMaterials,disposeMaterials}=await import('./local_workspace.mjs');
    const snapshot = jobs.length ? await prepareMaterials(boardDir) : null;
    if(snapshot)process.env.SCHEMATIC_MATERIAL_SNAPSHOT=snapshot;
    let results;
    const completed=[];let progressQueue=Promise.resolve();
    const progress=result=>{progressQueue=progressQueue.then(async()=>{
      completed.push(result);await writeJson(path.join(out,"sdk_judge_result.json"),{results:[...completed],mode,attempt_id:jobsDoc.attempt_id});
      const job=jobs.find(j=>j.job_id===result.job_id);
      if(job?.delivery_version){
        const r=spawnSync(process.env.SCHEMATIC_PYTHON||"python",["-m","agent_review.delivery_progress",boardDir,result.job_id],{cwd:ROOT,encoding:"utf8",windowsHide:true,env:{...process.env,PYTHONIOENCODING:"utf-8"}});
        console.log((r.stdout||r.stderr||"").slice(-2000));
      }
    });return progressQueue;};
    try { results = await mapPool(jobs, concurrency, (j) =>
      mode === "critique"
        ? runCritiqueJob(boardDir, j, model)
        : runJudgeJob(boardDir, j, model)
    ,progress); } finally { delete process.env.SCHEMATIC_MATERIAL_SNAPSHOT; if(snapshot)await disposeMaterials(snapshot); }
    await writeJson(path.join(out, "sdk_judge_result.json"), { results, mode,attempt_id:jobsDoc.attempt_id });
    const bad = results.filter((r) => r && !r.ok).length;
    console.log(`OK\t${mode} codex done bad=${bad}`);
    process.exit(bad ? 2 : 0);
  }

  if (mode === "context") {
    const jobsDoc = await readJson(path.join(out, "context_jobs.json"));
    const jobs = jobsDoc.jobs || [];
    console.log(`CONTEXT_JOBS\t${jobs.length}\tconcurrency=${concurrency}`);
    const results = await mapPool(jobs, concurrency, (j) => runContextJob(boardDir, j, model));
    await writeJson(path.join(out, "sdk_context_result.json"), { results });
    process.exit(0);
  }

  console.error(`unknown mode ${mode}`);
  process.exit(1);
}

main().catch((e) => {
  console.error("FAIL\t", e);
  process.exit(1);
});
