import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const values = new Map()
const events = []
globalThis.localStorage = {
  getItem: (key) => values.get(key) ?? null,
  setItem: (key, value) => values.set(key, value),
  removeItem: (key) => values.delete(key),
}
globalThis.window = { dispatchEvent: (event) => events.push(event.type) }
globalThis.CustomEvent = class { constructor(type) { this.type = type } }

const source = (await readFile(new URL('../src/api.js', import.meta.url), 'utf8'))
  .replace("import.meta.env.VITE_API_BASE || ''", "''")
const { api, getAccessToken, setAccessToken } = await import(`data:text/javascript,${encodeURIComponent(source)}`)

test('a module 401 cannot log out a valid global session', async () => {
  values.clear()
  events.length = 0
  setAccessToken('valid')
  globalThis.fetch = async (url) => ({
    status: url.endsWith('/auth/me') ? 200 : 401,
    ok: false,
    json: async () => ({}),
  })
  await assert.rejects(api.get('/modules/example/api/v1/data'), { status: 401 })
  assert.equal(getAccessToken(), 'valid')
  assert.deepEqual(events, [])
})

test('an expired global session is cleared once', async () => {
  values.clear()
  events.length = 0
  setAccessToken('expired')
  globalThis.fetch = async () => ({ status: 401, ok: false, json: async () => ({}) })
  await assert.rejects(api.get('/modules/example/api/v1/data'), { status: 401 })
  assert.equal(getAccessToken(), '')
  assert.deepEqual(events, ['ops-auth-required'])
})

test('an old 401 cannot clear a newly issued login token', async () => {
  values.clear()
  events.length = 0
  setAccessToken('old')
  let resolveRequest
  globalThis.fetch = () => new Promise((resolve) => { resolveRequest = resolve })
  const pending = api.get('/modules/example/api/v1/data')
  setAccessToken('new')
  resolveRequest({ status: 401, ok: false, json: async () => ({}) })
  await assert.rejects(pending, { status: 401 })
  assert.equal(getAccessToken(), 'new')
  assert.deepEqual(events, [])
})
