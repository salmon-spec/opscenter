import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { newRegistrationId } from '../src/utils/uuid.js'

test('registration IDs work without the secure-context randomUUID API', () => {
  assert.match(newRegistrationId(), /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
})

test('host creation submits automatic Agent installation', async () => {
  const editor = await readFile(new URL('../src/components/host/HostEditorDrawer.vue', import.meta.url), 'utf8')
  assert.match(editor, /api\.post\('\/modules\/host-inventory\/api\/v1\/hosts',\s*\{\s*auto_deploy_agent: true/)
  assert.match(editor, /主机已添加，正在自动安装 Agent/)
  const legacy = await readFile(new URL('../src/components/HostManagerDrawer.vue', import.meta.url), 'utf8')
  assert.match(legacy, /auto_deploy_agent:true/)
})

test('unavailable response keeps the last host list, selection and persisted cache', async () => {
  const values = new Map()
  globalThis.localStorage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) }
  let snapshot = { status: 'ok', items: [{ id: 'one', name: 'node', status: 'online' }] }
  globalThis.auditApi = { get: async () => snapshot }
  const code = (await readFile(new URL('../src/hostContext.js', import.meta.url), 'utf8'))
    .replace("import { computed, ref } from 'vue'", 'const ref = value => ({value}); const computed = fn => ({get value(){return fn()}})')
    .replace("import { api } from './api'", 'const api = globalThis.auditApi')
  const { useHostContext } = await import(`data:text/javascript,${encodeURIComponent(code)}`)
  const state = useHostContext()
  await state.refreshHosts(true)
  const previous = values.get('ops-host-cache-v1')
  snapshot = { status: 'unavailable', items: [], error: 'temporary failure' }
  await state.refreshHosts(true)
  assert.equal(state.hosts.value.length, 1)
  assert.equal(state.selectedHostId.value, 'one')
  assert.equal(values.get('ops-host-cache-v1'), previous)
  assert.equal(state.refreshError.value, 'temporary failure')
  snapshot = { status: 'ok', items: [] }
  await state.refreshHosts(true)
  assert.equal(state.hosts.value.length, 0)
})
