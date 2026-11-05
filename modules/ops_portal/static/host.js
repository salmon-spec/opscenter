// The portal owns operator credentials; child documents receive only a scoped message channel.
export function createModuleHost({token, frames, expired, openModule}) {
 const nonce=()=>Array.from(crypto.getRandomValues(new Uint8Array(16)),b=>b.toString(16).padStart(2,'0')).join('');
 const announce=item=>item.frame.contentWindow?.postMessage({type:'ops-module-info',nonce:item.nonce,module:item.module},'*');
 async function message(event){
  const item=[...frames.values()].find(i=>i.module&&i.frame.contentWindow===event.source);
  if(!item)return;
  const data=event.data||{};
  if(data.type==='ops-module-ready'){announce(item);return;}
  if(data.type==='ops-module-open'&&data.nonce===item.nonce){openModule(String(data.key||''));return;}
  if(!['ops-module-api-request','ops-module-download-request'].includes(data.type)||data.nonce!==item.nonce)return;
  const id=String(data.id||'');if(!id||id.length>200)return;
  const reply=(ok,result)=>{if(frames.get(item.app.id)!==item)return;event.source.postMessage({type:'ops-module-api-response',nonce:item.nonce,id,ok,data:result},'*');};
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),Math.min(Math.max(Number(data.timeoutMs)||20000,1000),180000));
  try{
   let response;
   const headers={Authorization:'Bearer '+token()};
   if(data.upload){
    if(!(data.upload.bytes instanceof ArrayBuffer)||data.upload.bytes.byteLength>5*1024*1024)throw Error('附件不能超过5MiB');
    const form=new FormData();form.append('file',new Blob([data.upload.bytes],{type:data.upload.type||'application/octet-stream'}),String(data.upload.name||'attachment'));
    if(data.idempotencyKey)headers['Idempotency-Key']=String(data.idempotencyKey);
    response=await fetch('/session/upload/'+encodeURIComponent(item.module.key)+'?path='+encodeURIComponent(data.path),{method:'POST',headers,body:form,signal:controller.signal});
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
 return {attach(item){item.nonce=nonce();item.frame.addEventListener('load',()=>{item.nonce=nonce();announce(item);});},announce};
}
