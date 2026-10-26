import assert from 'node:assert/strict'
import test from 'node:test'
import { webcrypto } from 'node:crypto'
import { formFields, normalizeForm, operationId } from '../src/utils/life.js'

test('editing a task preserves its date, recurrence and revision',()=>{
  const form=normalizeForm('life-reminders',{title:'测试',project_id:2,priority:3,due_date:'2027-01-01T01:00:00Z',workbench_repeat:'yearly',updated:'rev'}, {projects:[]})
  assert.equal(form.repeat,'yearly');assert.equal(form.expected_updated,'rev');assert.ok(form.due_date);assert.equal(form.priority,3)
})
test('a homebox entity form retains parent and tag identifiers',()=>{
  const form=normalizeForm('home-inventory',{name:'家电',entityType:{id:'type'},parent:{id:'room'},tags:[{id:'tag'}],updatedAt:'rev'}, {types:[]})
  assert.equal(form.entityTypeId,'type');assert.equal(form.parentId,'room');assert.deepEqual(form.tagIds,['tag']);assert.equal(form.expected_updated,'rev')
})
test('operation ids work without HTTPS-only randomUUID',()=>{
  Object.defineProperty(globalThis,'crypto',{value:{getRandomValues:(x)=>webcrypto.getRandomValues(x)},configurable:true})
  assert.match(operationId(),/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
  assert.notEqual(operationId(),operationId())
})
