# 运维门户 0.4.0

独立门户保留纯色六边形图标、白色线条图标及左图右文布局。门户管理通过 OpsCenter 的 `ops-portal` 扩展模块提供，配置保存在本模块 PVC 的 SQLite 中。

- 独立访问：`http://10.66.66.13:30092/`，使用工作台账号登录，令牌仅保存在当前浏览器会话。
- 管理入口：工作台侧栏“运维门户”，路由 `#/extensions/ops-portal`；沿用现有沙箱及 API 消息桥，不向 iframe 下发工作台令牌。
- 管理功能：新增、编辑、移除、显隐、业务分组、顺序及默认快捷栏。修改需管理员权限，配置使用 revision 乐观锁，保存与审计为同一事务。
- 浏览器个人快捷栏与最近访问独立保存；正式目录由服务端统一持有。
- 初始目录包含 13 个控制台及 13 个已有 OpsCenter 功能入口。HTTP 核查与内嵌状态分别展示；既有六个应用的跨来源限制及两个 HTTPS 问题仍需按应用验收，不能以门户上线代替应用验收。

## 部署

使用 `deploy/k8s/opscenter-portal.json` 部署模块、专属 PVC、ClusterIP、NodePort 和入口网络策略。独立入口调度到 Master，NodePort 使用 Local 流量策略，入口网络策略限定 WireGuard、局域网及工作台 Web Pod。

用 `deploy/k8s/opscenter-portal-registration.json` 经核心模块注册 API 注册并显示入口。该记录使用已有注册模块机制持久保存，核心启动不会重置。门户配置不访问核心数据库，也不导入其他业务模块。

网关镜像在现网不可变前端镜像上仅增加 `/modules/ops-portal/` 路由；保留原静态包、发布清单和所有其他路由。后端及其他模块无需重建。

## 检查

`python -m pytest modules/ops_portal/test_main.py -q` 验证匿名/过期会话/角色、持久化、冲突、审计、URL 和目录约束。

发布前保存模块注册表备份、原部署模板和 Caddy 配置。回退只移除新增入口、恢复本次网关模板并停止门户部署；门户 PVC 与 SQLite 保留，不覆盖原注册表或其他模块资料。
