import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import vm from 'node:vm'

async function navigation(){
 const source=await readFile(new URL('../../modules/ops_portal/static/app.js',import.meta.url),'utf8');
 const code=source.slice(source.indexOf('function openRegisteredModule'),source.indexOf('function validateApp'));
 const opened=[],notices=[];
 const context={URL,apps:()=>[
  {id:'overview',module_key:'core-overview',url:'http://portal/host/core-overview/ui/',aliases:['http://workbench:30088/']},
  {id:'hosts',module_key:'core-hosts',url:'http://portal/host/core-hosts/ui/',aliases:['http://workbench:30088/#/hosts']},
  {id:'automation',module_key:'automation',url:'http://portal/host/automation/ui/'},
  {id:'forgejo',url:'http://portal:30094/',aliases:['http://upstream:8082/']},
 ],openApp:id=>opened.push(id),openManagement:()=>opened.push('management'),toast:message=>notices.push(message)};
 vm.createContext(context);vm.runInContext(code,context);return {context,opened,notices};
}

test('module manager opens core business entries and portal administration',async()=>{
 const {context,opened,notices}=await navigation();
 for(const key of ['host-inventory','automation','ops-portal','missing'])context.openRegisteredModule(key);
 assert.deepEqual(opened,['hosts','automation','management']);assert.equal(notices.length,1);
});

test('service links use configured portal entries and reject unregistered destinations',async()=>{
 const {context,opened}=await navigation();
 for(const url of ['http://workbench:30088/#/hosts','http://upstream:8082/','http://unknown/','javascript:alert(1)','http://user:password@upstream:8082/'])context.openCatalogueUrl(url);
 assert.deepEqual(opened,['hosts','forgejo']);
});
