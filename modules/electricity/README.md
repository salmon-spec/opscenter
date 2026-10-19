# electricity — 国家电网电费监测模块（v0.7.6）

独立业务模块，把一个户号的电费余额、最近账单和逐日用电纳入运维工作台，
并在余额/欠费/阶梯升档/预算超支/采集异常时主动推送提醒。
模块自带采集、自带存储、自带告警引擎、自带诊断与预测、自带页面，
不读写其他模块的数据，也不依赖旧 backend。

## v0.5.0 相对 v0.2.0 的新增

| 版本 | 新增能力 |
|---|---|
| 0.2.1 | 通知通道**重试（指数退避）+ 熔断**（失败状态持久化，重启不立即重打）；`alert_history` **轮转**；`ELECTRICITY_JOB_START_TIME` 固定时刻采集；飞书**交互式卡片**（严重度配色 + 跳转按钮）；findings 带 `url` 深链 |
| 0.3.0 | **原始报文脱敏留档**（`ELECTRICITY_RAW_CAPTURE`）+ `GET /api/v1/diagnostics/raw`；**未调用接口探针**（`POST /api/v1/diagnostics/probe`，探 `c9/f02`、`c04/f03`）；**阶梯电价**与**分时电量**启发式解析；长期历史（`daily_history.jsonl` / `monthly_history.jsonl`）+ 保留策略；阶梯感知的耗尽预测；`step-enter`、`tou-tip-heavy` 两条规则 |
| 0.4.0 | **月度预算告警**（`electricity:budget`，预计超出 warning / 已超 critical）；**会话重登审计**（`electricity:relogin-storm`）；**事件出口** `ELECTRICITY_EVENT_WEBHOOK`（每轮采集推结构化事件）；**采集窗口** `ELECTRICITY_COLLECT_WINDOW` |
| 0.5.0 | 7 日预测（星期效应优先）；90 日趋势图；**预付费停电风险提示**；尖峰省钱建议；`GET /api/v1/report.csv` 报表导出 |

> **未采纳多户号**（2026-10-17 明确排除）：数据结构仍按 `households` 数组建模，未来需要时可直接扩展。

## 模块契约

| 接口 | 说明 |
|---|---|
| `GET /health/live`、`GET /health/ready` | 进程存活 / 数据目录可写 |
| `GET /api/v1/module` | 模块 key、版本与能力清单 |
| `GET /api/v1/summary` | 余额、剩余可用天数、7 日预测、本月电费与月末预测、告警计数 |
| `GET /api/v1/findings` | **与推送同源**的规则引擎结果（含 `url` 深链） |
| `GET /api/v1/overview` | 业务明细（户号、余额、账单、近 30 日用电、预测、本月电费），需工作台会话 |
| `GET /api/v1/usage` | 阶梯、分时、长期历史序列、7 日预测 |
| `POST /api/v1/refresh` | 手动触发一轮采集（**无视采集窗口**），需工作台会话 |
| `GET /api/v1/alerts` | 告警状态机 + 通知历史 + 通道/熔断/静默信息 |
| `GET /api/v1/alerts/rules` | 已生效的规则、阈值、安静时段、调度与窗口配置 |
| `POST /api/v1/alerts/test` | 向所有已配置通道发一条测试告警 |
| `POST /api/v1/alerts/silence`、`/unsilence` | 静默 / 解除静默（`{"id": "*", "hours": 12}`） |
| `GET /api/v1/diagnostics/raw` | 已留档报文列表 + 留档开关状态 |
| `GET /api/v1/diagnostics/raw/shape?name=` | 某份报文的**结构摘要**（键名/类型/列表长度），不返回数值 |
| `GET /api/v1/diagnostics/raw/content?name=` | 某份报文的脱敏全文 |
| `POST /api/v1/diagnostics/probe` | 调用平时不用的 `c9/f02`/`c04/f03`，判断阶梯/分时是否可取 |
| `GET /api/v1/report.csv?month=YYYYMM` | 月度报表（日用电 + 账单 + 告警次数） |
| `GET /ui` | 自带展示页（告警/阶梯/分时/趋势/诊断面板） |

网关通过 `handle_path /modules/electricity/*` 把前缀剥掉后反向代理到本模块。
工作台扩展模块目录注册 `/ui`，经受限的 API 桥接传递工作台会话，不把 JWT 暴露给 iframe。
直接访问 `<opscenter 入口>/modules/electricity/ui` 仍可使用工作台本地会话。


## 提醒告警（v0.2.0）

### 规则

