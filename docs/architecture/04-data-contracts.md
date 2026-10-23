# 数据与JSON契约

## 产物按板隔离

| 位置 | 内容 | 写入责任 |
|---|---|---|
| materials.json及其指向文件 | 原始材料配置与设计事实 | 收料与材料生产流程 |
| review_out/design_graph.json，board_kg.json | 连接关系和板级事实视图 | kernel前置阶段 |
| review_out/context_store/ | 密封事实上下文 | 上下文构建器；Judge不能修改 |
| review_out/agent_jobs/规则/对象.context.json | 对象工作台 | 控制器与workbench |
| 同目录 .schema.json/.main.schema.json/.research.schema.json | 对应输出契约 | 控制器 |
| 对象.analysis.md | 完整推导、比较、计算与范围 | 模型 |
| 对象.json | 正式源卡 | 模型产出，经正常校验补运行信息 |
| 对象.research.json | 原文事实、冲突、请求和出处 | 模型查证产物，经校验 |
| 对象.run.jsonl、delivery_progress/ | 真实阶段和对象运行信息 | 执行器与控制器 |
| research_assets/、page_evidence/ | 新取得资料及带哈希页图 | 正常查证/页图生产流程 |
| stress_paths/规则/对象/工况/ | 逐页图、flow.png、meta.json | 绘图器 |
| verdicts.json、findings.json、evidence/ | 汇总判定、问题和对象详情包 | 聚合与报告构建 |
| report_data.json | 前端数据与统一review_details | 报告构建 |
| expert_review.html、engineer_review.html | 静态报告 | 正常报告生产链 |
| history/ | 旧运行、修复前产物与执行快照 | 编排器 |

这里的review_out位置均相对于板目录。禁止手改正式源卡、context_store或verdicts来通过门禁。

## 并存的契约

| 契约 | 主要职责 | 当前状态 |
|---|---|---|
| 旧对象卡与states/electrical | 历史判定、数值及工况 | 仍在使用，经适配显示 |
| review.v1 | 新源卡结构、target/criterion/inputs/scenarios/decision等 | 通过配置接入，未全量迁移 |
| analysis.v1 | analysis_steps与key_assumptions关联 | 统一分析思路语义 |
| basis-main.v1 | decision_basis与主分析、图、计算绑定 | 已接入配置对象，含未完成证明表达 |
| evidence.v1 | facts、冲突、请求与原始证据引用 | 查证记录契约 |
| detail.v2 | 摘要、材料与思路、判定、主分析、比较、证据、资料、图幅 | 统一展示层，不代表所有源卡同构 |
| remediation.v1 | 外层可执行整改建议及验收条件 | 按开关启用，不能假设所有对象都接入 |

Schema入口：[schemas](../../agent_review/schemas) 与 [源卡Schema](../../rules/object_card_schema.json)。

## 引用与状态

analysis_steps引用key_assumptions，假设状态区分supported、model_assumption、unresolved。主分析引用decision_basis及对应state；图中正式数量应引用同一依据，不能另写更容易作图的数值。上界不等于可达最大值，null不等于零。

研究请求区分business_fact、public_material、reasoning、execution，只有真实项目事实进入业务补料。精确最大值未知、参数精度不足和足以改变判定的缺口须分别处理。
