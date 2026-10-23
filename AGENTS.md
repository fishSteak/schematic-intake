# 开发约定

- 本目录是原理图自动化评审的唯一开发仓。不要修改或依赖同级 `schematic-review`。
- 历史资料在 `archive/schematic-review/`；以现行代码、`rules/` 配置和 `docs/迁移到单仓.md` 为准。
- 程序检查器在 `kernel/checkers/`；求解模块在 `solve_prog/`；报告前端在 `report_ui/`。
- 禁止手改正式 `boards/*/review_out/agent_jobs/`、`context_store/` 或 `verdicts.json` 来通过门禁。
- 材料事实、卡契约、推理、展示和回归各自分层；不要将某板位号或拓扑特判塞入通用 Prompt。
- 用户反馈的问题须先定位评审流水线层级，判断并修复可复现的通用原因，补充适当回归验证；不能只修某份生成报告或写入预期答案。报告调整应通过正常生成链路完成。
- 判定契约或相关推理规则变化须使旧断点结果失效。正常设计核算、精确最大值和参数保证范围分开；已有独立工程超限依据不能被无关的最坏候选缺口撤销。
- 改迁移模块后运行 `py scripts/verify_standalone.py`。更新基线必须说明行为变更原因。
- 本分支目标为应用直接调用 LLM API：由应用管理模型配置、上下文、工具执行、校验和续跑，不依赖 Codex CLI 或其登录态。`agent_review/codex_agent.mjs` 等旧 worker 仅为历史迁移记录；禁止重新接入工作台或 Python 活跃执行链路。禁止恢复 Cursor SDK 依赖，模型以应用配置为准。
- 运行逻辑测试：`py -m unittest discover -s tests -v`；正式深审前可用 `py scripts/review_preflight.py --board boards/<板>` 检查准备状态。

- 详情统一使用“分析思路”：条件与具体因果推导只生成一份业务文案；analysis_steps 引用结构化关键假设。保留假设状态、证据和源字段用于校验追溯，未确认条件不可写成确定事实；不恢复独立“成立前提 / 判断思路”区块，不用前端拼接代替模型整合。
