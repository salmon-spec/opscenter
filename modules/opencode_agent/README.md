# OpenCode Agent 模块

模块 0.2.0，原生 OpenCode 1.19.3。2026-10-23 权限与响应修订 controls-r4 已上线。默认界面为工作台中的任务工作区；顶部「原生 OpenCode」通过原生 PTY 执行 attach，连接同一个执行服务、持久目录与任务。高级功能继续由官方交互界面提供，功能覆盖与验收边界见 [CAPABILITIES.md](CAPABILITIES.md)。

生产入口：http://10.66.66.15:30088/#/extensions/opencode-agent 。2026-10-23 已更新；当前生产使用原 0.1.1 基底镜像和不可变 0.2.0 代码包覆盖，完整镜像需下次按本目录源码重建。发布资料在 master `/home/ubuntu/opscenter-opencode-0.2.0-20261023/`，包含源包、数据库/工作区备份、实际 Pod 模板与回滚脚本。工作台 web 静态包不随本模块更新。

## 使用

- 左侧选择或新建工作区，任务按工作区保存；旧任务迁入默认工作区。支持搜索、归档、重命名、分支、撤销/恢复、上下文压缩和导出到工作区文件。
- 输入框选择快速执行、Build、Plan 或自定义 Agent，选择原生已连接模型及推理选项。`/命令 参数` 执行原生命令。文件面板可浏览、搜索、编辑；「＋」上传附件，每个文件不超过 2 MiB、最多 10 个附件。附件使用原生文件引用，内容读取、截断及图像准备交给 OpenCode；图片/PDF 是否支持由所选模型决定。
- 「设置与扩展」管理原生提供方 API Key、Agent、命令、MCP 与 npm 插件。提供方目录来自当前原生引擎，已连接模型不会伪装成连接成功；推理结果和限额仍以真实请求为准。
- 项目规则写入 `AGENTS.md`；技能写入 `.opencode/skills/<name>/SKILL.md`。本模块已恢复项目配置扫描，保存技能后重新发现。修改扩展会重载原生配置，须等待执行任务空闲。
- 文件/变更、任务清单、子任务、Token/费用、工具确认、提问、停止、诊断和审计均在工作区内。消息支持 Markdown、代码块及折叠思考过程。
- 「终端」提供真实 Bash PTY；「原生 OpenCode」进入官方完整 TUI，可用 Ctrl+P 打开命令面板、Tab 切换 Agent。可展开为整个模块，支持键盘、鼠标、滚动及窗口缩放。关闭详情侧栏或刷新任务会同步当前目录的原生任务到左侧列表。
- 每个执行环境最多创建四个 PTY，跨工作区共用额度。长时间不用时请关闭终端；输出桥接缓冲 512 KiB、连接无访问 30 分钟后关闭，原生终端进程需通过关闭按钮结束。

## 模型与扩展

DeepSeek 官方、OpenCode Go 和其他原生 API Key 提供方均可在模型连接页配置；提供方 OAuth、MCP 认证及其他高级选项使用官方界面或官方 CLI，需对应账户与可用回调条件。不要把密钥写进任务说明或文档。原生授权和配置存在专用持久卷，不是工作台核心库。

OpenCode 原生目录可能同时提供无需 Key 的免费模型，这与 DeepSeek 官方直连不同。名称、模型是否可用、限额和计价均以原生提供方实测为准；不能据此推断 Go 或 DeepSeek 官方 Key 有效。

扩展编辑器形状为 `agents`、`commands`、`mcp` 三个对象和 `plugins` 数组。Agent 支持 description、prompt、mode、disable、steps；命令支持 template、description、agent、subtask。MCP 支持本地 command 数组或无内嵌凭据的 HTTPS 远端；高级凭据/认证在原生配置完成。未接通的服务会保留真实未连接状态。

## 边界

所有业务 API 强制管理员 EdDSA JWT，使用工作台公钥验证；原生密码不进入页面，原生服务不对外暴露。界面仅通过父页限定 API 前缀的消息桥接访问网关，没有同源权限。

运行在独立容器和持久工作区，不自动取得生产 SSH、Kubeconfig、Docker socket 或 ServiceAccount Token。Bash、Git、Python、curl 和 SSH 客户端可用；连接外部主机或服务仍需明确配置其访问方式。工作区是同一管理员在一个运行环境中的目录划分，不是多租户安全隔离；原生界面保留官方的任务操作与并发方式。

