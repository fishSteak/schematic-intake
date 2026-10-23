# 功耗工程判定契约 v2（分析、自检、落卡共用）

评审判据是要求工作范围内的最大功耗是否满足降额限额，必须搜索并比较极端工况，不以“正常情况”替代最坏情况。若任何有材料支持、适用于要求工作范围的工况已经超限，则最大值必然不满足限额；工程模型的这个推论应标明模型范围，不能冒称全温实物保证下界。精确最大值尚未知不妨碍该极值判否。只有未经证实的任意假设候选超限，仍不足以单独判否。

内部 normal_design_model 表示材料支持的适用工程模型，不代表“只评普通工况”。业务文案围绕极值要求说明：已找到适用超限工况，所以最大功耗不符合；主图仍保留最重候选及其成立条件。不得把较轻见证称作最坏点。精确最坏值与全温/全容差保证另列证据边界。

1. 先独立查找正常设计的判定依据，再比较最坏通路。已经有正常设计依据超限时，外部更重候选的吸流能力未知只影响最大值，不撤销判否依据。
2. 正常设计依据可以是材料支持的标称工作模型、可达稳态、适用范围内的下界或所有控制状态共有的边界。应说明工作范围、模型适用性、节点平衡及遗漏支路的影响方向。典型参数可用于有出处的正常设计核算，但不得冒充全温保证。任意假设的外部强制电位、未验证的驱动能力、故障/缺装等不能直接当正常设计依据。
3. 未知控制状态、外部负载能力或占空比存在时，检查是否能用分段/单调性/共同边界绕开；不能仅因一个指定状态未证明永久保持，就丢弃所有状态都成立的超限推理。
4. 把电流/端压/功耗限额反推为阈值。说明未知参数需要变化到什么程度才可能改变满足/不满足；只有有材料支持且物理自洽的反向情形才是判定卡点。缺厂家版本时应区分已知型号的功能/标称参数与缺少的保证范围，不把正常器件擅自假定为开路、失效或另一规格。
5. 每个缺口必须归类：只影响最大值、只影响精度/置信度、可能改变判定。若已有独立正常设计超限证据，后两类外部候选的缺口不能覆盖它。只有确实可能改变当前判定且没有独立充分证据，才用待确认。

## 落卡结构（分析轮只写上述推理，不写 JSON）

必须提供 `decision_audit`：
- `version: 2`
- `normal_design_search`：独立于更重外部候选，如何寻找正常设计证据、控制变化是否可能减轻应力。
- `state_reviews`：每个 states 状态恰好一条 `{state_index, admissibility, reason}`；admissibility 为 `normal_design|conditional|unmapped|out_of_scope`。正常设计模型不得仅因数值未获全温保证而标 conditional；有真正未证实的外部/控制前提才标 conditional。
- `gaps`：每项 `{issue, effect, reason}`；effect 为 `maximum_only|precision_only|can_change_verdict`。can_change_verdict 必须另给 `countercase: {description, evidence, decision_effect}`，说明材料支持的哪一种正常情形可能改变判定。不得只写“资料缺失”。没有缺口则空数组。

正常设计核算已足以判否时，`decision_basis.kind=normal_design_model`，另给 `state_index/value_w/limit_w/evidence/conditions/model_scope="nominal_design"/applicability/coverage_argument`。applicability 说明模型为何适用于正常设计；coverage_argument 说明足以判否的工作点或跨控制状态边界，以及未知条件为何不能撤销此依据。该状态 `stress_kind=modeled`、status 可为 modeled，不是 conditional；值是工程模型结果，不是严格实物下界。

已有严格证据仍可用 reachable_state/proven_lower_bound；通过仍须 envelope_upper_bound 覆盖要求范围。全局最大值未确认则 P_max=null，与判否相容。启用 basis-main.v1 时，主图、主分析和计算必须共同引用 decision_basis，不允许用未证实条件候选替代；具体依据画具体工况，上界依据画跨工况供能/储能/耗散关系图。未启用的历史契约仍按旧卡兼容读取。

只用于独立判否、并不主张为已确认最大点的模型边界可用 `role=witness`；不把它强行计入 confirmed_state_index 的最坏点排名。公式可用“对象耗散 = …W”等中文表达，仍须有数值代入和材料来源。

校验器将拒绝：正常设计模型已超限却判待确认；没有可改变判定的有据反向情形却判待确认；用未经支持的条件候选直接判否；以全局最大值未知撤销正常设计超限。禁止写入任何特定位号、板名、预制拓扑或预期答案。

如果必要的充分包络证明确实未完成，不得编造可达超限反例。工作台 available_main_analysis_contract 允许使用 main_analysis，严格读取同目录 .main.schema.json：kind=incomplete_proof、proof_status=incomplete，与 decision_basis.kind=insufficient 及 state_index 一致。对应 can_change_verdict 缺口使用 proof_gap:{failed_claim,reason,evidence,decision_effect}，具体记录未成立的必要命题、已完成部分、原始出处及其判定影响；主图归该未完成证明持有。该方式适用于所有具有实际证明缺口的功耗对象，不取决于位号。
