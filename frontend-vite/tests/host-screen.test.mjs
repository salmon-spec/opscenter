import { test } from 'node:test'
import assert from 'node:assert/strict'
import { hostView, sortHosts, ageSeconds } from '../src/utils/hostScreen.js'

const now = Date.parse('2026-10-20T07:00:00Z')
const host = { id:'one',name:'正常',status:'online',agent_status:'running',agent_version:'2.6.4',last_seen:'2026-10-20T06:59:55Z' }
const samples = Object.fromEntries(['cpu','memory','disk'].map(key => [key,{latest:0,last_at:'2026-10-20T06:59:55Z'}]))
test('zero is a real sample; missing/null is not zero',()=>{
  assert.equal(hostView(host,samples,'2.6.4',now).level,'healthy')
  assert.equal(hostView(host,{...samples,cpu:{latest:null,last_at:host.last_seen}},'2.6.4',now).metricsFresh,false)
})
test('offline host never becomes healthy from recent metrics',()=>{
  assert.equal(hostView({...host,status:'offline'},samples,'2.6.4',now).level,'danger')
})
test('timestamps without zone are UTC and old heartbeats become unknown',()=>{
  assert.equal(ageSeconds('2026-10-20 06:59:55',now),5)
  assert.equal(hostView({...host,last_seen:'2026-10-20T06:50:00Z'},samples,'2.6.4',now).online,false)
  assert.equal(ageSeconds('2026-10-21T07:00:00Z',now),null)
})
test('old or partial metrics and failed refreshes cannot remain green',()=>{
  assert.equal(hostView(host,{...samples,disk:{latest:20,last_at:'2026-10-20T06:58:00Z'}},'2.6.4',now).level,'warning')
  assert.equal(hostView(host,samples,'2.6.4',now,false).level,'unknown')
  assert.equal(hostView(host,samples,'2.6.4',now,true,false).metricsFresh,false)
})
test('version mismatch is visible, with offline hosts sorted first',()=>{
  const older=hostView({...host,name:'版本旧',agent_version:'2.6.3'},samples,'2.6.4',now)
  const offline=hostView({...host,name:'离线',status:'offline'},samples,'2.6.4',now)
  assert.equal(older.versionOk,false)
  assert.equal(sortHosts([hostView(host,samples,'2.6.4',now),older,offline])[0].name,'离线')
  const critical=hostView({...host,name:'过载'},{...samples,cpu:{latest:99,last_at:host.last_seen}},'2.6.4',now)
  assert.equal(sortHosts([critical,offline])[0].name,'离线')
})
