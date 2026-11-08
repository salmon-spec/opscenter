import {test} from 'node:test'
import assert from 'node:assert/strict'
import {newWidget,applyLayout,themes,themeVariables,boardTemplates,templateConfig,contrastRatio,readableAccent} from '../src/utils/screenBoards.js'
import {moduleMetrics,moduleWarning,metricPercent,moduleWidgets} from '../src/utils/screenModules.js'

test('adding components never overlaps existing geometry and preserves content during drag',()=>{
  const first=newWidget('hosts',[]),second=newWidget('charts',[first])
  assert.ok(second.y>=first.y+first.h)
  assert.equal(second.w,12)
  assert.notEqual(first.id,second.id)
  const moved=applyLayout([first,second],[{id:first.id,x:2,y:4,w:6,h:6}])
  assert.equal(moved[0].title,first.title)
  assert.equal(moved[0].x,2)
  assert.deepEqual(moved[1],second)
  assert.equal(first.x,0)
})
test('theme fields map to scoped variables',()=>{
  const variables=themeVariables({...themes.light,font_size:16,radius:8})
  assert.equal(variables['--surface'],'#ffffff')
  assert.equal(variables['--board-font-size'],'16px')
  assert.equal(variables['--board-radius'],'8px')
})

test('widget IDs work on HTTP origins without randomUUID',()=>{
  const original=crypto.randomUUID
  try{crypto.randomUUID=undefined;const widget=newWidget('text',[]);assert.match(widget.id,/^widget-[a-f0-9]{32}$/)}
  finally{crypto.randomUUID=original}
})

test('scene templates cover every business module without overlapping',()=>{
  const covered=new Set()
  for(const template of boardTemplates){
    const config=templateConfig(template,{theme:{font_size:13,radius:10}})
    assert.ok(config.theme.style)
    assert.equal(config.theme.name,undefined)
    for(const a of config.widgets){
      covered.add(a.type)
      assert.ok(a.x+a.w<=12)
      assert.equal(config.widgets.filter(b=>a!==b&&a.x<b.x+b.w&&b.x<a.x+a.w&&a.y<b.y+b.h&&b.y<a.y+a.h).length,0)
    }
  }
  for(const type of Object.keys(moduleWidgets))assert.ok(covered.has(type),type)
})
test('module adapters preserve zero, missing values and distinct units',()=>{
  assert.equal(moduleMetrics('electricity',{month_cost:0})[0].value,0)
  assert.equal(moduleMetrics('electricity',{})[0].value,null)
  assert.equal(moduleMetrics('tasks',{data:{total:4,open:1}})[2].value,3)
  assert.equal(moduleMetrics('tasks',{})[2].value,null)
  assert.equal(moduleMetrics('modules',{items:[{running:true},{running:false}]})[1].value,1)
  assert.equal(moduleMetrics('usage',{health:{windows:{limited:true,five_hour:{usage_percent:17}},credits:{usage_percent:0}}}).at(-1).value,17)
  assert.equal(metricPercent({value:0,total:9}),0)
  assert.equal(metricPercent({value:0,total:0}),null)
  assert.equal(moduleWarning({health:{available:false,error:'未配置'}}),'未配置')
  assert.ok(moduleWarning({status:'demo'}))
})
test('business accents remain readable on every preset surface',()=>{
  for(const theme of Object.values(themes)){
    for(const module of Object.values(moduleWidgets))assert.ok(contrastRatio(readableAccent(module.color,theme.surface),theme.surface)>=4.5)
    const variables=themeVariables(theme)
    for(const key of ['--blue','--violet','--amber','--red'])assert.ok(contrastRatio(variables[key],theme.surface)>=4.5)
  }
  assert.equal(readableAccent('#ffffff','#000000'),'#ffffff')
  assert.ok(contrastRatio(readableAccent('#ffffff','#ffffff'),'#ffffff')>=4.5)
})
