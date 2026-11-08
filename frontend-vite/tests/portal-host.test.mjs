import test from 'node:test'
import assert from 'node:assert/strict'
import {createModuleHost} from '../../modules/ops_portal/static/host.js'

test('portal host keeps the initial handshake valid across document load and scopes its credential',async()=>{
 let listener, onload;const messages=[],calls=[];
 globalThis.window={addEventListener:(type,handler)=>{listener=handler}};
 globalThis.fetch=async(url,options)=>{calls.push({url,options});return {ok:true,status:200,json:async()=>({items:[1]})}};
 const child={postMessage:data=>messages.push(data)};
 const item={app:{id:'test'},module:{key:'automation',manifest:{}},frame:{contentWindow:child,addEventListener:(type,handler)=>{onload=handler}}};
 const frames=new Map([['test',item]]);
 createModuleHost({token:()=> 'private-operator-token',frames,expired:()=>assert.fail('unexpected logout')}).attach(item);
 await listener({source:child,data:{type:'ops-module-ready'}});
 assert.equal(item.ready,true);
 const initial=messages.at(-1).nonce;
 onload();assert.equal(messages.at(-1).nonce,initial);
 await listener({source:child,data:{type:'ops-module-api-request',nonce:initial,id:'first',path:'/modules/automation/api/v1/state'}});
 assert.equal(calls.length,1);assert.equal(calls[0].options.headers.Authorization,'Bearer private-operator-token');
 assert.deepEqual(messages.at(-1).data,{items:[1]});
 assert.ok(!JSON.stringify(messages).includes('private-operator-token'));
 await listener({source:{},data:{type:'ops-module-api-request',nonce:initial,id:'foreign'}});
 await listener({source:child,data:{type:'ops-module-api-request',nonce:'invalid',id:'stale'}});
 assert.equal(calls.length,1);
});

test('portal upload keeps the file and idempotency key on its authenticated transport',async()=>{
 let listener;const messages=[],calls=[];
 globalThis.window={addEventListener:(type,handler)=>{listener=handler}};
 globalThis.fetch=async(url,options)=>{calls.push({url,options});return {ok:true,status:200,json:async()=>({ok:true})}};
 const child={postMessage:data=>messages.push(data)};
 const item={app:{id:'inventory'},module:{key:'home-inventory'},frame:{contentWindow:child,addEventListener(){}}};
 createModuleHost({token:()=> 'secret',frames:new Map([['inventory',item]]),expired(){}}).attach(item);
 await listener({source:child,data:{type:'ops-module-api-request',nonce:item.nonce,id:'upload',path:'/modules/home-inventory/api/v1/items/abc-123/attachments',idempotencyKey:'operation-1',upload:{bytes:new Uint8Array([1,2,3]).buffer,name:'receipt.png',type:'image/png'}}});
 assert.match(calls[0].url,/^\/session\/upload\/home-inventory\?path=/);
 assert.equal(calls[0].options.headers['Idempotency-Key'],'operation-1');
 const file=calls[0].options.body.get('file');assert.equal(file.name,'receipt.png');assert.equal(file.size,3);
 assert.deepEqual(messages.at(-1).data,{ok:true});
});
