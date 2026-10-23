/** Live, isolated three-turn smoke test; no formal board cards are changed. */
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { Agent, Codex } from "../agent_review/codex_agent.mjs";

const cwd = await mkdtemp(path.join(os.tmpdir(), "schematic-codex-"));
const agent = await Agent.create({ local: { cwd } });
const results = [];
try {
  if ((await Codex.auth.status()).status !== "logged-in") throw new Error("Codex login required");
  const prompts = [
    "Create analysis.txt in this workspace with the calculation 24*24/100000 = 0.00576 W. Do not modify any other file. Reply ANALYZED.",
    "Read analysis.txt. Verify the units and conversion to milliwatts. Append one line stating 5.76 mW. Modify only analysis.txt. Reply CHECKED.",
    'Read the checked analysis.txt. Create result.json containing {"power_mw":5.76,"checked":true}. Do not change other files. Reply DELIVERED.',
  ];
  for (const prompt of prompts) {
    const run = await agent.send(prompt);
    const timer = setTimeout(() => run.cancel(), 90000);
    const result = await run.wait();
    clearTimeout(timer);
    results.push(result);
    if (result.status !== "completed") throw new Error(JSON.stringify(result));
  }
  if (new Set(results.map(r => r.id)).size !== 1) throw new Error("Session was not resumed");
  const card = JSON.parse(await readFile(path.join(cwd, "result.json"), "utf8"));
  if (card.power_mw !== 5.76 || !card.checked) throw new Error("Invalid delivered JSON");
  const report = { passed: true, backend: "codex-cli", turns: 3, same_session: true, file_delivery: true };
  await writeFile(new URL("../migration_baseline/codex_smoke.json", import.meta.url), JSON.stringify(report, null, 2) + "\n");
  console.log(JSON.stringify(report));
} finally {
  await agent[Symbol.asyncDispose]();
  // mkdtemp created this exact task-owned directory; remove only that directory.
  if (path.dirname(cwd) !== os.tmpdir() || !path.basename(cwd).startsWith("schematic-codex-")) throw new Error("Unexpected temporary path");
  await rm(cwd, { recursive: true, force: true });
}
