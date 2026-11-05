import {moduleWidgets} from './screenModules.js'
export const widgetLabels = {overview:'状态总览',hosts:'主机资源',cluster:'K3s 集群',alerts:'告警队列',charts:'监控图表组',text:'文字说明',...Object.fromEntries(Object.entries(moduleWidgets).map(([key,value])=>[key,value.name]))}
export const panelStyles={classic:'经典面板',gradient:'流彩渐变',glass:'通透玻璃',neon:'科技光线'}
export const widgetVariants={detail:'详细 · 仪表',cards:'彩色卡片',compact:'紧凑列表'}
export const themes = {
  graphite: {style:'classic',name:'石墨灰',background:'#111217',surface:'#1b1c22',text:'#e8e9ed',accent:'#79c99e'},
  ocean: {style:'classic',name:'深海蓝',background:'#0b1526',surface:'#12243b',text:'#e2edf8',accent:'#56c9e6'},
  light: {style:'classic',name:'简洁明亮',background:'#edf1f5',surface:'#ffffff',text:'#202b3c',accent:'#167352'},
  aurora: {name:'极光紫',background:'#110e26',surface:'#211b3c',text:'#f2edff',accent:'#bb9cff',style:'gradient'},
  emerald: {name:'翡翠森林',background:'#061c1a',surface:'#102e29',text:'#e4fff4',accent:'#39dcad',style:'glass'},
  sunset: {name:'落日珊瑚',background:'#261321',surface:'#3c2233',text:'#fff0e8',accent:'#ff997c',style:'gradient'},
  cyber: {name:'赛博青蓝',background:'#061322',surface:'#0c243b',text:'#e1f7ff',accent:'#34d5f5',style:'neon'},
  ivory: {name:'暖白橙金',background:'#f5f0e7',surface:'#fffcf5',text:'#403326',accent:'#a65014',style:'classic'},
}
export const clone = value => JSON.parse(JSON.stringify(value))
export function themeVariables(theme) {
  return {'--bg':theme.background,'--surface':theme.surface,'--text':theme.text,'--green':theme.accent,
    '--board-font-size':`${theme.font_size}px`,'--board-radius':`${theme.radius}px`,
    '--muted':`color-mix(in srgb, ${theme.text} 68%, ${theme.surface})`,
    '--line':`color-mix(in srgb, ${theme.text} 18%, ${theme.surface})`,
    '--blue':theme.background.startsWith('#f')||theme.background==='#edf1f5'?'#2465b5':'#64aaff',
    '--violet':theme.background.startsWith('#f')||theme.background==='#edf1f5'?'#8750b1':'#bc9aff'}
}
export function newWidget(type, widgets) {
  const y = Math.max(0,...widgets.map(w=>w.y+w.h))
  return {id:crypto.randomUUID?.() || `widget-${Array.from(crypto.getRandomValues(new Uint8Array(16)),n=>n.toString(16).padStart(2,'0')).join('')}`,type,title:widgetLabels[type],text:'',variant:'detail',accent:null,x:0,y,w:type==='overview'||type==='charts'?12:6,h:type==='charts'?12:type==='overview'?2:6}
}
export function applyLayout(widgets, layout) {
  const positions = new Map(layout.map(w=>[w.id,w]))
  return widgets.map(w=>({...w,...positions.get(w.id)}))
}

export const boardTemplates=[
  {key:'operations',name:'运维指挥中心',description:'主机、集群、告警与日志',theme:'cyber',types:['overview','hosts','cluster','alerts','logs','automation']},
  {key:'business',name:'业务服务全景',description:'服务、自动化、模型与对象存储',theme:'aurora',types:['services','automation','usage','oss','opencode','modules']},
  {key:'life',name:'生活与能源',description:'电费、订阅、保修与待办',theme:'ivory',types:['electricity','subscriptions','inventory','tasks','sms','password']},
  {key:'all',name:'全模块总览',description:'运维与生活模块同屏展示',theme:'emerald',types:['overview','logs','services','automation','usage','oss','opencode','electricity','subscriptions','inventory','tasks','sms','password','modules']},
]
export function templateConfig(template, builtin){
  const config=clone(builtin);config.title=template.name;config.theme={...config.theme,...themes[template.theme]};delete config.theme.name
  let y=0,x=0
  config.widgets=template.types.map(type=>{const w=newWidget(type,[]);w.variant='cards';w.h=type==='overview'?3:5;w.x=x;w.y=y;if(w.w===12){x=0;y+=w.h}else if(x===0)x=6;else{x=0;y+=w.h}return w})
  return config
}
