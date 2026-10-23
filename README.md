# Codex 自动化运行（2026-10-22 更新）

本仓是原理图自动化评审的唯一开发入口。Agent 后端已切换为本机 **Codex CLI**，不再导入或调用 Cursor SDK，也不需要 Cursor API 密钥。

## 环境与鉴权

- Python：先安装 `requirements.txt` 中的依赖，推荐使用下方的项目虚拟环境。
- Node.js：要求 >=24.0.0（见根目录 `package.json`）。运行时先读 `SCHEMATIC_NODE`，其次查 PATH，再尝试 Codex 提供的 `CODEX_MCP_NODE_PATH`；前端构建还需要可用的 npm。
- Codex：默认调用 PATH 中的 `codex`，可用 `SCHEMATIC_CODEX` 指定可执行文件。
- 鉴权：新环境执行 `codex login status`，未登录时按 CLI 提示完成登录。不复制登录凭据、不读取凭据内容、不依赖 Cursor 登录态。
- 模型默认继承用户的 Codex 配置；可用 `AGENT_JUDGE_MODEL` 显式指定。未指定时不再发送 Cursor 模型名。
- `report_ui/dist` 是本地构建产物，不进入 Git；首次 clone 后必须构建前端。根目录与 `sdk/` 的 `dependencies` 均为空，真实 Agent 通过本机 Codex CLI 执行，不需要安装 Cursor/OpenAI npm SDK。

## 首次 clone 后初始化

在仓库根目录执行以下 Windows PowerShell 命令。直接使用虚拟环境中的 Python，无须运行 `Activate.ps1`：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm --prefix report_ui ci --ignore-scripts
npm --prefix report_ui run build
codex login status
```

依赖版本按 Python 3.12 / Windows x64 验证（见 `requirements.txt`）；其他 Python 版本需另行验证。

下文运行和验证命令中的 `py` 表示已安装项目依赖的 Python。若按上面的 `.venv` 初始化，请将命令开头的 `py` 替换为 `.\.venv\Scripts\python.exe`；不需要激活虚拟环境。

## Git 内容范围

- 保留业务源码、规则、测试、依赖清单与锁文件，以及开发和运行所需文档。
- `.venv/`、`node_modules/`、`report_ui/dist/`、Python 缓存和大部分 `boards/*/review_out/` 已忽略；不需为 push 删除这些本地文件。
- 保留 `boards/<板>/*.json` 配置及 `boards/<板>/review_out/board_kg.json`：前者描述材料和评审配置，后者目前仍用于离线验证与既有入口，不能将整个 `review_out` 的例外直接去掉。
- 保留 `materials/` 原始输入及 `migration_baseline/` 迁移/回归基准，使新 clone 能准备材料并执行基准验证。生成报告、资料查询缓存和板级历史不等同于这些受控输入。

## 运行

在本 README 所在的仓库根目录执行：

```powershell
# 材料准备，包含查参；不运行对象判定
py -m kernel.run --board boards/0918全量

# 用当前材料重建密封事实，不运行对象判定
py -m agent_review.run --board boards/0918全量 --only-stage context_build --rebuild-context

# 全量评审；续跑仅复用契约有效且上下文仍一致的对象卡
py -m agent_review.run --board boards/0918全量 --resume

# 报告
py -m agent_review.serve --board boards/0918全量
```

默认范围来自 `rules/product_center_15.json`（当前 30 项），不受上次缓存的局部运行范围影响。只有显式传 `--rule-ids` 才执行子集。当前 16 项走 Agent，其他项走程序。

深审保留同一会话三轮：分析 → 自检 → 落卡。传输使用 `codex exec --json`，后续轮次按明确 session ID 调用 `codex exec resume`；每个对象拥有独立会话。CLI 使用 workspace-write 沙箱和非交互执行。

`--enrich-context` 可选启用 Codex 补充上下文；旧名 `--use-sdk-context` 仅保留为兼容别名。正常上下文重建使用确定性事实种子，不需要模型补全。

断点续跑拒绝复用：缺卡、坏 JSON、契约无效、失败标记、离线演练卡、中断占位卡、缺哈希或哈希不匹配。程序项照常重算。正式卡不得手改来通过门禁。

## 验证

先完成上述依赖安装和前端构建，再执行；`verify_standalone.py` 需要本地 `report_ui/dist`，不会替你构建前端。

注意：当前 `--dry-run` 不是全链路离线保证。`verify_standalone.py` 的报告汇总阶段在缺少分析文案缓存时仍可能调用真实 Codex，使用网络及模型额度；仅需本地代码检查时，运行单元测试和前端构建即可。

```powershell
py -m unittest discover -s tests -v
# 完整流程验证：可能调用真实 Codex，先确认鉴权和额度
py scripts/verify_standalone.py
# 真实 Codex 三轮文件交付测试（临时目录，不触及正式板卡）
# 使用已配置的 Node 运行 scripts/codex_smoke.mjs
```

### 2026-10-20 迁移验证记录（历史）

以下是迁移时本机的验证记录，不代表新 clone 已安装依赖、已登录或已完成评审；本地结果与历史目录不会随代码完整推送，需在目标环境重新验证。

当时已验证 Codex 登录、真实响应、同会话三轮和 JSON 文件交付，并通过新执行逻辑测试及独立运行验证。`migration_baseline/codex_smoke.json` 保存当时的真实三轮测试结果。

当时 0918 板材料重建记录：94 个器件、70 个网络、44 份规格书命中、原理图位号覆盖 94/94；来源路径可访问，密封上下文 184/184。旧知识图、索引、上下文和门禁快照保存在当时的本地 `boards/0918全量/history/`。

该次验证没有启动全量对象深审。`automation_preflight.json` 仅表示准备状态，不代表整板评审完成；最终仍以目标环境中正常全量流程生成的 `agent_review_gate.json` 为准。部分料号参数可能仍需补证；查不到时保持缺口，不以猜值通过。

`sdk_*.json`、`use_sdk` 等少量内部字段名为了兼容旧报告与脚本暂时保留，实际执行后端均为 Codex CLI。历史迁移资料见 `docs/迁移到单仓.md`，归档旧文档见 `archive/`。

官方接口说明：https://developers.openai.com/codex/noninteractive
