export * from './api.js'
let nonce='',sequence=0,readyResolve;
const ready=new Promise(resolve=>{readyResolve=resolve}),pending=new Map();
window.addEventListener('message',event=>{
 if(event.source!==parent)return;
 const data=event.data||{};
 if(data.type==='ops-module-info'&&typeof data.nonce==='string'){
  nonce=data.nonce;readyResolve(data.module);return;
 }
 if(data.type!=='ops-module-api-response'||data.nonce!==nonce)return;
 const item=pending.get(data.id);if(!item)return;
 pending.delete(data.id);clearTimeout(item.timer);
 data.ok?item.resolve(data.data):item.reject(Object.assign(Error(data.data?.message||'请求失败'),{status:data.data?.status}));
});
const handshake=setInterval(()=>{if(nonce)clearInterval(handshake);else parent.postMessage({type:'ops-module-ready'},'*');},500);
parent.postMessage({type:'ops-module-ready'},'*');
export const portalReady=ready;
export function openPortalModule(key){parent.postMessage({type:'ops-module-open',nonce,key},'*');}
export const getAccessToken=()=>'';
async function request(path,options={}){
 await ready;
 const id='native-'+(++sequence);
 return new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>{pending.delete(id);reject(Error('请求超时，请核对操作结果'));},(options.timeoutMs||20000)+2000);
  pending.set(id,{resolve,reject,timer});
  parent.postMessage({type:'ops-module-api-request',nonce,id,path,...options},'*');
 });
}
export const api={
 get:(path,query,options={})=>request(path,{...options,method:'GET',query}),
 post:(path,body,options={})=>request(path,{...options,method:'POST',body}),
 put:(path,body,options={})=>request(path,{...options,method:'PUT',body}),
 patch:(path,body,options={})=>request(path,{...options,method:'PATCH',body}),
 del:(path,options={})=>request(path,{...options,method:'DELETE'}),
 download:async(path,query,options={})=>{const result=await request(path,{...options,query,binary:true});return new Blob([result.bytes],{type:result.contentType});},
 upload:async(path,file,options={})=>request(path,{...options,method:'POST',upload:{name:file.name,type:file.type,bytes:await file.arrayBuffer()}}),
};
