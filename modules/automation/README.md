# 自动化作业 · 0.4.0

独立运行的 OpsCenter 风格界面与 Semaphore API 网关；支持工作台统一登录与沙箱通信。

## 首版范围

- 独立令牌登录；白底表格、页签、搜索与详情抽屉，支持窄屏。
- Ansible 模板创建、编辑；仓库/变量环境/主机清单选择。
- 静态 INI/YAML 清单创建与查看，只引用 Semaphore 中已有的 SSH 凭据。
- 手动提交任务，默认 Ansible 检查模式；查看状态与日志，正常停止活动任务。
- 五段 Cron 计划创建，默认暂停；支持启用/暂停。真实调度由 Semaphore 执行。
- SQLite 保存本模块审计及演示数据；真实模板、清单、计划和日志归 Semaphore 持有。
- `/health/live`、`/health/ready`、`/api/v1/module`、`summary`、`findings` 为后续集成提供入口。

所有 `/api/` 路由要求 Bearer 认证：独立运行使用模块令牌，配置 `OPS_JWT_PUBLIC_KEY_FILE` 后只接受工作台管理员 JWT。UI 不接触 Semaphore Token、私钥或变量环境内容。
仓库、变量环境和 SSH 凭据在 Semaphore 原生界面配置。本版只支持编辑/运行 Ansible 模板。
修改已有模板时先获取完整源数据，保留 vault、survey、task_params 等未显示配置。

## Windows 本地启动

在 PowerShell 执行：

```powershell
.\modules\automation\start.ps1 -Mode demo -Port 18089
```

从仓库根目录执行，或用脚本绝对路径。首次启动需要 Python 3.13，脚本在模块 `.venv` 安装依赖。
访问 `http://127.0.0.1:18089/`，从模块 `.local/admin-token` 文件读取独立登录令牌。
令牌只保存在浏览器当前会话存储中；退出清除。数据目录、令牌与虚拟环境均不入 Git。

`demo` 明确显示为演示，初始清单使用 RFC 5737 示例地址；手动运行只生成模拟记录，
计划启用也不会执行。数据在刷新/服务重启后保留。不预装生产主机或生产凭据。

## 连接真实 Semaphore

1. 准备自托管 Semaphore 项目，配置 Git 仓库、变量环境与专用 SSH 凭据。确保 runner 已安装 Ansible。
2. 用有该项目所需权限的专用用户生成 API Token，放到工作区之外的受保护文件；不复用平台管理员凭据。
3. 用以下环境变量启动独立模块：

| 变量 | 说明 |
|---|---|
| `AUTOMATION_MODE=semaphore` | 固定真实连接模式；不可从网页切换 |
| `SEMAPHORE_URL` | 不含 `/api` 的服务基础 URL。远端必须 HTTPS，使用系统信任链；本机 loopback 或 Docker 服务名 `semaphore` 允许 HTTP |
| `SEMAPHORE_PROJECT_ID` | 正整数，所有请求固定在该项目内 |
| `SEMAPHORE_TOKEN_FILE` | 只读 API Token 文件的路径 |
| `AUTOMATION_DATA_DIR` | 独立 SQLite 数据目录，默认模块 `.local` |
| `AUTOMATION_TOKEN_FILE` | 独立网页访问令牌文件；默认数据目录 `admin-token`，不存在时自动生成 |
| `AUTOMATION_ALLOWED_HOSTS` | 精确主机名列表，默认 `127.0.0.1,localhost,testserver`；反向代理时添加真实入口主机名 |

设置环境后执行 `start.ps1 -Mode semaphore -Port 18089`。令牌值不放在 URL 或命令参数中。
Semaphore 服务需配置 `TZ=Asia/Shanghai`，真实定时任务以该服务时区为准。
网关不关闭 TLS 验证，不跟随重定向，不自动重试写请求；超时后先核对状态。
真实模式故障返回错误和保留的上次页面数据，不回退演示数据。状态列表每次整体获取，
每个上游响应限制 4 MiB；大量历史时需在 Semaphore 归档/缩小范围后再用本模块。