| 告警 ID | 级别 | 触发条件 | 环境变量 |
|---|---|---|---|
| `electricity:credentials` | critical | 账号/摘要/户号未配置 | — |
| `electricity:no-data` | warning | 已配置但从未采集成功 | — |
| `electricity:stale` | warning | 采集结果超期未更新 | `ELECTRICITY_STALE_AFTER_SECONDS`（默认 86400） |
| `electricity:arrears` | critical | 国网返回待缴欠费 > 0 | — |
| `electricity:balance-critical` | critical | 余额低于临界值（随时可能停电） | `ELECTRICITY_CRITICAL_BALANCE`（默认 5） |
| `electricity:balance-low` | warning | 余额低于提醒阈值 | `ELECTRICITY_LOW_BALANCE`（默认 20） |
| `electricity:depletion` | warning | 预计可用天数不足 | `ELECTRICITY_DEPLETION_DAYS`（默认 5）、`ELECTRICITY_PRICE_PER_KWH`（默认 0.5） |
| `electricity:daily-spike` | info | 日电量异常升高 | `ELECTRICITY_DAILY_SPIKE_RATIO`（默认 2.0）、`ELECTRICITY_DAILY_SPIKE_MIN_KWH`（默认 3） |
| `electricity:slice-retry:<hash>` | info | 逐日/账单切片采集失败待重试 | — |

**预计可用天数**有可靠分档数据时按分档单价计算；否则按近 7 个已公布用电日的
日均电量乘年度账单均价计算。缺少逐日用电时才回退到余额观测差值；余额变动可能包含
充值或调整，不能在有用电记录时当作实际日耗费。充值导致余额上升时不使用余额差值。

### 告警状态机与降噪

- 状态：`pending → firing → resolved`，由 `alerts.json` 持久化，**重启不重复轰炸**。
- 首次告警立即推送；未恢复期间按 `ELECTRICITY_REPEAT_INTERVAL_SECONDS`（默认 86400）重复提醒。
- 条件消失只发**一条**恢复通知。
- 防抖：`ELECTRICITY_PENDING_OBSERVATIONS`（默认 1，设为 2 表示连续两轮命中才推送）。
- 免打扰：`ELECTRICITY_QUIET_HOURS="23:00-07:00"`，时段内非 critical 只记录不推送；critical 照常推送。
- 静默：`POST /api/v1/alerts/silence`，支持单条与全量，到期自动失效；静默期间工作台仍显示告警。
- 评估节奏：采集后立即评估 + 每 `ELECTRICITY_ALERT_INTERVAL_MINUTES`（默认 30）分钟独立复评
  （用于捕捉"数据变旧""余额随实测速率逼近阈值"这类与采集无关的变化）。

### 通知通道

