/* OpsCenter Configuration (P3-03: Externalized config) */
window.OpsConfig = {
  apiBase: '/ops/api/v2',
  navItems: [
    { key: 'nav', label: '服务导航', icon: 'fa-compass' },
    { key: 'monitor', label: '系统监控', icon: 'fa-chart-line' },
    { key: 'tools', label: '运维工具', icon: 'fa-toolbox' },
    { key: 'settings', label: '设置', icon: 'fa-cog' },
  ],
  refreshInterval: 30,
  maxRecentItems: 10,
  maxRecentDisplay: 5,
  version: 'v2.3',
};
