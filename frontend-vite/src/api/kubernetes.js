/* OpsCenter v4.9 K3s 只读监控 API 客户端（FE-2 所有权文件，不改 src/api.js）。
   契约：GET /api/v2/clusters 返回裸数组；其余端点返回信封
   { data, data_timestamp, cached, cache_age_seconds, partial_errors, source_status }。
   后端未就绪（404/503）时由调用方 catch 并显示错误卡片 + 重试，不白屏。 */
import { api } from '../api'
import { unwrap, mergeMeta, apiErrorText } from './envelope'

export { unwrap, mergeMeta, apiErrorText }

const enc = (v) => encodeURIComponent(String(v ?? ''))

export const k8sApi = {
  clusters: (opts) => api.get('/clusters', undefined, opts),
  summary: (id, query, opts) => api.get(`/clusters/${enc(id)}/summary`, query, opts),
  nodes: (id, query, opts) => api.get(`/clusters/${enc(id)}/nodes`, query, opts),
  workloads: (id, query, opts) => api.get(`/clusters/${enc(id)}/workloads`, query, opts),
  pods: (id, query, opts) => api.get(`/clusters/${enc(id)}/pods`, query, opts),
  pod: (id, ns, name, opts) => api.get(`/clusters/${enc(id)}/pods/${enc(ns)}/${enc(name)}`, undefined, opts),
  podLogs: (id, ns, name, query, opts) => api.get(`/clusters/${enc(id)}/pods/${enc(ns)}/${enc(name)}/logs`, query, opts),
  services: (id, query, opts) => api.get(`/clusters/${enc(id)}/services`, query, opts),
  ingresses: (id, query, opts) => api.get(`/clusters/${enc(id)}/ingresses`, query, opts),
  storage: (id, query, opts) => api.get(`/clusters/${enc(id)}/storage`, query, opts),
  jobs: (id, query, opts) => api.get(`/clusters/${enc(id)}/jobs`, query, opts),
  events: (id, query, opts) => api.get(`/clusters/${enc(id)}/events`, query, opts),
  netpol: (id, query, opts) => api.get(`/clusters/${enc(id)}/network-policies`, query, opts),
}
