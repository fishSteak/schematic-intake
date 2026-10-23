# 代码地图

## 顶层目录

| 目录 | 职责与定位 |
|---|---|
| [kernel](../../kernel) | 材料接入、网表与BOM适配、规格书检索抽取、原理图索引、板级知识图、确定性检查与报告适配 |
| [agent_review](../../agent_review) | 对象枚举、事实工作台、Agent执行、证据循环、契约、恢复、绘图、汇总、HTTP服务 |
| [solve_prog](../../solve_prog) | 已有电路连接、MNA、行为求解和SPICE相关能力；不能把程序试算直接当Agent结论 |
| [rules](../../rules) | 检查项范围、完整条款、源卡Schema、契约开关与执行轮次配置 |
| [report_ui](../../report_ui) | Vue/Vite报告前端，源码在src，发布服务使用dist |
| [vendor/expert_html](../../vendor/expert_html) | 现行静态HTML构建器及PDF.js资源，仍承担原expert_review.html生成 |
| [scripts](../../scripts) | 范围交付、定向修复、校验、独立运行验证等操作工具；并非每个脚本都是通用正式入口 |
| [packages](../../packages) | 随仓包与辅助工具，例如datasheet-hunt；不等于顶层评审编排器 |
| [sdk](../../sdk) | 旧入口与归档残留；orchestrate.mjs已明确废弃并退出，现行调用位于agent_review |
| [boards](../../boards) | 每板材料配置、review_out和history；是运行数据，不能当公共算法源 |
| [archive](../../archive) | 历史资料，不作为现行运行依赖 |
| [tests](../../tests) | 逻辑、契约、来源、绘图与兼容回归；通过不等于电子结论已验收 |
| [output](../../output) | 导出的提示词、流程图等交付附件；不是事实输入或生产源码 |

## 按职责找文件

| 修改意图 | 主要文件 |
|---|---|
| 材料配置或解析 | kernel/steps/s0_materials.py，kernel/adapters/，kernel/steps/s1_graph.py |
| 规格书、额定参数、页图证据 | kernel/steps/s2_datasheet.py，s3_extract.py；agent_review/verified_sources.py，page_evidence.py |
| 对象漏审或错误不适用 | agent_review/enumerate.py，applicability.py，rule_modes.py |
| 传给模型的材料或脚网缺失 | agent_review/workbench.py，criteria.py，design_pin_labels.py，stages/context_build.py |
| 执行并发、会话、运行记录 | agent_review/stages/judge.py，orchestrator.mjs，codex_agent.mjs |
| 交付草稿与自检轮次 | agent_review/delivery_worker.mjs，execution_policy.py，rules/review_execution.json |
| 工作目录与附件恢复 | agent_review/local_workspace.mjs，reuse_inputs.mjs，repair_cards.py |
| 源卡与判定一致性 | agent_review/review_contract.py，decision_contract.py，main_analysis.py，validate_job.py |
| 业务详情内容与格式 | agent_review/detail_view.py，detail_prose.py，kernel/report_workbench.py |
| 连线定位与图形排版 | agent_review/schematic_routes.py，stress_figures.py，stress_path_render.py |
| 原报告与Vue展示 | vendor/expert_html/build_expert_html.py，report_ui/src/components/ReviewContractDetail.vue |
| 板级批次覆盖与发布 | agent_review/selection_coverage.py，scripts/finish_selection_delivery.py |

以上路径的逐文件符号和导入声明见 [code-index.json](code-index.json)。导入声明仅反映静态语句，不包含全部动态执行关系。