## Docker

```sh
cd modules/automation
docker compose up -d --build
```

默认仅在宿主 `127.0.0.1:18089` 开放演示模式，独立非 root 容器和命名卷。
该 Compose 只启动本网关，Semaphore 服务需单独准备。真实模式增加上述配置、只读挂载
Semaphore Token 文件，并让网关访问该服务。Windows 本机没有 Docker；Linux 镜像已在 VM100 构建运行。
数据卷中的 `admin-token` 可由宿主管理员读取；不要把端口直接公开至公网。

本模块仅有独立管理员身份；多人使用与工作台统一身份应在后续接入时实现。
反向代理对外提供 HTTPS。不要关闭 Host 校验或将工作台 JWT 直接作为 Semaphore Token。
日志/清单可能包含敏感运维信息，备份目录和令牌需限制属主权限；Windows 目录权限使用 NTFS ACL。
Ansible 检查模式仍连接真实目标，且不保证任意 playbook 都没有副作用，需审查 Git 脚本。

## 验证

```powershell
.\modules\automation\.venv\Scripts\python.exe -m pip install -r modules/automation/requirements-dev.txt
.\modules\automation\.venv\Scripts\python.exe -m pytest modules/automation/test_main.py -q
node --check modules/automation/static/app.js
```

适配依据：[官方 API 定义](https://github.com/semaphoreui/semaphore/blob/develop/api-docs.yml)、
[路由](https://github.com/semaphoreui/semaphore/blob/develop/api/router.go)及
[计划实现](https://github.com/semaphoreui/semaphore/blob/develop/api/projects/schedules.go)，2026-10-22 查询。
当前适配版本已固定到 Semaphore 2.19.17，在 VM100 隔离项目执行真实 Ansible 成功/预期失败作业。
24项本地测试使用 HTTP 模拟覆盖协议与边界；真实验收发现的停止限制见下文。

## 后续接入与回退

保持本模块独立服务和数据卷，先在测试环境验证真实 Semaphore 项目，再加入工作台入口。
目前 API 的独立管理员令牌与原工作台沙箱消息桥尚未打通；正式集成应复用工作台公钥验 JWT，
按用户角色限制写入，并适配站内前缀和沙箱请求桥。本轮只提供独立 `/api/v1/`，不宣称已完成统一登录。

停止本网关不会停止 Semaphore 已排队或已运行的任务，也不会暂停 Semaphore 内的计划。
停止真实业务前先在执行引擎核对任务和计划。仅演示模式可直接停止本地进程；保留 `.local` 或
命名卷以保留历史。禁止使用 `docker compose down -v` 回退，以免删除数据。未提交或推送。

## VM100 独立测试部署

测试机目录 `/opt/opscenter-automation-test`，服务为 `opsautomation-gateway`、`opsautomation-semaphore`。
网关仅监听远端 `127.0.0.1:18089`；引擎没有宿主端口，留在 Docker 内部网络。
网关另接入口网络以发布 loopback 端口；Docker 内部网络本身不发布端口。
未更改工作台导航、JWT、生产网关或生产数据库。

在本机执行以下命令建立 SSH 隧道，再打开 `http://127.0.0.1:18089/`：

```powershell
.\modules\automation\connect-test.ps1
```

该本地端口不能同时运行演示服务。隧道断开只影响访问，不停止远端容器。
远端独立访问令牌文件为部署目录 `secrets/gateway-token`，需通过管理员权限读取；不要将令牌写在 URL。
引擎项目只配置容器 localhost 与隔离验收脚本，不配置生产 SSH 密钥和集群权限。
备份在同目录 `backups/`，SQLite 使用在线 backup API，密钥配置恢复包按敏感资料保护。

## 停止语义（真实验收发现）

Semaphore 2.19.17 接受停止并将任务置为 stopped 后，已启动的 Ansible 本地子命令仍可能继续。
隔离验收观察到20秒延迟标记仍写入。界面标为“引擎已停止”，确认框、返回提示和详情均提示核对目标。
停止API额外返回 execution_may_continue=true。不能将任务状态当作远端进程终止或变更回滚证据。
要求强制终止与执行隔离时，应使用每任务独立容器/runner并另行验收进程及目标副作用。

## 工作台生产部署（0.2.0）

入口为 `http://10.66.66.15:30088/#/extensions/automation`。独立运行方式保留；生产通过工作台管理员 JWT 和原有沙箱消息桥访问，无需模块二次登录。
部署清单为 `deploy/k8s/opscenter-automation-module.json`，需要先准备所引用的专用引擎 Secret、只含公钥的 JWT 挂载和隔离脚本 ConfigMap，并导入固定镜像。真实发布脚本、配置备份及恢复资料保存于 master/worker2 的 `/home/ubuntu/opscenter-automation-20261023/`。
生产当前只有隔离 localhost 目标和暂停计划。local-path 的 2Gi PVC 申请不构成单卷硬配额，应监控 worker2 实际磁盘占用。
内置浏览器任务与日志验收通过；桌面 Chrome 内嵌仍显示空白，未判通过。首次初始化一次状态读取超时，后续任务与重建后连续复测正常；详情见项目记忆的生产接入专题。

## 工作台界面修订 20261023-r3

页面通过四个页签显示对应列表、搜索、数量和操作。生产以 `opscenter-automation-ui-20261023-r3` ConfigMap 覆盖静态目录，部署清单已包含此资源与挂载，基础镜像保持 0.2.0。下次重建镜像需包含当前 static/ 三文件，并在核对哈希后处理覆盖挂载。
发布前模板、静态文件及回滚脚本位于 master `/home/ubuntu/opscenter-automation-ui-20261023/`，详见项目记忆的界面优化专题。


## 0.3.0：脚本编辑与 PVE 主机台账同步

任务模板列表新增“编辑脚本”。管理员可以编辑配置的本地 Git 作业仓库内 `.yml`、`.yaml`、`.py`、`.sh` 文件；外部仓库继续在源仓库管理。保存会检查内容版本、拒绝覆盖他人修改，并提交 Git。任务排队或运行期间禁止保存；脚本仓库与执行引擎共用持久卷，后续任务读取新提交。脚本正文保留缩进与尾部换行，单文件上限 128 KB。脚本语法与执行结果由作业运行验证。

配置 `AUTOMATION_SCRIPTS_DIR` 为本地 Git 仓库绝对路径，`AUTOMATION_SCRIPTS_REPOSITORY_ID` 为该仓库在 Semaphore 中的 ID。网关需安装 Git 并具有仓库写权限。模板详情的脚本接口为 `GET/POST /api/v1/templates/{id}/script`，POST 字段为 `path`、`content`、`revision`（读取时返回的 SHA256）。仓库只存脚本，不存访问凭据。脚本由管理员维护，拥有执行环境已配置的权限。

### 同步作业

- `runbooks/pve_inventory.py`：安装到 PVE 的只读采集器，交叉核对集群清单与所有在线节点的 QEMU 列表，读取配置中的 CPU、内存与 cloud-init IPv4。以宿主 machine-id 的摘要标识来源。节点离线、读取失败、清单变化时退出失败。
- `runbooks/sync_pve_hosts.py`：通过严格校验 SSH 主机密钥的专用受限密钥读取清单，经现有主机 API 同步；默认预览，加 `--apply` 才写入。
- `runbooks/pve-sync.yml`：运行该 Python 脚本的 Ansible 作业入口；检查模式仅预览，执行模式实际同步。使用容器 localhost 清单，不在虚拟机内执行升级命令。

同步按 `pve-source:<来源>` 和 `pve-vmid:<VMID>` 标签识别托管记录；首次可在配置中用主机 UUID→VMID 明确绑定历史记录。未绑定的现存主机仅在管理地址唯一匹配时接管；冲突会停止。新增记录补名称、IP、资源信息与来源标签；无静态 IPv4 时登记 `.invalid` 占位并标明“待补管理IP”，不猜测可连接地址。既有名称、管理连接配置、凭据、K3s 映射及人工备注保留，资源信息写在 `[PVE sync]` 行。

已删除虚拟机的记录通过现有归档 API 从主机管理移除，历史与磁盘保留。关机虚拟机仍保留，PVE 模板不新增为主机。宿主、云主机及其他来源不会因未出现在当前 PVE 清单中被移除。清单为空默认停止，确实删除最后一台虚拟机时须显式使用 `--allow-empty`。API 写入不自动重试，失败时日志显示已完成的操作，修复后再次运行核对。

运行示例：

```sh
python3 sync_pve_hosts.py --config /run/pve-sync/config.json
python3 sync_pve_hosts.py --config /run/pve-sync/config.json --apply
```

配置 JSON 包含 `source_id`、`pve_ssh`、`known_hosts`、`ssh_key`、`opscenter_url`、`username_file`、`password_file`、`legacy_vmids`；凭据字段均为只读文件路径。对外工作台地址必须 HTTPS，容器内部 `http://web` 使用已限制的网络路径。PVE 的 SSH 公钥必须设置 `restrict` 与强制命令，只运行独立安装的采集器，不能使用作业密钥执行其他命令。

生产 0.3.0 使用镜像内静态文件，并取消旧 UI ConfigMap 覆盖。初始化仅补入不存在的脚本，重建不会覆盖编辑后的内容。网络仅新增到指定 PVE 的 TCP/22、内部 web TCP/80 和集群 DNS 的出站。仅引擎挂载工作台登录凭据与 PVE 只读密钥；网关不挂载这些凭据。未指定周期时只创建手动作业，不启用定时计划。

验证：`python -m pytest modules/automation/test_main.py modules/automation/test_scripts.py -q`；部署后须验证检查/执行/幂等、脚本保存及重建持久化、匿名鉴权，并保留当前部署和两端数据库备份。


## 0.4.0：新增虚拟机自动接入监控

同步执行模式会为运行中的受管PVE虚拟机安装或修复Agent，并等待后台任务完成、认证指标可读。已运行Agent只检查监控数据，不重复安装；检查模式保留只读预览。失败在作业及主机任务中明确显示，下次执行可重试。

主机管理新增默认自动安装Agent，状态由“部署中”变为“运行中/在线”或具体失败。PVE同步登记缺少SSH凭据的虚拟机时，核心通过可信PVE核对来源、VMID、cloud-init静态IP、管理用户与公钥，复用匹配的已存加密密钥，再经QEMU Guest Agent取得SSH主机公钥并严格验证连接；密码/私钥不经过同步脚本。需Linux/systemd、可用QEMU Guest Agent、cloud-init公钥有对应管理密钥及免密sudo。

PVE安装 `deploy/pve/opscenter-agent-forward.py` 到 `/usr/local/sbin/opscenter-agent-forward`，并配置 `k8s-vm-forward.service` 的ExecStartPost。此脚本持久化管理目标，限制六集群节点到对应客户机的TCP22/19100及已建立的返回连接。已有防火墙策略保留。接入完成后使用原30秒采集循环保存历史并推送主机/监控模块。

运行表单支持 JSON extra vars。WireGuard 作业提供目标主机、Hub、接口、自动地址池和 Endpoint 参数；默认只读检查，正式运行通过握手和连通性校验后回填主机 WG IP。安装脚本及 YAML 均可在页面编辑。详见 docs/releases/automation-0.4.0.md。
