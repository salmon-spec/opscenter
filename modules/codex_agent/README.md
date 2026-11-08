# Codex 工作台模块 0.3.0

独立模块 `codex-agent`，使用官方 `@openai/codex` 0.166.0 的 app-server stdio 协议。网关复用工作台 JWT 管理员鉴权和沙箱 iframe 消息桥，前端不持有工作台 Token 或模型密钥。通过模块注册 API 加入侧栏，无需修改核心应用源码。

## 功能

- 独立工作区、会话创建/改名/归档/恢复；Codex 原生历史持久化。
- 原生实时消息、工具调用与输出、思考摘要、Git文件变更查看；中断执行和错误状态。
- 原生单次执行/文件审批与补充问题回答；需要询问、完全控制·容器、Plan 只读模式。
- 文件浏览/编辑/上传，PNG/JPEG/GIF/WebP 预览与图片输入；Markdown 与行内工作区图片路径预览。
- 模型和推理强度选择；技能/MCP 状态、工作区 Git 变更、管理员命令面板、操作审计。
- 管理员命令面板每次最多20秒，输出256KiB；不提供交互式 PTY。

`full` 仅为新模块容器权限，不挂载宿主目录、集群凭据或旧 Agent 数据。工作区是同一管理员的目录划分，不是多租户安全边界。容器只能经网络策略访问公网 HTTP/HTTPS 与 DNS，不能横向访问集群私网。

## 配置与运行

`CODEX_HOME=/state/codex`，`CODEX_GATEWAY_DATA=/state/gateway`，`CODEX_WORKSPACE=/workspace`。设置 `CODEX_API_BASE`、`CODEX_API_KEY_FILE`、`CODEX_MODEL` 可复用已有 Responses API；密钥以专用 Kubernetes Secret 只读挂载，由 app-server 子进程通过环境变量读取，不写入镜像、源码或页面。未设置自定义服务时支持原生 OpenAI Key/ChatGPT设备授权。

新模块使用独立 PVC；旧 OpenCode 模块停用时保留其 PVC、会话、文件、配置和部署模板，便于恢复。

验证：`python -m pytest modules/codex_agent/test_main.py -q`。部署清单 `deploy/k8s/opscenter-codex-module.yaml`；模块注册请求体 `module.json`。具体实测与发布记录以工作区 `artifacts/codex-module-20261105/report.md` 为准。

官方协议文档：https://learn.chatgpt.com/docs/app-server 。

实现说明：复用原 Agent 的模型 API，不替换为其他账号。对于旧 API，模块将 Codex 的 namespace/custom 工具定义转换为函数格式；图片请求经同一服务的 Chat Completions 端点适配，工具仍由官方 Codex 执行。API 限流使用有界重试和单密钥并发控制。

节点前置：在固定的 Worker 2 上加载本目录 `apparmor.profile` 为 `/etc/apparmor.d/opscenter-codex-userns`。它只为新模块授权原生 user namespace，避免 Ubuntu 自动切入受限 profile；未修改全局 sysctl。Pod 仍为 UID10001、capabilities=ALL drop、禁止提权、无宿主挂载/ServiceAccount令牌。部署使用 Localhost AppArmor profile，迁移节点时须先加载该 profile。

验收边界：默认沿用原 Agent 的 Agnes 2.5 Flash；官方 Codex 负责执行循环。该第三方模型的原生 freeform apply_patch 能力未列为通过项，文件创建、编辑和校验已通过原生终端实测。

## 0.3.0 发送与界面

消息在点击发送时立即显示；创建会话与提交在后台继续，失败恢复输入。已有原生会话复用加载状态，重启后首次续聊恢复；轮询读请求不再初始化写库。上游HTTP连接复用，执行时150ms检查事件、空闲降低频率、隐藏页面暂停轮询。界面采用窄侧栏、居中对话、底部输入框及可折叠工具面板。首次会话建立和模型首字仍受执行引擎及API耗时影响。

## 0.3.0 限流与对话显示

上游429遵循Retry-After（无有效值时60秒，最多1小时），单层单次请求，禁用原生HTTP/流重试叠加；冷却期间本地拒绝再次推理，已有运行任务返回409。提供方解除限流后下一次成功请求清除错误状态；不绕过账号额度。原生reasoning与think标签不渲染，commentary进展默认折叠；错误实体解码，失败问题可放回输入框。
