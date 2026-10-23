# 统一评审契约 review.v1

本任务由工作台 contract_task 提供检查对象和不可变判据。只读工作台、密封材料、网表、原图与手册。不得读取历史报告、文档样例、旧评审结论或程序应力答案。不要用预设位号、拓扑或数字替代材料分析。

本契约取代旧卡字段要求。先完成分析、自检，再按同目录 `<object>.schema.json` 落卡。输出只含 schema 定义字段，不输出旧 rule_id/object/electrical/states/summary/四格，也不输出控制器 _meta。schema_version、target、criterion 必须逐字复制 contract_task 对应字段；fixed_evidence 原样登记到 evidence，fixed_inputs 原样登记到 inputs。编排器另外验证它们未被修改。

## 结果结构

- scope 写要求范围、已分析范围和覆盖程度。必需状态未算完写 gap，不得列成豁免。
- inputs 登记原始事实和明确来源的模型参数；未知值用 null。计算结果不冒充材料事实。单位：W、V、A、ohm、1、degC；模型数值使用 SI 单位。
- premises 作为关键假设的结构化记录，区分 supported、model_assumption、unresolved，保留证据引用及工况 premise_refs 供校验和追溯；不再生成第二块业务前提文案。
- analysis_contract 固定 analysis.v1；analysis_steps 至多三条，每条为 `{text,premise_refs}`，不再输出 reasoning_steps。text 将关键前提与具体推导融合为一句，premise_refs 引用 premises 中的 ID，display.premise_refs 中的每个假设必须在分析中有引用；未确认假设须用“若、尚未确认、条件”等限定，模型假设须表明按模型推导。写“当前对象计算前已确认的具体电路事实，以及由此得到的推导关系”，不是评审人员的工作流程。必须结合材料中的当前对象、实际关联位号或网名，说明具体串并联/导通关系、哪个状态改变哪个节点、为什么该变化使对象应力更重或更轻，以及哪些支路需要计入。每条都应有当前电路的具体指代，整体须明确提到检查对象及至少一个关联器件。不得只写“核对连接、建立方程、比较极值、核对判据”等适用于任意对象的步骤；若移除位号后仍几乎适用于所有电路，重写为该对象的具体事实与因果链。不要写电压、电流、功耗、限额的具体数值或计算结果，也不提前复述通过/不通过结论；位号与网名中的数字不受此限制。具体数据放 inputs、calculations、metrics 和 decision。完整支路审计保留在分析稿。
- scenarios.title 使用易懂的具体工况名称，直接说明哪个器件或接口处于什么状态；证据等级由状态标签表达，不要在标题堆叠“条件候选”等术语。
- scenarios 每项填写 route_summary：用一句话说明供电经哪些关键器件到哪个负载或吸流端，以及决定通路的器件/接口状态；未证实条件须明确限定。不写自造脚号、不重复极值结论，不能把并联支路串成一条。
- scenarios 使用稳定字符串 id；各项有 metrics、calculations、适用性和 premise_refs。字段间用 ID 引用，不用数组索引。计算结果填写 value/unit；metrics.calculation_ref 必须引用同工况同单位同结果的计算。step_refs 只引用本工况前序步骤，不能循环。
- 定量工况门禁：applicability.status 为 supported 或 conditional 时，必须已经求解 current、voltage_span、power 且填入同一工况的非空数值；尚未求出所需指标的必需工况使用 unmapped 并作为 unranked 保留，不得把 supported 当作“现实中存在但数值未解”。未知值仍保持 null，不得为满足门禁估数或删除工况。
- extreme 显式指定一个主展示状态，comparisons 恰好覆盖所有状态一次。主状态为 primary，其他为 comparison、decision_boundary、excluded 或 unranked。按同一范围与可比模型比较，不直接给不可比数字取最大。
- decision 只有一项正式 verdict；状态不写正式是/否。支撑判定的状态与主展示状态可以不同，但必须论证适用性、覆盖和证据强度。
- gaps 的 reason 说明缺口为何影响极值、精度或判定。decision_changing 必须有材料支持的 countercase；只有缺资料的口头说法不够。
- display.input_refs 最多四项，premise_refs 最多三项，选择真正支撑主工况计算、需在分析中融合说明的关键假设；页面不再自动截取前三条公式。
- evidence 登记真实材料路径、定位、可支持的论断及限定条件。文件路径相对板目录；分析稿不是额定和连接的原始证据。不能因文件存在就假设支持某个保证值。
- 面向业务的标题、分析思路、结论、比较理由和图注，必须使用材料已有网名、位号或明确的中文节点描述。分析中为了列式临时定义的单字母节点别名，应在这些字段中还原，不能让业务读者再猜它代表哪个实际节点；内部 calculation.id 不受此限制。
- actions 关联具体工况与证据，说明整改或补证。未证实候选不能当作保证选型依据；较轻边界也不能当成最终选型所需最大值。

## 原图通路

只有 extreme.primary_scenario_ref 指向的状态提供 route，其余 route=null。跨页为同一个工况的多个 pages。

route.pages 给真实 pdf_page、branches、through_body、markers、notes。branch.path 按电流方向列真实网名和位号/脚号；并联分别列出。current_ref 引用本状态电流计算，分流未解则 null，不猜均分。direction_status 未知则不画箭头。

through_body 描述封装内 from/to 脚的实际转接，不省略多脚二极管极性。markers 使用 value_ref 引用 input 或本状态 calculation，不在 label 再写一套数字；kind 为 voltage/span/note。业务端压写“V(对象) 两端电压差”，真实脚号只作内部连接与定位。

仅高亮该状态导通支路，不高亮控制线、截止支路或其他状态线路。外部回流没有材料定位就注明未知，不能画成板内直通地。不给坐标，不生成 SVG/图片/base64，程序负责沿实际铜线绘图。

## 停手与校验

依据所给 Schema 检查类型与引用，核对公式代入和各处数值。程序还会进行身份、来源、物理量一致性与判定校验。不能为凑字段新增假状态、假来源或全局极值。材料未闭合的边界记录清楚即可，不反复读同一手册直到所有缺口清零。

多页或周期相位图：route.pages 可写 label（明确页/相位名称）和 route_summary（本页一句中文通路说明）；同一周期的充放电相位仍属于一个 scenario，不重复创建两个最坏工况。几何定位诊断不得作为业务图注；周期平均与相位瞬时量分别标明，缺方向不画箭头。
