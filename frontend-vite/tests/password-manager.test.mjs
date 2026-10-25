import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { runInNewContext } from 'node:vm'
import test from 'node:test'

const source = await readFile(new URL('../src/views/PasswordManager.vue', import.meta.url), 'utf8')
const script = source.split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm, '')
function page(window, get) {
  return runInNewContext(script + '\n;({ load, health, error, loading, canEmbed, workspaceUrl })', {
    URL, window, ref: (value) => ({ value }), computed: (fn) => ({ get value() { return fn() } }), onMounted() {}, api: { get },
  })
}
const health = { available: true, url: 'https://vault.example', workbench_origin: 'https://workbench.example' }

test('the vault runs inside an isolated frame in the secure workbench', async () => {
  const context = page({ isSecureContext: true, location: { origin: 'https://workbench.example' } }, async () => ({ health }))
  await context.load()
  assert.equal(context.canEmbed.value, true)
  assert.match(source, /<iframe v-if="canEmbed"/)
  assert.match(source, /sandbox="allow-scripts allow-same-origin allow-forms allow-downloads allow-modals"/)
  assert.doesNotMatch(source, /target="_blank"/)
})

test('an insecure or unauthorized workbench cannot initialize the vault', async () => {
  for (const window of [
    { isSecureContext: false, location: { origin: 'http://workbench.example' } },
    { isSecureContext: true, location: { origin: 'https://different.example' } },
    { isSecureContext: true, location: { origin: 'https://vault.example' } },
  ]) {
    const context = page(window, async () => ({ health }))
    await context.load()
    assert.equal(context.canEmbed.value, false)
    assert.equal(context.workspaceUrl.value, 'https://workbench.example/v3/#/extensions/password-manager')
  }
})

test('a failed refresh removes the active vault frame', async () => {
  let response = { health }
  const context = page({ isSecureContext: true, location: { origin: 'https://workbench.example' } }, async (path) => {
    assert.equal(path, '/modules/password-manager/status')
    if (response instanceof Error) throw response
    return response
  })
  await context.load()
  assert.equal(context.canEmbed.value, true)
  response = Object.assign(new Error('hidden'), { status: 409 })
  await context.load()
  assert.equal(context.canEmbed.value, false)
  assert.match(context.error.value, /已隐藏/)
  assert.equal(context.loading.value, false)
})
