# 代码与历史版本索引（2026-10-20）

## 范围与历史

核心后端、前端源码及构建产物、Agent、独立模块、测试、依赖锁文件、K3s/Compose 配置、流水线和历史文档全部保留。沿用提交链，不改写旧提交或移动既有标签。

整理前 Forgejo/main 为 `69f543e`，GitHub/main 为 `6b3edd9`，GitHub 缺少 38 个已有提交，后者是前者的祖先，可快进同步。目标为同步 main 和版本标签。

`.source-compare/` 为本地生产对账副本、临时诊断脚本与归档包，忽略并保留；有效修改归入正式源码。真实配置、私钥、数据库和部署备份不作源码发布。

| 已有提交 | 内容 |
| --- | --- |
| cf2f34b / 7077190 | 模块化候选与生产部署 |
| 8097275 | 用量历史窗口基线 |
| 15bf6a2 | 监控 API、扩展路由与服务健康修复 |
| b8ae789 / fe02c59 | SSH 鉴权、探测、清理与生产覆盖文件 |
| 8d903ee | PostgreSQL I/O 探针容忍 |
| 4f4a24e / 67cd0ed | PVE/备份与 AI 旧入口退役 |
| 07a42c2 | 生产功能修复 |
| 456ddce / 5553678 | 电费模块与工作台接入 |
| b2ab056 | 电费 0.7.3 |
| cd6b43e | 隔离 CI 构建恢复 |
| 973505b | 电费 0.7.5：预测、日历、通知目标 |
| 69f543e | 电费 0.7.6：Apprise 通道 |

本轮按电费、服务目录/管理、模型用量、Agent、大屏、发布资料拆分新增提交。电费 0.7.7 与前端 5.5.13–5.5.16 是分批部署状态，未保留逐字复现的独立完整提交，以说明记载，不补造标签。最终标签对应可核实代码。

## 维护记录来源

工作区 memory 与 Obsidian PVE主机保存以下实测专题：2026-10-15 生产发布与监控跟进、2026-10-16 功能修复、2026-10-18 流水线恢复、2026-10-19 电费逻辑/通知、2026-10-20 电费与服务广场修复、离线主机恢复、模型多厂商、主机大屏与 Agent 统一。发布说明保留组件必要资料，凭据留在环境。

## 回归验证

使用隔离测试数据库，绝不能指向生产。安装各组件 requirements 与 pytest。模块入口同名，须分进程测试。

```sh
# repo root; set test DATABASE_URL first
python -m pytest backend/tests/test_model_usage_accounts.py backend/tests/test_command_code_cache.py backend/tests/test_extension_modules.py backend/tests/test_agent_address_resolution.py backend/tests/test_agent_health_recovery.py backend/tests/test_agent_metrics_collection.py agent/test_processes.py -q
# each module in its own process with PYTHONPATH=modules
python -m pytest modules/electricity/test_main.py -q
python -m pytest modules/service_catalog/test_main.py -q
cd frontend-vite
node --test tests/host-screen.test.mjs
npm run build
```

电费测试需要其声明的 Apprise 依赖；构建存在既有 ECharts 大包提示。测试结果和双端 SHA 核对另存归档维护记录。

## 本轮归档提交

| 提交 | 范围 |
| --- | --- |
| d095a1f | 服务管理与可见服务统计 |
| 4d2378d | 多厂商模型用量与账户配置 |
| d8bc015 | Agent 2.6.4 与部署模板 |
| 32ec9d9 | 主机大屏、前端 5.5.17 及构建产物 |
| 6e85725 | 分批指标保留清理配置归档 |
| fefabe5 | 生产电费 0.7.8 源码、通知设置与页面修复 |

归档回归合计 178 项通过，Vite build 通过。电费测试在独立快照进行，未改动另一个任务正在开发的 0.8.0。用户已明确要求先同步已验收版本，0.8.0 完成后补交。

新增关键注释解释 UTC 时间处理、账户历史隔离及快照删除标记。使用 annotated tags 保存组件版本和变更说明，旧标签保持原指向。源码统一 LF，避免跨平台换行改变 Agent 校验和；部分旧文件的差异因换行归一较大，可用 git diff --ignore-space-at-eol 查看逻辑变化。
