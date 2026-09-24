/* OpsCenter v5.0.0 数据服务 API 客户端（Redis/RabbitMQ/Kafka/ZooKeeper/Nacos/MinIO/MongoDB）。
   契约：全部端点返回信封（见 api/envelope.js），HTTP 200 且 internal available=false 时页面降级展示。
   端点在 backend/app/data_services.py 冻结；不要绕过 api.get 直连。 */
import { api } from '../api'
import { unwrap, mergeMeta, apiErrorText } from './envelope'

export { unwrap, mergeMeta, apiErrorText }

const enc = (v) => encodeURIComponent(String(v ?? ''))

export const DS_KINDS = [
  { kind: 'redis', name: 'Redis', icon: '🔴', desc: '缓存 / 分布式锁' },
  { kind: 'rabbitmq', name: 'RabbitMQ', icon: '🐇', desc: '异步任务队列' },
  { kind: 'kafka', name: 'Kafka', icon: '🟣', desc: '事件流' },
  { kind: 'zookeeper', name: 'ZooKeeper', icon: '🧩', desc: '协调服务' },
  { kind: 'nacos', name: 'Nacos', icon: '🧭', desc: '配置 / 注册中心' },
  { kind: 'minio', name: 'MinIO', icon: '🪣', desc: '对象存储' },
  { kind: 'mongodb', name: 'MongoDB', icon: '🍃', desc: '文档存储' },
]

export const dsApi = {
  configs: (opts) => api.get('/data-services/config', undefined, opts),
  config: (kind, opts) => api.get(`/data-services/${enc(kind)}/config`, undefined, opts),
  saveConfig: (kind, body, opts) => api.put(`/data-services/${enc(kind)}/config`, body, opts),
  testConfig: (kind, body, opts) => api.post(`/data-services/${enc(kind)}/config/test`, body, opts),
  resetConfig: (kind, opts) => api.post(`/data-services/${enc(kind)}/config/reset`, undefined, opts),
  instances: (query, opts) => api.get('/data-services/instances', query, opts),
  createInstance: (kind, body, opts) => api.post(`/data-services/${enc(kind)}/instances`, body, opts),
  testNewInstance: (kind, body, opts) => api.post(`/data-services/${enc(kind)}/instances/test`, body, opts),
  updateInstance: (id, body, opts) => api.put(`/data-services/instances/${enc(id)}`, body, opts),
  deleteInstance: (id, opts) => api.del(`/data-services/instances/${enc(id)}`, opts),
  testInstance: (id, opts) => api.post(`/data-services/instances/${enc(id)}/test`, undefined, opts),
  promoteInstance: (id, opts) => api.post(`/data-services/instances/${enc(id)}/promote`, undefined, opts),
  overview: (opts) => api.get('/data-services/overview', undefined, opts),
  monitorOverview: (opts) => api.get('/data-services/monitor/overview', undefined, opts),
  status: (kind, query, opts) => api.get(`/data-services/${enc(kind)}/status`, query, opts),
  monitor: (kind, query, opts) => api.get(`/data-services/${enc(kind)}/monitor`, query, opts),
  metrics: (kind, query, opts) => api.get(`/data-services/${enc(kind)}/metrics`, query, opts),
  timeseries: (kind, query, opts) => api.get(`/data-services/${enc(kind)}/timeseries`, query, opts),
  dimensions: (kind, opts) => api.get(`/data-services/${enc(kind)}/dimensions`, undefined, opts),
  diagnostics: (kind, query, opts) => api.get(`/data-services/${enc(kind)}/diagnostics`, query, opts),
  probe: (kind, opts) => api.post(`/data-services/${enc(kind)}/probe`, undefined, opts),
  redisKeyspace: (opts) => api.get('/data-services/redis/keyspace', undefined, opts),
  redisKeys: (query, opts) => api.get('/data-services/redis/keys', query, opts),
  rabbitQueues: (opts) => api.get('/data-services/rabbitmq/queues', undefined, opts),
  kafkaTopics: (opts) => api.get('/data-services/kafka/topics', undefined, opts),
  kafkaGroups: (opts) => api.get('/data-services/kafka/consumer-groups', undefined, opts),
  zkTree: (query, opts) => api.get('/data-services/zookeeper/tree', query, opts),
  nacosServices: (opts) => api.get('/data-services/nacos/services', undefined, opts),
  nacosConfigs: (query, opts) => api.get('/data-services/nacos/configs', query, opts),
  minioBuckets: (opts) => api.get('/data-services/minio/buckets', undefined, opts),
  minioObjects: (query, opts) => api.get('/data-services/minio/objects', query, opts),
  mongoDatabases: (opts) => api.get('/data-services/mongodb/databases', undefined, opts),
  mongoCollections: (query, opts) => api.get('/data-services/mongodb/collections', query, opts),
}
