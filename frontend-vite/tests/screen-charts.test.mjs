import {test} from 'node:test'
import assert from 'node:assert/strict'
import {dailyAlerts,resourceTrend} from '../src/utils/screenCharts.js'

test('trend preserves zero, missing buckets, and metric percent limits',()=>{
  const at=1792576800
  const trend=resourceTrend({cpu:[[at,0],[at+30,50],[at+300,100],[at+330,null],[at+360,110]],memory:[],disk:[]},1)
  assert.equal(trend.lines[0].segments.length,2)
  assert.equal(trend.lines[0].segments[0][0].value,0)
  assert.equal(trend.lines[0].segments[1][0].value,100)
  assert.ok(trend.end>trend.start)
  assert.deepEqual(resourceTrend({},24).lines,[])
})

test('single bucket still has a finite usable time axis',()=>{
  const trend=resourceTrend({cpu:[[1792576800,20]]},1)
  assert.ok(trend.end>trend.start)
  assert.ok(Number.isFinite(trend.lines[0].segments[0][0].x))
})

test('daily events use Beijing dates, preserve zero days, and deduplicate',()=>{
  const now=Date.parse('2026-10-21T10:00:00Z')
  const data=dailyAlerts([
    {id:'a',created_at:'2026-10-20T16:00:00Z'},
    {id:'a',created_at:'2026-10-20T16:00:00Z'},
    {id:'b',created_at:'2026-10-20T15:59:59'},
    {id:'c',created_at:'invalid'},
    {id:'d',created_at:'2026-10-22T00:00:00Z'},
    {id:'old',created_at:'2026-10-01T00:00:00Z'},
  ],now)
  assert.equal(data.days.length,7)
  assert.equal(data.total,2)
  assert.equal(data.days[6].day,'2026-10-21')
  assert.equal(data.days[6].count,1)
  assert.equal(data.days[5].count,1)
  assert.equal(data.omitted,1)
  assert.equal(dailyAlerts([],now).total,0)
})
