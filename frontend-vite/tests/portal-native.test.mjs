import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import vm from 'node:vm'

test('native transport strips AbortSignal, normalizes core routes and cancels locally',async()=>{
 let listener;const messages=[];
 const source=(await readFile(new URL('../src/portal-api.js',import.meta.url),'utf8'))
  .replace("export * from './api.js'",'')
  .replace("import {apiUrl} from './api.js'","const apiUrl = path => path.startsWith('/modules/') ? path : '/api/v2'+path")
  .replaceAll('export ','')+'\nglobalThis.native={api,getAccessToken,createWebSocket};';
 const parent={postMessage:data=>messages.push(structuredClone(data))};
 const context={parent,window:{addEventListener:(type,fn)=>listener=fn},setInterval:()=>0,clearInterval(){},setTimeout,clearTimeout,DOMException,Blob};
 vm.createContext(context);vm.runInContext(source,context);
 listener({source:parent,data:{type:'ops-module-info',nonce:'nonce',module:{key:'core-hosts'}}});
 const controller=new AbortController(),pending=context.native.api.get('/servers/1',undefined,{signal:controller.signal});
 await Promise.resolve();
 const message=messages.at(-1);assert.equal(message.path,'/api/v2/servers/1');assert.equal(message.signal,undefined);
 controller.abort();await assert.rejects(pending,{name:'AbortError'});
 assert.equal(context.native.getAccessToken(),'portal-session');
 const socket=context.native.createWebSocket('/ws/terminal/abc');await Promise.resolve();
 const id=messages.at(-1).id;let opened=false;socket.onopen=()=>opened=true;
 listener({source:parent,data:{type:'ops-module-socket-event',nonce:'wrong',id,event:'open'}});assert.equal(opened,false);
 listener({source:parent,data:{type:'ops-module-socket-event',nonce:'nonce',id,event:'open'}});assert.equal(opened,true);
 socket.send('input');assert.equal(messages.at(-1).type,'ops-module-socket-send');
 socket.close();assert.equal(messages.at(-1).type,'ops-module-socket-close');
});

test('sandboxed storage retains selection without browser storage permission',async()=>{
 const source=(await readFile(new URL('../src/safeStorage.js',import.meta.url),'utf8')).replace('export const','globalThis.safeStorage =');
 const context={};Object.defineProperty(context,'localStorage',{get(){throw Error('opaque origin')}});
 vm.createContext(context);vm.runInContext(source,context);
 context.safeStorage.setItem('selected','host-2');assert.equal(context.safeStorage.getItem('selected'),'host-2');
});
