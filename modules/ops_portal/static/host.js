// The portal owns operator credentials; child documents receive only a scoped message channel.
export function createModuleHost({token, frames, expired, openModule}) {
 const sockets=new Map();
 const detach=item=>{for(const [id,value] of sockets)if(value.item===item){value.cancelled=true;value.socket?.close();sockets.delete(id)}};
 const nonce=()=>Array.from(crypto.getRandomValues(new Uint8Array(16)),b=>b.toString(16).padStart(2,'0')).join('');
 const announce=item=>item.frame.contentWindow?.postMessage({type:'ops-module-info',nonce:item.nonce,module:item.module},'*');
 async function message(event){
  const item=[...frames.values()].find(i=>i.module&&i.frame.contentWindow===event.source);
  if(!item)return;
  const data=event.data||{};
  if(data.type==='ops-module-ready'){announce(item);return;}
  if(data.type==='ops-module-open'&&data.nonce===item.nonce){openModule(String(data.key||''));return;}
  if(data.type?.startsWith('ops-module-socket-')&&data.nonce===item.nonce){
   const id=String(data.id||'');if(!id||id.length>200)return;
   const socketId=item.nonce+':'+id,value=sockets.get(socketId);
   const emit=(event,extra={})=>{if(frames.get(item.app.id)===item)eventSource.postMessage({type:'ops-module-socket-event',nonce:item.nonce,id,event,...extra},'*')};
   const eventSource=event.source;
   if(data.type==='ops-module-socket-close'){if(value){value.cancelled=true;value.socket?.close();sockets.delete(socketId)}return;}
   if(data.type==='ops-module-socket-send'){if(value?.socket?.readyState===1&&typeof data.data==='string'&&data.data.length<=1024*1024)value.socket.send(data.data);return;}
   if(data.type!=='ops-module-socket-open'||value||[...sockets.values()].filter(v=>v.item===item).length>=8)return;
   const pending={item,cancelled:false};sockets.set(socketId,pending);
   try{
    const response=await fetch('/session/socket-ticket',{method:'POST',headers:{Authorization:'Bearer '+token(),'Content-Type':'application/json'},body:JSON.stringify({module:item.module.key,path:data.path})});
    if(response.status===401)expired();if(!response.ok)throw Error('终端连接失败');
    const ticket=(await response.json()).ticket;if(pending.cancelled)return;
    const socket=new WebSocket((location.protocol==='https:'?'wss://':'ws://')+location.host+'/session/socket/'+encodeURIComponent(ticket));pending.socket=socket;
    socket.onopen=()=>emit('open');socket.onmessage=e=>emit('message',{data:e.data});socket.onerror=()=>emit('error');socket.onclose=e=>{sockets.delete(socketId);emit('close',{code:e.code,reason:e.reason})};
   }catch{ sockets.delete(socketId);emit('error');emit('close',{code:1011,reason:'终端连接失败'}); }
   return;
  }
  if(!['ops-module-api-request','ops-module-download-request'].includes(data.type)||data.nonce!==item.nonce)return;
  const id=String(data.id||'');if(!id||id.length>200)return;
  const reply=(ok,result)=>{if(frames.get(item.app.id)!==item)return;event.source.postMessage({type:'ops-module-api-response',nonce:item.nonce,id,ok,data:result},'*');};
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),Math.min(Math.max(Number(data.timeoutMs)||20000,1000),180000));
  try{
   let response;
   const headers={Authorization:'Bearer '+token()};
   if(data.upload){
    const max=item.module.key.startsWith('core-')?64:5;
    if(!(data.upload.bytes instanceof ArrayBuffer)||data.upload.bytes.byteLength>max*1024*1024)throw Error('文件不能超过'+max+'MiB');
    const form=new FormData();form.append('file',new Blob([data.upload.bytes],{type:data.upload.type||'application/octet-stream'}),String(data.upload.name||'attachment'));
    if(data.idempotencyKey)headers['Idempotency-Key']=String(data.idempotencyKey);
    response=await fetch('/session/upload/'+encodeURIComponent(item.module.key)+'?path='+encodeURIComponent(data.path)+'&directory='+encodeURIComponent(data.query?.path||''),{method:'POST',headers,body:form,signal:controller.signal});
   }else{
    headers['Content-Type']='application/json';
    response=await fetch('/session/relay',{method:'POST',headers,signal:controller.signal,body:JSON.stringify({module:item.module.key,path:data.path,method:data.method||'GET',query:data.query||{},body:data.body??null,idempotency_key:data.idempotencyKey||'',binary:!!data.binary})});
   }
   if(response.status===401)expired();
   if(!response.ok){let error={};try{error=await response.json();}catch{}throw Object.assign(Error(error.detail||'模块请求失败'),{status:response.status});}
   if(data.type==='ops-module-download-request'){
    const url=URL.createObjectURL(await response.blob()),a=document.createElement('a');a.href=url;a.download=String(data.filename||'download');a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);reply(true,{downloaded:true});
   }else if(data.binary){reply(true,{bytes:await response.arrayBuffer(),contentType:response.headers.get('content-type')});}
   else reply(true,await response.json());
  }catch(e){reply(false,{message:e.name==='AbortError'?'请求超时，请核对操作结果':e.message,status:e.status});}
  finally{clearTimeout(timer);}
 }
 window.addEventListener('message',message);
 return {attach(item){item.nonce=nonce();item.frame.addEventListener('load',()=>{detach(item);announce(item)});},announce,detach};
}
