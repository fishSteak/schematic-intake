# 运行与排障

所有命令在仓库根执行，示例板为boards/0918全量。真实评审命令会调用模型并写入板级结果，应先核对范围；文档校验和索引刷新不会启动评审。

## 依赖与检查

Python依赖见requirements.txt；Node最低版本按package.json为24.0.0（代码使用 await using 语法）。真实Agent需要本地Codex CLI及可用登录态。Node路径可设置SCHEMATIC_NODE，CLI路径可设置SCHEMATIC_CODEX。前端构建在report_ui中执行npm run build。

```powershell
codex login status
py scripts/review_preflight.py --board boards/0918全量
```

预检只检查准备条件，不能代替电子判定。

## 正式执行入口

```powershell
# 通用全链，显式并发2
py -m agent_review.run --board boards/0918全量 --concurrency 2
# 同一范围续跑，仍需匹配签名
py -m agent_review.run --board boards/0918全量 --resume --concurrency 2
# 指定电阻复核；此入口有历史兼容行为，先查--help
py -m agent_review.run_p11a01_objects --board boards/0918全量 --objects R38 --concurrency 2
```

本轮delivery.v1使用已有selection_delivery配置与followups队列：

```powershell
# 仅处理已排入队列的目标；空队列不会自动变成全板重评
py scripts/finish_selection_delivery.py --board boards/0918全量 --reconcile
# 按真实运行与产物恢复校验；不是新增模型评审
py scripts/finish_selection_delivery.py --board boards/0918全量 --revalidate
# 发布冻结选择范围，执行源审计、媒体与展示检查
py scripts/finish_selection_delivery.py --board boards/0918全量 --publish
```

修改选择范围须通过相应配置和生产流程，不手改正式卡以改变结论；上述操作不能盲目作为通用重置命令。

## 仅重建展示与访问

```powershell
py -m agent_review.publish_details --board boards/0918全量
py -m agent_review.serve --board boards/0918全量 --host 127.0.0.1 --port 8765 --no-browser
```

publish_details不重跑电气判定，但缺少合规展示文案时可能调用模型补写文案，并非保证零模型调用。HTTP服务提供Vue根入口、expert_review.html、报告数据、PDF.js和共享批注API；服务默认host为0.0.0.0，示例显式限定本机。完整PDF交互需HTTP访问。

## 发布校验

```powershell
py scripts/check_selection_delivery.py --board boards/0918全量 --published
py scripts/check_detail_presentation.py --board boards/0918全量 --published
py scripts/check_delivery_geometry.py --board boards/0918全量
```

前两项适用于对应交付数据。迁移模块变更另按AGENTS运行verify_standalone；按修改范围选择必要测试，不因仅改文档重跑全板模型。

## 排障顺序

| 表象 | 优先核对 |
|---|---|
| 对象缺失或错误不适用 | 枚举范围、原始用途、applicability和本轮对象清单 |
| 参数或引脚错 | 原始材料、workbench、设计脚号/脚名、准确型号与测试条件 |
| 证明待完成 | analysis、decision_basis、proof_checks；区分推导未完成和真实缺业务事实 |
| 运行成功但文件无效 | run日志、schema/evidence校验、invalid/failed标记及progress |
| 重复读材料或续跑错 | execution_hash、模型/提示词变化、工作台和阶段快照附件 |
| 图片缺失、漏线或反向 | figure规范、图类型、meta中的定位/方向告警与原图 |
| 图或构建内存失败 | 逐阶段运行、逐页图与缩略总览，避免模型、前端构建、大图并行占用 |
| 页面仍旧内容 | 发布结果、实际HTTP根目录、报告数据、浏览器刷新；不先手改HTML |