工作台「通知设置」新增三种 [Apprise](https://github.com/caronc/apprise) 渠道。
Apprise `2.0.1`（[BSD-2-Clause](https://github.com/caronc/apprise/blob/master/LICENSE)）
随电费模块镜像安装，不需要另建 Apprise 服务；现有通知人和环境变量通道继续可用。

| 页面方式 | 地址示例 | 外部准备 |
|---|---|---|
| 短信（Apprise） | `twilio://SID:TOKEN@发送号码/接收号码` 或 `httpsms://KEY@发送号码/接收号码` | [Twilio](https://github.com/caronc/apprise/wiki/Notify_twilio) 账号与号码，或 [httpSMS](https://github.com/NdoleStudio/httpsms) 的 Android 手机、SIM 卡及 API Key |
| 微信推送（Apprise） | `wecombot://机器人Key`、`schan://SendKey`、`pushplus://Token`、`wxpusher://AppToken/UID` | 对应服务已开通，机器人或推送 Token 已获取；企微群机器人消息在企业微信群显示 |
| 邮件（Apprise） | `mailtos://smtp.example.com:465?user=发件账号&pass=授权码&to=收件地址` | SMTP 服务及授权码；特殊字符须按 URL 编码 |

Apprise 短信正文限制为最多 65 个字符，避免一条告警拆成多条收费短信。
三类 URL 的服务协议在保存时校验，原始 URL 保存在模块数据卷的 `notify.json`（0600），
列表仅回显渠道名称与遮罩地址。真实发送需外部账号/设备就绪；页面「测试」只发送给所选通知人。
`httpsms://` 使用的是 Apprise 对 httpSMS 服务的客户端集成；httpSMS 服务与手机本身不随模块镜像安装。

`ELECTRICITY_NOTIFY_CHANNELS` 为 JSON 数组，逐通道可设 `min_severity` 做分级路由：

```json
[
  {"type": "feishu", "webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"},
  {"type": "wecom",  "webhook": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx", "min_severity": "warning"},
  {"type": "dingtalk", "webhook": "https://oapi.dingtalk.com/robot/send?access_token=xxx", "secret": "SECxxx"},
  {"type": "webhook", "webhook": "https://example.com/hook", "min_severity": "info"}
]
```

| type | 必填字段 | 备注 |
|---|---|---|
| `feishu` | `webhook` | 可选 `secret`（自定义机器人签名） |
| `wecom` | `webhook` | 群机器人 markdown |
| `dingtalk` | `webhook` | 可选 `secret`（加签） |
| `webhook` | `webhook` | 通用 JSON 事件，便于接 Alertmanager 风格中继 |
| `serverchan` | `sendkey` | Server 酱 |
| `bark` | `webhook` 或 `device_key` | Bark（iOS） |
| `ntfy` | `webhook`+`topic` | ntfy |
| `email` | `smtp_host`/`smtp_port`/`username`/`password`/`mail_from`/`mail_to` | 465 走 SSL，其余走 STARTTLS |

也支持单值简写环境变量：`ELECTRICITY_NOTIFY_FEISHU`、`_WECOM`、`_DINGTALK`、`_SERVERCHAN`、
`_BARK`、`_NTFY`、`_WEBHOOK`。
未配置任何通道时模块照常运行，告警只在工作台面板可见（不报错、不影响采集）。
所有渠道超时可调：`ELECTRICITY_NOTIFY_TIMEOUT`（默认 10 秒）；单通道失败不影响其他通道。

## 数据与凭据

- 采集内核复用已验证运行的 `sgcc-standalone`：`vendor/web_api.py`（国网网页通道）
  与 `vendor/utils/crypt.py`（国密实现），授权见 `LICENSE.upstream`、`LICENSE.hass-state-grid`。
- 凭据由部署注入环境变量，不落盘、不进 Git：
  `ELECTRICITY_ACCOUNT`、`ELECTRICITY_PASSWORD_DIGEST`（32 位 MD5 大写摘要）、
  `ELECTRICITY_TARGET_ID`、`ELECTRICITY_TARGET_METER`（JSON）。
- 数据目录（`ELECTRICITY_DATA_DIR`，默认 `/data`）内：
  `snapshot.json`（采集快照）、`alerts.json`（告警状态机/静默/余额观测）、
  `alert_history.jsonl`（推送审计，追加写、0600）、`web_session.json`（国网会话）。
- 采集默认每 12 小时一轮，启动时立即执行一轮；手动刷新走 `POST /api/v1/refresh`。

## 本地测试

```sh
cd modules && python -m pytest electricity/test_main.py -q
```

## 部署要点

1. 构建 `opscenter/module-electricity:<tag>` 并导入承载节点（`imagePullPolicy: Never`）。
2. 新增 Secret：模块凭据（账号 / 摘要 / 目标表计）、通知通道（可选）与数据卷；
   沿用 `opscenter-jwt-keypair` 只读公钥。
3. 新增 Deployment + Service（与 `module-logs` 同构，`Recreate`、单副本、固定节点）。
4. 在网关 ConfigMap 中追加 `handle_path /modules/electricity/*` 路由块并热加载。
5. 后端启动时登记内置模块目录；前端将其显示在扩展导航、模块管理与值班首页。
6. 不改动其他模块的数据或资源。
7. **不要**再用 ConfigMap `subPath` 覆盖 `main.py`/`page.py`：镜像里已含 0.2.0 源码，
   残留挂载会反而把容器内代码覆盖回旧版本。

## 借鉴来源

| 来源 | 借鉴点 |
|---|---|
| [Poiig/sgcc_electricity](https://github.com/Poiig/sgcc_electricity) | 余额不足通知的多通道（PushPlus/URL/企业微信）与分级推送思路 |
| ARC-MX/sgcc_electricity_new、cdajun/sgcc_electricity_2026 | 余额阈值告警、`JOB_START_TIME` 定时、`IGNORE_USER_ID`、公布日延迟语义 |
| [stevenjoezhang/hass-state-grid](https://github.com/stevenjoezhang/hass-state-grid)、tiejiang29（`LICENSE.*`） | 采集内核与国密/设备身份实现（模块 vendor 目录已授权复用） |
| [caronc/apprise](https://github.com/caronc/apprise) | 「一个通道一个配置项」的统一渠道抽象与标签/分级路由 |
| Prometheus Alertmanager | pending→firing→resolved 状态机、重复提醒间隔、静默（silence）语义 |

## 回滚

删除本模块的 Deployment、Service、Secret、PVC 与网关路由块即可；
其他模块与工作台入口不受影响。
仅回滚 v0.2.0 功能时：把镜像 tag 改回上一版并恢复 ConfigMap 挂载即可，
`alerts.json` / `alert_history.jsonl` 为新增文件，旧版本会忽略它们。