默认关闭外部分享与自动升级；新任务默认「需要询问」，常规读取直接执行，写入、终端和子任务按原生请求确认。「完全控制 · 容器」自动放行当前执行容器内工具，仍受容器本身权限和挂载范围约束。Plan 保留编辑、终端及子任务拒绝规则。任务空闲后才能切换权限，切回询问会清除该工作区原生临时长期批准；界面只提供单次批准与拒绝。手工终端是管理员直接输入命令，不受模型工具审批模式控制。MCP、插件和项目配置是可执行扩展；生产接入范围应与实际任务相符。审计记录操作者、动作、目标和受理结果，不记录 Key、提示词或终端输入内容。

## 本地和完整镜像构建

从仓库根构建 gateway 的 `modules/opencode_agent/Dockerfile` 和 runner 的 `deploy/docker/opencode-runner.Dockerfile`。Linux runner 原生二进制需要支持 AVX 的实际运行节点；当前生产固定在已验证的 worker2。

Compose 模板为 `deploy/docker/compose.opencode.yaml`。准备属于 gateway 用户的持久目录、受保护的原生密码文件和工作台 JWT 公钥，再设置模板中声明的文件位置；不要把签名私钥交给模块。gateway 的 workspace 和 runner 配置挂载必须保留，原生全局配置实际写入 `opencode.jsonc`。

K3s 模板为 `deploy/k8s/opscenter-opencode-module.yaml`，用于构建并导入新镜像后的部署。当前生产代码/离线 WebSocket 依赖覆盖必须与新镜像一起处理：包含全部 vendor、terminal.py 和最终 main.py，再移除旧覆盖或切换为同一份源包。不要用旧镜像直接覆盖当前生产模板而丢失功能。

测试：从仓库根以包含仓库路径的 PYTHONPATH 运行 `python -m pytest modules/opencode_agent/test_main.py modules/runtime/tests -q`。入口密码校验测试需要 POSIX shell；Windows 可将 Git Bash 的 bin 目录加入该次测试进程 PATH。浏览器验收使用真实工作台消息协议和生产入口；Chrome 自动化下沙箱仍为空白，当前内置浏览器验收与该兼容性问题分别记录。

第三方前端代码及许可证在 vendor/；版本见 vendor/VERSIONS.json。xterm 复用工作台已有依赖，Markdown 由 marked 解析并经过 DOMPurify 的 HTML 配置清洗，不加载消息中的外部图片或脚本。

## 权限与响应修订

模型目录精简缓存、HTTP连接复用及并行初始化保留。任务文字使用轻量live接口约150ms拉取原生SSE缓存，完整状态运行中约1.2秒、空闲5秒刷新，侧栏按需加载。提交确认返回前即可显示已生成文字；慢历史响应不会覆盖更新的流式内容，失败草稿和双击防重保留。

快速执行使用简洁的运维指令，保留项目规则、技能与实际工具验证；Build原生执行和Plan均可选择。模型、Agent、推理和新任务权限偏好按工作区保存。权限真实写入原生会话，按最后生效的通配规则判断；原生PATCH追加规则，不能按第一条判断模式。普通消息及自定义命令均统一校验Agent并应用Plan约束。

网关SQLite保持WAL和synchronous=FULL，表结构仅首次初始化，普通读取不再执行写事务，避免被审计落盘阻塞。审计顺序与数据持久化要求保留。备份使用SQLite backup API，不能仅复制活跃主库文件而漏掉WAL。

当前生产不可变代码包opscenter-opencode-controls-r4-20261023；master /home/ubuntu/opscenter-opencode-controls-20261023/保存变更前模板、源码及release.py rollback。回滚只恢复代码，保留当前数据库、任务和凭据。原生会话权限也会保留；需要恢复询问时先在当前版本切回询问并保存偏好，不能把代码回滚当成权限撤销。

验证：Python/runtime53项、Node5组异步行为；包括WAL持有写事务时读取仍完成、权限规则追加后的模式判断、Plan命令约束、发送防重/失败恢复、切任务竞态及流式历史竞态。实际首字仍受模型上下文、缓存及提供方负载影响，单次速度样本不能代表保证延迟。
