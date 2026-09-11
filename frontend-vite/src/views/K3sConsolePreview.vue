<template>
  <div class="demo-shell">
    <aside class="demo-side">
      <div class="brand"><span>O</span><div><b>运维工作台</b><small>v5.0.0 · 交互预览</small></div></div>
      <nav>
        <button v-for="item in navItems" :key="item.key" :class="{active:activeNav===item.key}" @click="openPage(item)"><i>{{ item.icon }}</i>{{ item.label }}</button>
      </nav>
      <div class="side-foot"><span class="online-dot"></span>平台运行正常</div>
    </aside>

    <section class="demo-workspace">
      <header class="demo-tabs">
        <button v-for="tab in tabs" :key="tab.id" :class="{active:activeTab===tab.id}" @click="activeTab=tab.id">
          <span>{{ tab.title }}</span><i v-if="tabs.length>1" @click.stop="closeTab(tab.id)">×</i>
        </button>
        <button class="new-tab" title="打开集群概览" @click="openPage(navItems[1])">＋</button>
        <div class="header-tools"><span>🔔</span><span>帮助</span><b>admin ▾</b></div>
      </header>

      <template v-for="tab in tabs" :key="tab.id">
        <main v-show="activeTab===tab.id" class="demo-page">
          <template v-if="tab.kind==='k3s'">
            <div class="page-toolbar"><label>当前集群<select v-model="cluster"><option>生产 K3s</option><option>灾备 K3s（演示）</option></select></label><span class="healthy"><i></i>集群健康</span><button @click="lastRefresh=now()">↻ 刷新</button></div>
            <div class="title-row"><div><h1>K3s 集群概览</h1><p>节点、工作负载、服务和存储的统一运行视图</p></div><small>数据更新 {{ lastRefresh }}</small></div>

            <div class="metric-grid">
              <article v-for="m in metrics" :key="m.label"><span>{{ m.icon }}</span><div><small>{{ m.label }}</small><b>{{ m.value }}</b><em :class="m.warn?'warn':''">{{ m.note }}</em></div></article>
            </div>

            <div class="dashboard-grid">
              <section class="panel node-panel"><div class="panel-head"><b>节点资源水位</b><small>点击节点查看详情</small></div><div class="legend"><i class="cpu"></i>CPU <i class="mem"></i>内存</div><div class="node-bars">
                <button v-for="host in hosts" :key="host.name" @click="openHost(host)"><span>{{ host.short }}</span><div class="bars"><i class="cpu" :style="{height:host.cpu+'%'}"></i><i class="mem" :style="{height:host.mem+'%'}"></i></div><small>{{ host.cpu }}% / {{ host.mem }}%</small></button>
              </div></section>
              <section class="panel"><div class="panel-head"><b>异常工作负载</b><span class="danger-badge">2</span></div><table><thead><tr><th>名称</th><th>命名空间</th><th>状态</th><th>原因</th></tr></thead><tbody><tr><td>metrics-server</td><td>kube-system</td><td><em class="amber">不健康</em></td><td>副本数不足</td></tr><tr><td>snapshot-controller</td><td>longhorn</td><td><em class="amber">告警</em></td><td>就绪副本不足</td></tr><tr><td>opscenter-backend</td><td>opscenter</td><td><em class="green">正常</em></td><td>—</td></tr></tbody></table></section>
              <section class="panel"><div class="panel-head"><b>服务双层健康</b><small>运行与访问分开判断</small></div><table><thead><tr><th>服务</th><th>内部健康</th><th>外部访问</th></tr></thead><tbody><tr v-for="s in services" :key="s.name"><td>{{ s.name }}</td><td><em class="green">● 正常</em></td><td><em :class="s.external?'green':'amber'">● {{ s.external?'正常':'告警' }}</em></td></tr></tbody></table></section>
              <section class="panel"><div class="panel-head"><b>备份与任务</b><small>最近执行结果</small></div><div class="jobs"><div><span>集群配置备份</span><em class="green">● 成功</em><small>今天 02:00</small></div><div><span>OpsCenter 数据库</span><em class="green">● 成功</em><small>今天 03:15</small></div><div><span>镜像同步任务</span><em class="blue">● 运行中</em><small>2 分钟前</small></div></div></section>
            </div>

            <section class="panel host-list"><div class="panel-head"><b>集群节点（4）</b><small>选择任意节点打开右侧详情</small></div><table><thead><tr><th>节点名称</th><th>角色</th><th>状态</th><th>CPU</th><th>内存</th><th>Pod</th><th>运行时间</th></tr></thead><tbody><tr v-for="host in hosts" :key="host.name" :class="{selected:selectedHost?.name===host.name}" @click="openHost(host)"><td><b>{{ host.name }}</b></td><td>{{ host.role }}</td><td><em class="green">● Ready</em></td><td>{{ host.cpu }}%</td><td>{{ host.mem }}%</td><td>{{ host.pods }}</td><td>{{ host.uptime }}</td></tr></tbody></table></section>
          </template>

          <template v-else-if="tab.kind==='hosts'">
            <div class="page-toolbar"><strong>主机管理</strong><div class="spacer"></div><button>＋ 添加主机</button><button @click="lastRefresh=now()">↻ 刷新</button></div>
            <div class="title-row"><div><h1>主机管理</h1><p>统一管理主机、连接通道、资源状态和 Agent</p></div></div>
            <section class="panel host-list"><table><thead><tr><th>主机</th><th>角色</th><th>管理地址</th><th>资源</th><th>Agent</th><th>服务</th></tr></thead><tbody><tr v-for="host in hosts" :key="host.name" @click="openHost(host)"><td><b>{{ host.name }}</b><small class="block">{{ host.os }}</small></td><td>{{ host.role }}</td><td>{{ host.lan }}<small class="block">WG {{ host.wg }}</small></td><td>CPU {{host.cpu}}% · MEM {{host.mem}}%</td><td><em class="green">● v2.6.2</em></td><td>{{ host.services }} 项</td></tr></tbody></table></section>
          </template>

          <template v-else>
            <div class="page-toolbar"><strong>{{ tab.title }}</strong><label v-if="tab.needsHost">当前主机<select v-model="currentHostName"><option v-for="h in hosts" :key="h.name">{{h.name}}</option></select></label></div>
            <div class="empty-preview"><span>{{ tab.icon }}</span><h1>{{ tab.title }}</h1><p>{{ tab.needsHost?'此页面需要主机上下文，因此顶栏显示主机选择器。':'此页面是全局视图，因此不显示主机选择器。' }}</p><button @click="openPage(navItems[1])">返回 K3s 集群概览</button></div>
          </template>
        </main>
      </template>
    </section>

    <div v-if="selectedHost" class="drawer-mask" @click="selectedHost=null"></div>
    <aside v-if="selectedHost" class="host-drawer">
      <header><div><h2>主机详情 · {{ selectedHost.name }}</h2><p><span class="online-dot"></span>在线 · {{ selectedHost.role }}</p></div><button @click="selectedHost=null">×</button></header>
      <div class="host-meta"><span>IP {{ selectedHost.lan }}</span><span>WG {{ selectedHost.wg }}</span><span>{{ selectedHost.os }}</span></div>
      <nav class="drawer-tabs"><button v-for="t in drawerTabs" :key="t" :class="{active:drawerTab===t}" @click="drawerTab=t">{{t}}</button></nav>
      <div v-if="drawerTab==='资源趋势'" class="drawer-body"><div class="range"><button v-for="r in ranges" :key="r" :class="{active:range===r}" @click="range=r">{{r}}</button></div><article class="trend"><div><b>CPU 使用率</b><span>当前 {{selectedHost.cpu}}% · 平均 48%</span></div><svg viewBox="0 0 420 130" role="img" aria-label="CPU 趋势"><polyline points="0,95 35,82 70,88 105,69 140,75 175,62 210,66 245,48 280,55 315,39 350,44 385,27 420,35" /></svg></article><article class="trend memory"><div><b>内存使用率</b><span>当前 {{selectedHost.mem}}% · 平均 62%</span></div><svg viewBox="0 0 420 130" role="img" aria-label="内存趋势"><polyline points="0,82 35,78 70,72 105,75 140,66 175,68 210,58 245,61 280,49 315,53 350,39 385,35 420,27" /></svg></article></div>
      <div v-else-if="drawerTab==='服务情况'" class="drawer-body"><div class="drawer-list" v-for="s in services" :key="s.name"><b>{{s.name}}</b><span class="green">● 内部正常</span><span :class="s.external?'green':'amber'">● 外部{{s.external?'正常':'告警'}}</span></div></div>
      <div v-else-if="drawerTab==='连接诊断'" class="drawer-body"><div class="connect"><b>LAN（首选）</b><em class="green">● 已连接 · 2 ms</em><small>{{selectedHost.lan}}:19100</small></div><div class="connect"><b>WireGuard（备用）</b><em class="green">● 已连接 · 8 ms</em><small>{{selectedHost.wg}}:19100</small></div><p class="hint">同站点监控使用 LAN，LAN 不可达时有界切换至 WireGuard。</p></div>
      <div v-else class="drawer-body"><div class="agent-card"><div><span class="agent-icon">A</span><p><b>OpsAgent v2.6.2</b><small>运行正常 · 最近上报 {{lastRefresh}}</small></p></div><button :disabled="checking" @click="checkAgent">{{ checking?'检查中…':agentMessage }}</button></div></div>
    </aside>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'

