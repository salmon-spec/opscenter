import { test } from 'node:test'
import assert from 'node:assert/strict'
import { sourceState, orderFindings, podSegments } from '../src/utils/monitorScreen.js'

const now = Date.parse('2026-10-21T10:00:00Z')
const snapshot = { value: { status: 'ok', collected_at: '2026-10-21T09:59:30Z', stale_after_seconds: 120 }, error: '' }

test('missing, failed, stale and collector failure snapshots never report healthy', () => {
  assert.equal(sourceState({value:null,error:''},now).usable,false)
  assert.equal(sourceState(snapshot,now).usable,true)
  assert.equal(sourceState({...snapshot,error:'HTTP 502'},now).usable,false)
  assert.equal(sourceState(snapshot,now+121000).usable,false)
  for (const patch of [{collected_at:null},{status:'unavailable'},{error:'保留上次采集'}]) {
    assert.equal(sourceState({...snapshot,value:{...snapshot.value,...patch}},now).usable,false)
  }
  assert.equal(sourceState({...snapshot,value:{...snapshot.value,status:'degraded'}},now).level,'warning')
})

test('alerts prioritize severity then time, deduplicate IDs, and preserve input', () => {
  const items=[{id:'a',severity:'warning',observed_at:'2026-10-21T09:59:00Z'},
    {id:'b',severity:'critical',observed_at:'2026-10-21T09:00:00Z'},
    {id:'c',severity:'warning',observed_at:'2026-10-21T09:58:00Z'}]
  assert.deepEqual(orderFindings([...items,items[0]]).map(i=>i.id),['b','a','c'])
  assert.equal(items[0].id,'a')
})

test('pod remainder is other phases, never fabricated Pending or healthy', () => {
  assert.deepEqual(podSegments({pods:{total:10,running:7,failed:1}}).map(i=>i.value),[7,1,2])
  assert.equal(podSegments({pods:{total:0,running:0,failed:0}}).length,3)
  for (const pods of [{total:5,running:7,failed:0},{total:5,running:2},{total:5,running:-1,failed:0}]) {
    assert.deepEqual(podSegments({pods}),[])
  }
})
