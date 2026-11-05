// Only allowlisted summary/status endpoints; dashboard widgets never fetch secrets or execute jobs.
const summary=key=>`/modules/${key}/api/v1/summary`
export const moduleWidgets={
  electricity:{name:'电费监测',path:'/modules/electricity/api/v1/overview',link:'/extensions/electricity',icon:'ϟ',color:'#efad55'},
  logs:{name:'日志中心',path:summary('logs'),link:'/logs',icon:'≋',color:'#57c5d8'},
  services:{name:'服务目录',path:summary('service-catalog'),link:'/services',icon:'◈',color:'#699aff'},
  automation:{name:'自动化作业',path:summary('automation'),link:'/extensions/automation',icon:'▶',color:'#b295f4'},
  usage:{name:'模型用量',path:'/modules/command-code-usage/status',link:'/extensions/command-code-usage',icon:'AI',color:'#e78fc3'},
  oss:{name:'OSS 存储',path:'/modules/oss-browser/status',link:'/extensions/oss-browser',icon:'▣',color:'#edb264'},
  opencode:{name:'OpenCode Agent',path:summary('opencode-agent'),link:'/extensions/opencode-agent',icon:'⌘',color:'#75c49e'},
  sms:{name:'短信通知',path:summary('sms'),link:'/extensions/sms',icon:'✉',color:'#72b5e8'},
  password:{name:'密码库状态',path:'/modules/password-manager/status',link:'/extensions/password-manager',icon:'◇',color:'#cf9ce7'},
  subscriptions:{name:'订阅管理',path:summary('subscriptions'),link:'/extensions/subscriptions',icon:'↻',color:'#6fc5ac'},
  inventory:{name:'物品与保修',path:summary('home-inventory'),link:'/extensions/home-inventory',icon:'▦',color:'#d5ad69'},
  tasks:{name:'生活待办',path:summary('life-reminders'),link:'/extensions/life-reminders',icon:'✓',color:'#a2b5f4'},
  modules:{name:'模块运行概览',path:'/modules',link:'/modules',icon:'⊞',color:'#8bbec7'},
}
const metric=(label,value,unit='',total=null)=>({label,value:value??null,unit,total})
export function moduleMetrics(type,payload){
  const d=payload?.data??payload??{}, h=payload?.health??d
  switch(type){
    case 'electricity':return [metric('当月电费',d.month_cost,'元'),metric('预计可用',d.remaining_days,'天'),metric('近7日均用电',d.average_7d_kwh,'kWh'),metric('预计日均费用',d.estimated_daily_cost,'元')]
    case 'logs':return [metric('采集器运行',d.collectors_running,'台',d.hosts_total),metric('日志新鲜',d.collectors_fresh,'台',d.hosts_total),metric('Loki',typeof d.loki_ready==='boolean'?(d.loki_ready?'就绪':'未就绪'):null)]
    case 'services':return [metric('已发现服务',d.services_total,'项'),metric('快照时长',d.cache_age_seconds,'秒')]
    case 'automation':return [metric('任务记录',d.tasks,'次'),metric('正在执行',d.running,'次'),metric('失败任务',d.failed,'次')]
    case 'usage':return [metric('账户',h.account_name),metric('服务可用',typeof h.available==='boolean'?(h.available?'可用':'不可用'):null),metric('额度使用',h.credits?.usage_percent,'%'),metric('累计请求',h.usage?.total_count,'次'),...Object.entries(h.windows??{}).filter(([,w])=>w&&typeof w==='object').slice(0,2).map(([key,w])=>metric(w.name||w.label||({five_hour:'5小时',weekly:'本周',month:'本月'}[key])||key,w.usage_percent??w.used_percent??w.usedPercent,'%'))]
    case 'oss':return [metric('存储桶',Array.isArray(h.buckets)?h.buckets.length:null,'个'),metric('连接状态',typeof h.available==='boolean'?(h.available?'可用':'不可用'):null),metric('响应时间',h.latency_ms,'ms')]
    case 'opencode':return [metric('执行器',typeof d.runner_ready==='boolean'?(d.runner_ready?'就绪':'未就绪'):null),metric('版本',d.runner_version)]
    case 'sms':return [metric('今日发送',d.sent_today,'条'),metric('今日失败',d.failed_today,'条'),metric('网关',typeof d.provider_configured==='boolean'?(d.provider_configured?'已配置':'未配置'):null)]
    case 'password':return [metric('入口状态',typeof h.available==='boolean'?(h.available?'可用':'不可用'):null),metric('版本',h.version||null),metric('响应时间',h.latency_ms,'ms')]
    case 'subscriptions':return [metric('订阅总数',d.total,'项'),metric('生效订阅',d.active,'项',d.total)]
    case 'inventory':return [metric('物品',d.total,'件'),metric('位置',d.locations,'处'),metric('保修记录',d.with_warranty,'件',d.total)]
    case 'tasks':return [metric('待办总数',d.total,'项'),metric('未完成',d.open,'项',d.total),metric('已完成',Number.isFinite(d.total)&&Number.isFinite(d.open)?d.total-d.open:null,'项',d.total)]
    case 'modules':{const rows=Array.isArray(payload)?payload:payload?.items??payload?.modules;return [metric('注册模块',Array.isArray(rows)?rows.length:null,'个'),metric('运行中',Array.isArray(rows)?rows.filter(x=>x.running===true||x.state==='running'||x.status==='running').length:null,'个')]}
    default:return []
  }
}
export function moduleWarning(payload){
  const h=payload?.health
  return payload?.error||h?.error||(payload?.status==='demo'?'演示数据':payload?.status==='unavailable'?'数据源不可用':payload?.status==='degraded'?'数据源状态需关注':h?.available===false?'服务不可用':'')
}
export function metricText(value){return value==null?'—':typeof value==='number'?new Intl.NumberFormat('zh-CN',{maximumFractionDigits:2}).format(value):String(value)}
export function metricPercent(row){return Number.isFinite(row.value)&&Number.isFinite(row.total)&&row.total>0?Math.max(0,Math.min(100,row.value/row.total*100)):null}
