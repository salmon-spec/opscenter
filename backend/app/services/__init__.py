"""OpsCenter v5.0.0 中间件接入层（K3s middleware ns 七件套）。

设计原则（方案 §4）：
- 全部客户端惰性连接、fail-open：未配置（URL 为空）或不可达时 available()=False，
  调用方必须降级到原有行为，绝不因中间件故障阻断工作台核心功能；
- 连接凭证只进内存，不返回前端、不写日志（status()/error 统一脱敏）；
- 模块级单例 import 时不产生网络 I/O，首个调用才建连接；
- 每个模块独立，禁止互相 import（tasks/mq 依赖 redis_store 除外）。

模块清单：
- redis_store   Redis：缓存/锁/租约/只读浏览
- tasks         统一任务注册表（Redis 持久化 + 内存兜底）
- mq            RabbitMQ：异步任务队列 + 消费 worker
- doc_store     MongoDB：审计/事件/任务文档存储
- event_bus     Kafka：事件流
- nacos_config  Nacos：动态配置 + 服务注册
- object_store  MinIO：报告/导出对象存储
- zk_view       ZooKeeper：只读视图
"""
