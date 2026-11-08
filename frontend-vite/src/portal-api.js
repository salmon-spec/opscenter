export * from './api.js'
import {apiUrl} from './api.js'
let nonce='',sequence=0,readyResolve;
const sockets=new Map();
const ready=new Promise(resolve=>{readyResolve=resolve}),pending=new Map();
window.addEventListener('message',event=>{
 if(event.source!==parent)return;
 const data=event.data||{};
 if(data.type==='ops-module-socket-event'&&data.nonce===nonce){const socket=sockets.get(data.id);if(socket){socket.readyState=data.event==='open'?1:data.event==='close'?3:socket.readyState;socket['on'+data.event]?.(data);if(data.event==='close')sockets.delete(data.id)}return;}
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
export function openPortalApp(url){parent.postMessage({type:'ops-module-open-url',nonce,url},'*');}
// UI capability marker; operator credentials remain in the parent document.
export const getAccessToken=()=>nonce?'portal-session':'';
async function request(path,options={}){
 await ready;
 const {signal,...messageOptions}=options;
 if(signal?.aborted)throw new DOMException('请求已取消','AbortError');
 const id='native-'+(++sequence);
 return new Promise((resolve,reject)=>{
  const cleanup=()=>{pending.delete(id);clearTimeout(timer);signal?.removeEventListener('abort',abort)};
  const abort=()=>{cleanup();reject(new DOMException('请求已取消','AbortError'))};
  const timer=setTimeout(()=>{cleanup();reject(Error('请求超时，请核对操作结果'));},(options.timeoutMs||20000)+2000);
  pending.set(id,{resolve:value=>{cleanup();resolve(value)},reject:error=>{cleanup();reject(error)},timer});
  signal?.addEventListener('abort',abort,{once:true});
  parent.postMessage({type:'ops-module-api-request',nonce,id,path:apiUrl(path),...messageOptions},'*');
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
export function createWebSocket(path){
 const id='socket-'+(++sequence);
 const socket={readyState:0,onopen:null,onmessage:null,onerror:null,onclose:null,
  send(data){if(this.readyState!==1)throw Error('终端尚未连接');parent.postMessage({type:'ops-module-socket-send',nonce,id,data},'*')},
  close(){this.readyState=2;parent.postMessage({type:'ops-module-socket-close',nonce,id},'*')},
 };
 sockets.set(id,socket);
 ready.then(()=>{if(socket.readyState===2){socket.readyState=3;sockets.delete(id);return}parent.postMessage({type:'ops-module-socket-open',nonce,id,path},'*')});
 return socket;
}
