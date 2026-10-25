import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { runInNewContext } from 'node:vm'
import test from 'node:test'

const source = await readFile(new URL('../src/views/PasswordManager.vue', import.meta.url), 'utf8')
const script = source.split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')

test('password manager refresh clears an old working link when the probe fails', async () => {
  let response = { health: { available: true, url: 'https://vault.example' } }
  const context = runInNewContext(script + '\n;({ load, health, error, loading })', {
    ref: (value) => ({ value }), onMounted() {}, api: { get: async (path) => {
      assert.equal(path, '/modules/password-manager/status')
      if (response instanceof Error) throw response
      return response
    } },
  })
  await context.load()
  assert.equal(context.health.value.available, true)
  response = Object.assign(new Error('hidden'), { status: 409 })
  await context.load()
  assert.equal(context.health.value.url, undefined)
  assert.match(context.error.value, /已隐藏/)
  assert.equal(context.loading.value, false)
  assert.match(source, /v-if="health.available && health.url"/)
  assert.match(source, /target="_blank" rel="noopener noreferrer"/)
})
