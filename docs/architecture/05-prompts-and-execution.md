# 提示词与模型执行

## 当前delivery首轮拼接

[delivery_worker.mjs](../../agent_review/delivery_worker.mjs) 先判断是否review.v1，再读取对应公共模板与检查项模板。以旧卡P11-A01为例，顺序为：

1. prompts/_common_judge.md：字段、来源、查证、适用性和业务表达硬约束。
2. prompts/judge/P11-A01.analysis.md：电阻功耗分析要求。
3. prompts/judge/P11-A01.md：落卡字段、工况与主分析约定。
4. prompts/judge/_power_decision.md：功耗充分依据、缺口分类和判定审计。
5. prompts/delivery_review.md：本轮完整交付、图规范和边界要求。
6. 动态对象任务：规则、对象、工作台路径、Schema、三个输出文件及禁止修改范围。

review.v1改用review_v1.common.md及review_v1.power.md等对应模板；材料本身由工作台及引用文件提供，不把预期答案写进公共Prompt。

定向复核时，已有分析、卡和研究是待审草稿，新独立文档是推导线索。需要实际核查决定性原始出处，不能把旧运行或旧结论标为本轮新评审。

## 自检与定修

delivery首轮已经写三个完整产物；随后根据job.self_check和实际错误决定是否再调用同会话核查。还有字段/引用错误时再定修。普通历史分支仍保留分析、自检、落卡的组织。`rules/review_execution.json`中的two_turn_rules与delivery实际分支应分别理解，不能仅按名称断定每个入口固定几轮。

当前模板还保留“只写Markdown”“只写JSON”等旧要求，再由delivery尾部覆盖；自检模板也有旧输出限制。这个冲突已经记录为整理任务，尚未声称消除。

## CLI与隔离

[codex_agent.mjs](../../agent_review/codex_agent.mjs)通过本地codex exec发送指令，后续轮次resume同一会话，按执行配置启用公开搜索。模型实际在本地临时工作目录操作，LocalWorkspace复制材料并仅回收声明的输出和允许的研究附件；不会直接授权Judge修改全板事实库。

Node由kernel/runtime.py按SCHEMATIC_NODE、PATH、CODEX_MCP_NODE_PATH查找；CLI由SCHEMATIC_CODEX或PATH定位。鉴权使用该CLI的现有登录态。本仓现行运行不使用Cursor SDK；node_modules或历史文档中的旧依赖不代表它仍是活动后端。

## 模型和并发

通用Python与Node代码默认并发目前均为4。模型可由AGENT_JUDGE_MODEL、入口参数及CLI配置决定，不能把某次运行的模型当仓库通用固定模型。代表性交付脚本明确设置gpt-6.1-sol、并发2；新入口应显式传入所需配置。

## 完成口径

运行签名与阶段快照由execution_policy管理。自检是同会话核查；当前独立子Agent由外部协作按需启动，未内建为每对象独立裁决。模型已经写出proof_gap不等于其必要性已获第二方确认，格式完成不能作为推导充分的替代。
