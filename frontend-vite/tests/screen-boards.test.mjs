import {test} from 'node:test'
import assert from 'node:assert/strict'
import {newWidget,applyLayout,themes,themeVariables} from '../src/utils/screenBoards.js'

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
