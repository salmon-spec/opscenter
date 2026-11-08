import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {portalModuleForRoute} from '../src/portal-navigation.js'
import {moduleNavigation} from '../src/workbench/moduleNavigation.js'
const catalog=JSON.parse(await readFile(new URL('../../modules/ops_portal/native_catalog.json',import.meta.url),'utf8'))
const seed=JSON.parse(await readFile(new URL('../../modules/ops_portal/seed.json',import.meta.url),'utf8'))
test('the original system functions resolve to one module and one catalogue entry',()=>{
 const paths=moduleNavigation.find(m=>m.paths).groups.flatMap(g=>g.items.map(i=>i.path))
 assert.equal(paths.length,7)
 for(const path of paths)assert.equal(portalModuleForRoute(path,catalog),'core-monitor',path)
 assert.deepEqual(seed.apps.filter(a=>a.module_key==='core-monitor').map(a=>a.name),['系统应用'])
 for(const key of ['core-files','core-database','core-terminal','core-processes','core-ssh','core-firewall']){
  assert.ok(!catalog.some(m=>m.key===key));assert.ok(!seed.apps.some(a=>a.module_key===key))
 }
})
test('cluster functions preserve the original groups in one module',()=>{
 for(const group of moduleNavigation.find(m=>m.prefix).groups)for(const item of group.items)assert.equal(portalModuleForRoute(item.path,catalog),'core-kubernetes')
 assert.equal(catalog.length,9)
 assert.equal(portalModuleForRoute('/extensions/automation',catalog),'automation')
})
