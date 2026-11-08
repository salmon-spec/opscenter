export const moduleNavigation = [
  { prefix: '/kubernetes/', groups: [
    { key: 'overview', label: '集群概览', path: '/kubernetes/overview', items: [{ label: '集群概览', path: '/kubernetes/overview' }] },
    { key: 'apps', label: '应用负载', path: '/kubernetes/workloads', items: [{ label: '工作负载', path: '/kubernetes/workloads' }, { label: 'Pod', path: '/kubernetes/pods' }] },
    { key: 'resources', label: '资源与运维', path: '/kubernetes/network', items: [{ label: '网络与入口', path: '/kubernetes/network' }, { label: '存储', path: '/kubernetes/storage' }, { label: '任务与事件', path: '/kubernetes/operations' }] },
  ] },
  { paths: ['/system/', '/database'], groups: [
    { key: 'system-app', label: '系统应用', path: '/system/monitor', items: [
      { label: '监控', path: '/system/monitor' },
      { label: '文件', path: '/system/files' },
      { label: '进程', path: '/system/processes' },
      { label: 'SSH', path: '/system/ssh' },
      { label: '防火墙', path: '/system/firewall' },
      { label: '终端', path: '/system/terminal' },
    ] },
    { key: 'runtime', label: '运行环境', path: '/database', items: [{ label: '数据库', path: '/database' }] },
  ] },
]