const now=()=>new Date().toLocaleTimeString('zh-CN',{hour12:false})
const lastRefresh=ref(now()), cluster=ref('生产 K3s'), currentHostName=ref('worker2'), activeTab=ref('k3s'), activeNav=ref('k3s')
const selectedHost=ref(null), drawerTab=ref('资源趋势'), range=ref('1小时'), checking=ref(false), agentMessage=ref('检查更新')
const navItems=[
  {key:'plaza',label:'服务广场',icon:'▦',kind:'simple'}, {key:'k3s',label:'K3s 集群',icon:'⬡',kind:'k3s'},
  {key:'hosts',label:'主机管理',icon:'▣',kind:'hosts'}, {key:'system',label:'系统监控',icon:'⚙',kind:'simple',needsHost:true},
  {key:'screen',label:'健康大屏',icon:'▤',kind:'simple'}, {key:'topology',label:'拓扑架构',icon:'⌘',kind:'simple'},
  {key:'alerts',label:'告警中心',icon:'♢',kind:'simple'}, {key:'api',label:'开放 API',icon:'⌁',kind:'simple'}, {key:'logs',label:'日志中心',icon:'≡',kind:'simple'}]
const tabs=ref([{id:'plaza',title:'服务广场',kind:'simple',icon:'▦'},{id:'k3s',title:'K3s 集群概览',kind:'k3s',icon:'⬡'},{id:'hosts',title:'主机管理',kind:'hosts',icon:'▣'}])
const metrics=[{label:'节点',value:'4/4',note:'Ready',icon:'⬡'},{label:'工作负载',value:'18/19',note:'可用',icon:'▱',warn:true},{label:'Pod',value:'42/44',note:'正常',icon:'⬢',warn:true},{label:'服务',value:'7/7',note:'可访问',icon:'⌘'},{label:'存储',value:'9/9',note:'Bound',icon:'▰'}]
const hosts=[
  {name:'master',short:'master',role:'控制平面',cpu:32,mem:48,pods:'11/12',uptime:'32 天',lan:'192.168.1.155',wg:'10.66.66.13',os:'Ubuntu 22.04',services:8},
  {name:'worker1',short:'worker1',role:'工作节点',cpu:18,mem:35,pods:'9/11',uptime:'32 天',lan:'192.168.1.157',wg:'10.66.66.14',os:'Ubuntu 22.04',services:6},
  {name:'worker2',short:'worker2',role:'工作节点',cpu:62,mem:78,pods:'11/11',uptime:'31 天',lan:'192.168.1.158',wg:'10.66.66.15',os:'Ubuntu 22.04',services:12},
  {name:'infra',short:'infra',role:'基础设施',cpu:28,mem:52,pods:'11/12',uptime:'29 天',lan:'192.168.1.159',wg:'待配置',os:'Ubuntu 22.04',services:9}]
