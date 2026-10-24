# 告警中心 0.6.0

Vue 告警中心与独立 alerts 服务。主机/服务/证书/K3s 规则、处理记录、静默、通知策略、投递记录和统计使用现有 alerts 逻辑库。

## 数据和迁移

启动时在事务中取得 PostgreSQL advisory lock，将已有 `alert_snapshots` 的规则、事件和静默导入 `alert_center_rules/events/silences`，按 ID 保留原记录。新处理记录、策略、任务存入 `alert_center_notes/policies/deliveries`；`alert_center_meta` 保存迁移标记和评估状态。原快照与证书表保留。

导入事件标记为 `notification_eligible=false`，不补发历史消息。后续恢复后再次发生的故障属于新事件周期。迁移可重复执行，任务和状态在同一事务提交；网络发送在事务外执行。短事务通过 advisory lock 串行更新，不会因多 worker 覆盖旧状态。

个人规模下保留按行 JSONB 和短事务锁，避免引入额外队列服务。大量事件时，优先将事件搜索和任务领取下推 SQL，再按事件粒度锁定；当前读写会扫描本模块已保存记录，不用于高吞吐接入。

## 环境配置

| 配置名 | 用途 |
|---|---|
| `ALERTS_DATABASE_URL_FILE` | 已有专用 alerts 数据库连接文件 |
| `ALERTS_SYNC_ENABLED=false` | 正式使用模块自有数据；只有显式兼容导入才设 true |
| `MODULE_AUTH_REQUIRED=true` | 必须保留网页 JWT 鉴权 |
| `ALERTS_DELIVERY_ENABLED` | 默认 false；部署验收后启用 worker。页面显示投递暂停状态 |
| `ALERTS_CREDENTIAL_KEY_FILE` | 专属 Fernet 密钥文件，默认 `/run/secrets/alerts_credential_key`；禁止入库/日志。没有密钥仍可使用站内渠道 |
| `ALERTS_PUBLIC_URL` | 通知详情链接的工作台基础地址，例如 HTTPS 入口的 `/v3` 前缀 |
| `ALERTS_WEBHOOK_ALLOWED_HOSTS` | 逗号分隔的明确允许内网目的主机；默认只允许 HTTPS 公网目的地址，禁止重定向。短信模块域名需单独放行 |
| `HOST_INVENTORY_INTERNAL_URL/TOKEN_FILE` | 已有资产内部接口 |
| `HOST_MONITOR_INTERNAL_URL/TOKEN_FILE` | 已有主机指标内部接口 |
| `SERVICE_CATALOG_INTERNAL_URL/TOKEN_FILE` | 新服务目录 `/api/v1/internal/alert-targets`；沿用其专属内部读令牌 |
| `K3S_INTERNAL_URL/TOKEN_FILE` | 新 K3s 目标接口；沿用其专属内部读令牌 |
| `ELECTRICITY_INTERNAL_URL/TOKEN_FILE` | 新电费评估状态接口；沿用其专属内部读令牌 |
| `PVE_INTERNAL_URL/TOKEN_FILE` | 预留规范化 PVE/备份目标接口；当前仓库没有有效 PVE 模块实现，保持未配置 |
| `SMS_INTERNAL_URL` | 短信模块地址；该模块必须设置非空 `SMS_TOKEN`，策略令牌与之匹配 |

`*_INTERNAL_TOKEN_FILE` 是完整变量后缀，例如 `SERVICE_CATALOG_INTERNAL_TOKEN_FILE`，不得把 Token 值写入环境配置文档。数据源服务的 `OPS_MODULE_INTERNAL_TOKEN_FILE` 必须指向与调用方相同的专属令牌文件。不要将一个来源的令牌复用于其他来源。

新功能发布需要配套发布 alerts、service_catalog 的 main/storage、K3s 的 main/collector、电费 main 和短信 main。现网存在源码 ConfigMap 挂载，必须逐项对账并包含新文件，避免旧挂载覆盖新镜像；不能仅替换页面或 main.py。

## 规则和状态

- 故障状态：`pending/firing/recovered/cancelled`；处理状态：`unacked/acked/closed`。旧 `status=acked` 读取时仍兼容，确认操作不再改变故障事实。
- 当前事件跨越历史查询窗口仍显示；搜索与页数来自同一过滤结果，日期按北京时间计算。
- 持续时间、恢复稳定期、恢复阈值、重复提醒分别处理。观察期内恢复记为 cancelled；数据无效/过期保持 unknown，并重置连续计时。
- 静默只限制通知；有开始/结束、对象/规则和按周维护时段。跨午夜的维护时段归属于开始日。
- 归档规则保留历史，并将其活动事件标为规则归档结束。停用规则保留故障状态，暂停该规则通知。
- 证书以每张证书 ID 为身份，关联同主机也互不合并。现有主机磁盘源为汇总指标，不宣称已提供每个挂载点的数据。
- K3s 直接读取节点就绪、Pod 状态/等待原因、PVC 状态；容器重启使用进程内近 5 分钟已观测的累计增加量，首次采集和长时间断流后的首样本为未知，不把历史累计当作本次增加。
- 电费只读取电费模块完成的持久评估状态，业务判断与外部通知仍归电费模块。集中告警可记录站内通知，外部策略跳过这些事件，防止双发；切换通知所有权属于另一步明确迁移。

## 通知

站内、Webhook、PushPlus、短信。策略按来源/级别筛选，可选合并（每批最多8条）、等待时间、未确认后延迟、重复间隔、发送上限、免打扰和恢复通知。通过另一条延迟短信策略实现升级，仍受各自上限与免打扰约束。

新策略默认仅预览。已生成的预览任务不能因修改策略变成真实发送。确认停止后续催告；已恢复的事件跳过尚未发送的故障任务。恢复通知只发往先前接受过该事件故障通知的渠道。

任务 ID 是故障周期/策略/消息类型/提醒轮次的稳定幂等键；发送有租约和 claim ID。HTTP 429/5xx 最多四次有限退避，网络中断/超时或 worker 发送中断保留 uncertain，管理员核对平台后可手动重试。无法保证第三方副作用恰好一次。发送成功仅标平台接受，站内标已记录。

测试只使用本地接收器，不会向现有用户渠道发送消息。真实测试需明确目标和条数。

## 发布与回退

1. 重新记录生产基线，备份 alerts 库、必要密钥恢复材料、部署与源码挂载，并校验备份可读取。
2. 先在隔离 schema 验证候选，再停止旧评估写入，部署一致的新版本；不做双写。
3. 对账旧快照与分表的 ID/状态/时间；历史迁移不发送。检查真实网关、匿名401、登录、详情、服务数据及模块故障隔离。
4. 首批策略保持预览，核对路由后再开启 worker；由管理员明确配置正式策略。
5. 回退前停止新 worker 与评估器。完整保存新表、审计与任务账本；回退旧应用前将切换后的新事件导出成兼容快照。旧版本无法表达的新来源规则必须暂停，并明确监测空档；优先前向修复。禁止直接回灌旧库覆盖新增数据。

2026-10-21 已发布生产并完成分表迁移、真实网关与浏览器验收，外部投递保持暂停。PostgreSQL隔离验证结束后清理；发布/回退及验证材料见工作区 memory/2026-10-21-opscenter-alert-center-production-deployment.md 和 artifacts。
