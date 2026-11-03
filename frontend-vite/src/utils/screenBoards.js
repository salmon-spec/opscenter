export const widgetLabels = {overview:'状态总览',hosts:'主机资源',cluster:'K3s 集群',alerts:'告警队列',charts:'监控图表组',text:'文字说明'}
export const themes = {
  graphite: {name:'石墨灰',background:'#111217',surface:'#1b1c22',text:'#e8e9ed',accent:'#79c99e'},
  ocean: {name:'深海蓝',background:'#0b1526',surface:'#12243b',text:'#e2edf8',accent:'#56c9e6'},
  light: {name:'简洁明亮',background:'#edf1f5',surface:'#ffffff',text:'#202b3c',accent:'#167352'},
}
export const clone = value => JSON.parse(JSON.stringify(value))
export function themeVariables(theme) {
  return {'--bg':theme.background,'--surface':theme.surface,'--text':theme.text,'--green':theme.accent,
    '--board-font-size':`${theme.font_size}px`,'--board-radius':`${theme.radius}px`,
    '--muted':`color-mix(in srgb, ${theme.text} 68%, ${theme.surface})`,
    '--line':`color-mix(in srgb, ${theme.text} 18%, ${theme.surface})`}
}
export function newWidget(type, widgets) {
  const y = Math.max(0,...widgets.map(w=>w.y+w.h))
  return {id:crypto.randomUUID?.() || `widget-${Array.from(crypto.getRandomValues(new Uint8Array(16)),n=>n.toString(16).padStart(2,'0')).join('')}`,type,title:widgetLabels[type],text:'',x:0,y,w:type==='overview'||type==='charts'?12:6,h:type==='charts'?12:type==='overview'?2:6}
}
export function applyLayout(widgets, layout) {
  const positions = new Map(layout.map(w=>[w.id,w]))
  return widgets.map(w=>({...w,...positions.get(w.id)}))
}