const services=[{name:'OpsCenter',external:true},{name:'GitLab',external:true},{name:'Jenkins',external:true},{name:'Prometheus',external:false}]
const drawerTabs=['资源趋势','服务情况','连接诊断','Agent'], ranges=['1小时','6小时','24小时','7天']
const activePage=computed(()=>tabs.value.find(t=>t.id===activeTab.value))
function openPage(item){activeNav.value=item.key;let tab=tabs.value.find(t=>t.id===item.key);if(!tab){tab={id:item.key,title:item.label,kind:item.kind,icon:item.icon,needsHost:item.needsHost};tabs.value.push(tab)}activeTab.value=tab.id}
function closeTab(id){const idx=tabs.value.findIndex(t=>t.id===id);if(idx<0)return;tabs.value.splice(idx,1);if(activeTab.value===id)activeTab.value=tabs.value[Math.max(0,idx-1)]?.id||''}
function openHost(host){selectedHost.value=host;drawerTab.value='资源趋势';range.value='1小时'}
function checkAgent(){checking.value=true;agentMessage.value='检查更新';setTimeout(()=>{checking.value=false;agentMessage.value='已是最新版';lastRefresh.value=now()},900)}
</script>

<style scoped>
*{box-sizing:border-box}.demo-shell{height:100vh;min-height:720px;display:flex;background:#f3f6fb;color:#14213d;font:14px/1.45 Inter,"Microsoft YaHei",sans-serif;overflow:hidden}.demo-side{width:188px;flex:none;background:linear-gradient(180deg,#0b2446,#071a34);color:#bed0e8;padding:18px 10px;display:flex;flex-direction:column}.brand{display:flex;align-items:center;gap:10px;padding:0 8px 20px;border-bottom:1px solid #ffffff18}.brand>span{width:34px;height:34px;border-radius:9px;background:#2563eb;color:white;display:grid;place-items:center;font-weight:800}.brand b{display:block;color:white;font-size:16px}.brand small{display:block;font-size:11px;margin-top:2px}.demo-side nav{display:flex;flex-direction:column;gap:4px;margin-top:18px}.demo-side nav button{height:42px;padding:0 12px;border:0;border-radius:7px;background:transparent;color:inherit;text-align:left;cursor:pointer;font:inherit}.demo-side nav button i{font-style:normal;display:inline-block;width:28px}.demo-side nav button:hover,.demo-side nav button.active{background:#1768e9;color:white}.side-foot{margin-top:auto;padding:14px 8px 2px;border-top:1px solid #ffffff18;font-size:12px}.online-dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#18b96b;margin-right:6px}.demo-workspace{min-width:0;flex:1;display:flex;flex-direction:column}.demo-tabs{height:48px;display:flex;align-items:stretch;background:#eaf0f8;border-bottom:1px solid #d5dfed}.demo-tabs>button{min-width:132px;max-width:210px;padding:0 13px;border:0;border-right:1px solid #d5dfed;background:#eef3f9;color:#344663;display:flex;align-items:center;justify-content:space-between;gap:10px;cursor:pointer}.demo-tabs>button.active{background:white;color:#1768e9;box-shadow:inset 0 -2px #1768e9}.demo-tabs>button i{font-style:normal;color:#8b9bb0;font-size:18px}.demo-tabs .new-tab{min-width:46px;max-width:46px;font-size:20px;justify-content:center}.header-tools{margin-left:auto;display:flex;align-items:center;gap:18px;padding:0 18px;color:#44536c}.demo-page{flex:1;min-height:0;overflow:auto;padding:16px 18px 24px}.page-toolbar{height:46px;display:flex;align-items:center;gap:14px;background:white;border:1px solid #dce4ef;border-radius:8px;padding:6px 10px}.page-toolbar label{display:flex;align-items:center;gap:8px;font-weight:600}.page-toolbar select{min-width:180px;border:1px solid #cfd9e7;border-radius:6px;padding:7px 10px;background:white}.page-toolbar button,.empty-preview button,.agent-card button{border:1px solid #c8d5e7;border-radius:6px;background:white;color:#2459a8;padding:7px 13px;cursor:pointer}.page-toolbar button:hover,.agent-card button:hover{border-color:#1768e9}.healthy{margin-left:auto}.healthy i{display:inline-block;width:8px;height:8px;background:#18b96b;border-radius:50%;margin-right:6px}.spacer{flex:1}.title-row{display:flex;justify-content:space-between;align-items:flex-end;margin:16px 0 12px}.title-row h1{margin:0;font-size:24px}.title-row p{margin:4px 0 0;color:#78879b}.title-row small{color:#78879b}.metric-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}.metric-grid article{background:white;border:1px solid #dce4ef;border-radius:9px;padding:15px;display:flex;gap:12px;align-items:center}.metric-grid article>span{width:38px;height:38px;border-radius:9px;background:#e8f1ff;color:#1768e9;display:grid;place-items:center;font-size:19px}.metric-grid small,.metric-grid b,.metric-grid em{display:block}.metric-grid b{font-size:21px;margin:2px 0}.metric-grid em{font-style:normal;color:#18a660;font-size:12px}.metric-grid em.warn{color:#d98a13}.dashboard-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px}.panel{background:white;border:1px solid #dce4ef;border-radius:9px;padding:15px;min-width:0}.panel-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}.panel-head small{color:#8997aa}.danger-badge{background:#e84b57;color:white;border-radius:12px;padding:1px 7px;font-size:11px}.legend{font-size:12px;color:#718197}.legend i{display:inline-block;width:8px;height:8px;border-radius:2px;margin:0 4px 0 12px}.legend i:first-child{margin-left:0}.cpu{background:#2878f0}.mem{background:#2bb673}.node-bars{height:155px;display:flex;align-items:flex-end;justify-content:space-around;border-bottom:1px solid #dce4ef;padding:8px 12px 0}.node-bars button{border:0;background:none;cursor:pointer;color:inherit}.node-bars .bars{height:105px;display:flex;gap:5px;align-items:flex-end;justify-content:center}.node-bars .bars i{display:block;width:18px;border-radius:3px 3px 0 0}.node-bars span,.node-bars small{display:block}.node-bars small{font-size:11px;color:#7c8a9d;margin-top:5px}table{width:100%;border-collapse:collapse;text-align:left}th{background:#f3f6fa;color:#6d7c91;font-weight:500}th,td{padding:9px 10px;border-bottom:1px solid #e6ebf2;font-size:12px}tbody tr{cursor:pointer}tbody tr:hover,tbody tr.selected{background:#edf5ff}em{font-style:normal}.green{color:#12a35c}.amber{color:#d7870c}.blue{color:#1768e9}.jobs>div,.drawer-list,.connect{display:grid;grid-template-columns:1fr auto auto;gap:12px;align-items:center;padding:11px 2px;border-bottom:1px solid #e6ebf2}.jobs small,.block{display:block;color:#8290a3}.host-list{margin-top:10px}.empty-preview{height:calc(100% - 46px);display:grid;place-content:center;text-align:center;color:#73829a}.empty-preview>span{font-size:52px}.empty-preview h1{margin:8px 0}.empty-preview p{margin:0 0 18px}.drawer-mask{position:fixed;inset:0;background:#0d1a2b33;z-index:20}.host-drawer{position:fixed;right:0;top:0;bottom:0;width:min(470px,44vw);background:white;z-index:21;box-shadow:-10px 0 30px #162b4820;overflow:auto}.host-drawer>header{height:82px;padding:16px 18px;border-bottom:1px solid #dfe6ef;display:flex;justify-content:space-between}.host-drawer h2{margin:0;font-size:18px}.host-drawer header p{margin:5px 0;color:#708097}.host-drawer header button{border:0;background:none;font-size:24px;cursor:pointer}.host-meta{padding:12px 18px;display:flex;gap:8px;flex-wrap:wrap}.host-meta span{background:#f0f4f9;padding:5px 8px;border-radius:5px;font-size:12px}.drawer-tabs{display:flex;border-bottom:1px solid #dfe6ef;padding:0 14px}.drawer-tabs button{flex:1;border:0;border-bottom:2px solid transparent;background:white;padding:12px 4px;color:#51627a;cursor:pointer}.drawer-tabs button.active{color:#1768e9;border-color:#1768e9}.drawer-body{padding:14px 18px}.range{display:flex;gap:7px}.range button{flex:1;padding:8px;border:1px solid #ccd7e5;background:white;border-radius:5px;cursor:pointer}.range button.active{background:#1768e9;color:white;border-color:#1768e9}.trend{margin-top:12px;border:1px solid #dfe6ef;border-radius:8px;padding:12px}.trend>div{display:flex;justify-content:space-between}.trend span{color:#718197;font-size:12px}.trend svg{width:100%;height:130px;margin-top:8px;background:repeating-linear-gradient(#fff,#fff 31px,#e9eef5 32px)}.trend polyline{fill:none;stroke:#2878f0;stroke-width:3}.trend.memory polyline{stroke:#2bb673}.connect{grid-template-columns:1fr auto}.connect small{grid-column:1/-1;color:#8190a4}.hint{background:#edf5ff;color:#446486;padding:10px;border-radius:6px}.agent-card{border:1px solid #dfe6ef;border-radius:8px;padding:16px}.agent-card>div{display:flex;align-items:center}.agent-card p{margin:0}.agent-card small{display:block;color:#8190a4;margin-top:4px}.agent-icon{width:38px;height:38px;border-radius:9px;background:#1768e9;color:white;display:grid;place-items:center;font-weight:700;margin-right:10px}.agent-card button{width:100%;margin-top:16px;background:#1768e9;color:white}.agent-card button:disabled{opacity:.6}@media(max-width:1000px){.metric-grid{grid-template-columns:repeat(3,1fr)}.dashboard-grid{grid-template-columns:1fr}.host-drawer{width:min(470px,70vw)}}@media(max-width:720px){.demo-side{width:58px}.brand div,.demo-side nav button:not(.active){font-size:0}.brand{padding-left:2px}.demo-side nav button{padding:0 9px}.demo-side nav button i{font-size:18px}.side-foot{display:none}.metric-grid{grid-template-columns:1fr 1fr}.header-tools{display:none}.host-drawer{width:92vw}.demo-page{padding:10px}.page-toolbar .healthy{display:none}}
</style>
