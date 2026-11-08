// Project each application's existing navigation into its portal module sidebar.
// The controls remain in place so the application's own handlers and state are retained.
(()=>{
 let nonce='',key='',last='',controls=new Map(),timer,accepted=false,currentNav;
 const preferred={'core-alerts':'.center-tabs','core-services':'[aria-label="服务功能"]',electricity:'#tabs','command-code-usage':'#accountTabs'};
 function publish(force=false){
  if(!nonce||['codex-agent','opencode-agent'].includes(key))return;
  const nav=(preferred[key]&&document.querySelector(preferred[key]))||document.querySelector('[data-native-functions]')||document.querySelector('[role="tablist"]');
  if(!nav)return;
  currentNav=nav;
  controls=new Map();const items=[];
  for(const node of nav.querySelectorAll('button,a,[role="tab"]')){
   const label=(node.textContent||node.getAttribute('aria-label')||'').replace(/\s+/g,' ').trim().slice(0,80);if(!label)continue;
   const id='f'+items.length;controls.set(id,node);
   items.push({id,label,group:(node.closest('[data-function-group]')?.getAttribute('data-function-group')||nav.getAttribute('aria-label')||'功能导航').slice(0,80),active:node.getAttribute('aria-selected')==='true'||node.getAttribute('aria-current')==='page'||['active','selected','current','btn-primary','router-link-exact-active'].some(name=>node.classList.contains(name)),disabled:!!node.disabled});
   if(items.length>=60)break;
  }
  if(!items.length)return;
  if(accepted)nav.setAttribute('data-portal-function-source','');
  const value=JSON.stringify(items);if(!force&&value===last)return;last=value;
  parent.postMessage({type:'ops-module-functions',nonce,items},'*');
 }
 addEventListener('message',event=>{
  if(event.source!==parent)return;const data=event.data||{};
  if(data.type==='ops-module-info'&&typeof data.nonce==='string'){nonce=data.nonce;key=data.module?.key;publish(true)}
  if(data.type==='ops-module-functions-accepted'&&data.nonce===nonce){accepted=true;currentNav?.setAttribute('data-portal-function-source','')}
  if(data.type==='ops-module-select-function'&&data.nonce===nonce){const node=controls.get(data.id);if(node&&!node.disabled)node.click()}
 });
 new MutationObserver(()=>{clearTimeout(timer);timer=setTimeout(()=>publish(),30)}).observe(document.documentElement,{subtree:true,childList:true,attributes:true,attributeFilter:['aria-selected','aria-current','class','disabled']});
})();
