import {createModuleHost} from './host.js';
const $ = s => document.querySelector(s);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icons = {
 gear:'<path d="m9 3-.6 3-2.8 1-2.5-1.2-2 3.4 2.2 2-.3 3-2 2 2 3.4L6 18l2.7 1 .5 3h4l.6-3 2.7-1 2.5 1.2 2-3.4-2.2-2 .3-3 2-2-2-3.4L17 7l-2.7-1-.5-3Z"/><circle cx="11.3" cy="12.5" r="3.2"/>',
 server:'<rect x="5" y="2" width="14" height="7"/><path d="M12 9v5M4 14h16M4 14v4m8-4v4m8-4v4"/><rect x="1" y="18" width="6" height="4"/><rect x="9" y="18" width="6" height="4"/><rect x="17" y="18" width="6" height="4"/>' ,
 grid:'<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
 star:'<path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9Z"/>',
 clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v6l4 2"/>',
 check:'<circle cx="12" cy="12" r="9"/><path d="m7 12 3 3 7-7"/>',
 code:'<path d="m8 5-6 7 6 7m8-14 6 7-6 7m-3-15-2 16"/>',
 chart:'<path d="M3 3v18h18M7 15l4-6 4 4 6-8"/>',
 shield:'<path d="m12 2 9 4v6c0 5-9 10-9 10S3 17 3 12V6Z"/>',
 box:'<path d="m12 2 9 5v10l-9 5-9-5V7Zm0 10 9-5M12 12 3 7m9 5v10"/>',
 tool:'<path d="m14 6 4 4 4-4c1 6-4 10-9 7l-8 8-3-3 8-8C7 5 11 0 17 2Z"/>'
};
const svg = key => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[key] || icons.grid}</svg>`;
const storageKey = 'salmon-portal-v2';
let sessionToken=sessionStorage.getItem('salmon-portal-token')||'';
let saved = {};
try {saved=JSON.parse(localStorage.getItem(storageKey)||'{}');} catch {}

let favorites=new Set(Array.isArray(saved.favorites)?saved.favorites:[]), recent=Array.isArray(saved.recent)?saved.recent:[];
let verified=saved.verified && typeof saved.verified==='object'?saved.verified:{}, section='all', category='平台支撑', active=null;
const opened=new Map(); let base=[], snapshot={}, modules=new Map();
const moduleHost=createModuleHost({token:()=>sessionToken,frames:opened,expired:logout,openModule:key=>{const app=apps().find(a=>a.module_key===key||(key==='password-manager'&&/Vaultwarden/.test(a.name)));if(app)openApp(app.id);}});
function validateApp(app) {
 if (!app || typeof app.name!=='string' || !app.name.trim() || app.name.length>80) throw Error('请填写有效的应用名称');
 const url=new URL(app.url);
 if (!['http:','https:'].includes(url.protocol) || url.username || url.password) throw Error('请使用独立应用的 HTTP/HTTPS 地址，地址中不能包含账号密码');
 if (typeof app.id!=='string' || !app.id || app.id.length>120) throw Error('应用 ID 无效');
 if (typeof app.category!=='string' || !app.category.trim() || app.category.length>80) throw Error('请填写有效分类');
 return {...app,name:app.name.trim(),url:url.href,description:String(app.description||'').slice(0,300),domain:['平台支撑','运维场景','众创共享'].includes(app.domain)?app.domain:undefined,aliases:Array.isArray(app.aliases)?app.aliases.filter(a=>typeof a==='string').slice(0,30):[]};
}
function apps(){return base.filter(a=>a.enabled!==false);}
function save(){try{localStorage.setItem(storageKey,JSON.stringify({favorites:[...favorites],recent,verified}));}catch{toast('浏览器存储不可用，本次偏好未持久保存');}}
function toast(message){$('#toast').textContent=message;$('#toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('#toast').hidden=true,3500);}
function state(app){return verified[app.id]===app.url?'已人工验收':app.status==='unreachable'?'访问待处理':app.embedding==='restricted'?'需适配内嵌':'内嵌待验证';}
function selectSection(id){section=id;active=null;render();}
const domainOf = app => app.domain || (['监控与日志'].includes(app.category) || /OpsCenter/.test(app.name) ? '运维场景' : ['效率工具','应用服务'].includes(app.category) ? '众创共享' : '平台支撑');
const iconOf = app => /Grafana|监控|电费|大屏/.test(app.name)?'chart':/Jenkins|自动化|IT-Tools/.test(app.name)?'tool':/Forgejo|OpenCode/.test(app.name)?'code':/Nexus|MinIO|OSS|物品/.test(app.name)?'box':/Proxmox|主机|容器/.test(app.name)?'server':/Vaultwarden|告警/.test(app.name)?'shield':/待办|订阅/.test(app.name)?'clock':'gear';
const colorOf = app => /Forgejo|Nexus|SonarQube/.test(app.name)?'#bd7044':/Grafana|监控|大屏/.test(app.name)?'#55856e':/Proxmox|容器/.test(app.name)?'#7b6b9e':/IT-Tools|OpenCode/.test(app.name)?'#338d9a':/告警/.test(app.name)?'#b56868':'#5875a4';
function appIcon(app){return `<span class="app-icon" style="--icon-color:${colorOf(app)}">${svg(iconOf(app))}</span>`;}
function render(){
 const all=apps();
 const menuGroups=[['基础设施','server',all.filter(a=>/Proxmox|Nacos|MinIO|主机管理|容器管理|OSS|文件管理|防火墙|SSH 管理|终端|进程管理|数据库|服务广场/.test(a.name))],['可观测性','chart',all.filter(a=>/OpsCenter|Grafana|日志中心|系统监控|告警中心|大屏/.test(a.name))],['交付协作','code',all.filter(a=>/Forgejo|Jenkins|SonarQube|Nexus|自动化/.test(a.name))],['工具服务','tool',all.filter(a=>domainOf(a)==='众创共享')],['门户管理','gear',all.filter(a=>a.module_key==='core-modules')]];
 $('#navigation').innerHTML=menuGroups.map(([name,icon,rows],n)=>`<div class="menu-group"><button data-menu="menu-${n}" aria-expanded="false">${svg(icon)}${name}<span class="chevron">⌄</span></button><div class="menu-popup" id="menu-${n}" hidden>${rows.map(a=>`<button data-open="${esc(a.id)}">${esc(a.name)}</button>`).join('')}${name==='门户管理'?'<button data-settings>快捷访问</button><button data-admin>门户配置管理</button>':''}${!rows.length&&name!=='门户管理'?'<small>暂无应用</small>':''}</div></div>`).join('');
 $('#launcher').innerHTML=svg('grid');$('#catalog-summary').textContent=`${all.length} 个应用入口`;
 $('#categories').innerHTML=['平台支撑','运维场景','众创共享'].map(c=>`<button role="tab" aria-selected="${c===category}" data-filter="${c}" class="${c===category?'active':''}">${({'平台支撑':'基础平台','运维场景':'运维工作台','众创共享':'工具与应用'})[c]}<span class="domain-count">${all.filter(a=>domainOf(a)===c).length}</span></button>`).join('');
 const query=$('#search').value.trim().toLowerCase();
 let rows=all.filter(a=>(section!=='favorites'||favorites.has(a.id))&&(section!=='recent'||recent.includes(a.id))&&(query||section!=='all'||domainOf(a)===category));
 if(query)rows=rows.filter(a=>[a.name,a.description,a.category,a.url].join(' ').toLowerCase().includes(query));
 if(section==='recent')rows.sort((a,b)=>recent.indexOf(a.id)-recent.indexOf(b.id));
 $('#scope-title').hidden=section==='all';$('#scope-title').textContent=section==='favorites'?'我的快捷应用':'最近使用';
 $('#cards').innerHTML=rows.map(a=>`<article class="desktop-app"><button class="app-main" data-open="${esc(a.id)}" title="${esc(a.name)}：${esc(a.description)}">${appIcon(a)}<span class="app-copy"><strong>${esc(a.name)}</strong><small>${esc(a.description||a.title||'应用控制台')}</small></span></button><button class="app-more" data-detail="${esc(a.id)}" aria-label="${esc(a.name)} 应用详情" title="应用详情">⋯</button></article>`).join('');
 $('#empty').hidden=rows.length>0;
 const quick=all.filter(a=>favorites.has(a.id));
 $('#dock').innerHTML=quick.slice(0,7).map(a=>`<button data-open="${esc(a.id)}" aria-label="快捷访问 ${esc(a.name)}">${appIcon(a)}<span class="dock-label">${esc(a.name)}</span></button>`).join('');
 renderWorkspace();
}
function renderWorkspace(){
 $('#tabs').innerHTML=opened.size?`<button data-home class="${active===null?'active':''}">应用中心</button>`+[...opened].map(([id,{app}])=>`<button data-tab="${esc(id)}" class="${active===id?'active':''}">${esc(app.name)}<span class="close-tab" role="button" tabindex="0" data-close-tab="${esc(id)}" aria-label="关闭 ${esc(app.name)}">×</span></button>`).join(''):'';
 $('#home').hidden=active!==null;$('#workspace').hidden=active===null;
 for(const [id,{frame}] of opened)frame.hidden=id!==active;
 if(active===null)return;
 const app=opened.get(active).app;$('#app-name').textContent=app.name;$('#app-url').textContent=app.url;$('#external').href=app.url;
 const note=$('#frame-note');note.hidden=!!app.module_key||app.embedding==='adapted';$('#external').hidden=!!app.module_key;note.classList.toggle('verified',verified[app.id]===app.url);
 note.textContent=verified[app.id]===app.url?'已记录人工验收。若应用升级或登录策略变化，请重新核验。':app.status==='unreachable'?'该地址未通过 HTTP/TLS 核查，请先处理访问问题。':app.embedding==='restricted'?'该应用限制跨来源嵌入，需要为门户配置精确来源。可先独立打开检查登录。':'页面显示、登录、交互尚待验证；页面加载事件不会自动判定集成成功。';
 const confirm=document.createElement('button');confirm.textContent=verified[app.id]===app.url?'撤销验收':'记录人工验收';confirm.onclick=()=>{if(verified[app.id]===app.url){delete verified[app.id];save();render();}else showDetails(app.id);};note.append(confirm);
}
function openApp(id,override){const app=override||apps().find(a=>a.id===id);if(!app)return;
 if(!opened.has(id)){const frame=document.createElement('iframe'),module=modules.get(app.module_key);frame.title=app.name;frame.referrerPolicy='no-referrer';frame.setAttribute('sandbox',module?module.manifest.sandbox.join(' '):'allow-scripts allow-same-origin allow-forms allow-downloads allow-popups allow-popups-to-escape-sandbox allow-modals');const item={app,frame,module};opened.set(id,item);active=id;renderWorkspace();if(module)moduleHost.attach(item);$('#frames').append(frame);frame.src=module?module.entry_url:app.url;}
 active=id;recent=[id,...recent.filter(r=>r!==id)].slice(0,20);save();render();
}
function closeTab(id){const item=opened.get(id);if(!item)return;moduleHost.detach(item);item.frame.remove();opened.delete(id);if(active===id)active=[...opened.keys()].at(-1)||null;renderWorkspace();}
function showDetails(id){const app=apps().find(a=>a.id===id);if(!app)return;
 const fields=[['应用',app.name],['地址',app.url],['内嵌状态',state(app)],['HTTP 状态',app.code||app.error||'未检查'],['核查时间',app.checked_at?new Date(app.checked_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):'未检查'],['嵌入响应头',app.x_frame_options||app.csp||'未检出限制，仍需实测'],['其他入口',(app.aliases||[]).join('\n')||'无']];
 $('#detail-content').innerHTML=`<dl>${fields.map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl><label class="hint"><input type="checkbox" id="verify-check"> 我已在门户内验证页面显示、登录及主要操作</label>`;
 $('#detail-actions').innerHTML=`<button class="primary" id="confirm-verified" disabled>记录验收</button>`;
 $('#verify-check').onchange=e=>$('#confirm-verified').disabled=!e.target.checked;
 $('#confirm-verified').onclick=()=>{verified[id]=app.url;save();$('#details').close();render();toast('已记录人工验收结果');};$('#details').showModal();
}
document.addEventListener('click',e=>{
 const el=e.target.closest('button,[data-close-tab]');if(!el)return;
 if(el.dataset.closeTab){e.stopPropagation();closeTab(el.dataset.closeTab);}
 else if(el.dataset.close)$('#'+el.dataset.close).close();
 else if(el.dataset.section)selectSection(el.dataset.section);
 else if(el.dataset.category){section='all';category=el.dataset.category;active=null;render();}
 else if(el.dataset.filter){section='all';category=el.dataset.filter;render();}
 else if(el.dataset.menu){const menu=$('#'+el.dataset.menu);const wasOpen=!menu.hidden;document.querySelectorAll('.menu-popup').forEach(m=>m.hidden=true);document.querySelectorAll('[data-menu]').forEach(b=>b.setAttribute('aria-expanded','false'));menu.hidden=wasOpen;el.setAttribute('aria-expanded',String(!wasOpen));}
 else if(el.hasAttribute('data-settings')){$('#manage').click();}
 else if(el.hasAttribute('data-admin'))openManagement();
 else if(el.dataset.open){document.querySelectorAll('.menu-popup').forEach(m=>m.hidden=true);openApp(el.dataset.open);}
 else if(el.dataset.tab){active=el.dataset.tab;renderWorkspace();}
 else if(el.hasAttribute('data-home')){active=null;render();}
 else if(el.dataset.detail)showDetails(el.dataset.detail);
 else if(el.dataset.star){favorites.has(el.dataset.star)?favorites.delete(el.dataset.star):favorites.add(el.dataset.star);save();render();showManagement();}
});
$('#search').oninput=render;$('#add').onclick=openManagement;$('#desktop-settings').onclick=()=>$('#manage').click();$('#shortcut-edit').onclick=()=>$('#manage').click();$('#desktop-home').onclick=e=>{e.preventDefault();selectSection('all');};$('#home-float').onclick=()=>selectSection('all');$('#launcher').onclick=()=>selectSection('all');$('#recent-menu').onclick=()=>selectSection('recent');$('#account').onclick=()=>selectSection('favorites');
$('#reload').onclick=()=>{const item=opened.get(active);if(item)item.frame.src=item.module?item.module.entry_url:item.app.url;};$('#fullscreen').onclick=async()=>{try{await $('#workspace').requestFullscreen();}catch{toast('浏览器未允许全屏');}};$('#app-details').onclick=()=>showDetails(active);
function showManagement(){$('#management-list').innerHTML=apps().map(a=>`<div class="management-row"><span>${esc(a.name)}</span><button class="${favorites.has(a.id)?'pinned':''}" data-star="${esc(a.id)}">${favorites.has(a.id)?'★ 已固定':'☆ 固定'}</button></div>`).join('');}
$('#manage').onclick=()=>{showManagement();$('#settings').showModal();};
document.addEventListener('keydown',e=>{if(e.key==='/'&&!['INPUT','TEXTAREA'].includes(document.activeElement.tagName)&&!document.querySelector('dialog[open]')){e.preventDefault();active=null;renderWorkspace();$('#search').focus();}if(e.key==='Enter'&&e.target.dataset.closeTab)closeTab(e.target.dataset.closeTab);});
async function loadCatalogue(){
 const response=await fetch('/api/v1/catalogue',{headers:{Authorization:'Bearer '+sessionToken},cache:'no-store'});
 if(response.status===401){logout();throw Error('请重新登录');}if(!response.ok)throw Error('应用目录暂不可用');
 snapshot=await response.json();
 const registry=await fetch('/session/modules',{headers:{Authorization:'Bearer '+sessionToken},cache:'no-store'});if(!registry.ok)throw Error('模块目录暂不可用');
 modules=new Map((await registry.json()).items.map(m=>[m.key,m]));
 base=snapshot.apps.map(validateApp).map(a=>{const url=new URL(a.url),key=a.module_key||a.url.match(/#\/extensions\/([a-z0-9-]+)/)?.[1]||(url.origin===location.origin?url.pathname.match(/^\/host\/([a-z0-9-]+)\/ui\/$/)?.[1]:null);return modules.has(key)?{...a,module_key:key,url:new URL(modules.get(key).entry_url,location.origin).href}:a;});
 if(!Object.hasOwn(saved,'favorites'))favorites=new Set(snapshot.default_favorites||[]);
 document.body.classList.remove('locked');$('#portal-login').hidden=true;render();
}
function logout(){sessionToken='';sessionStorage.removeItem('salmon-portal-token');document.body.classList.add('locked');$('#portal-login').hidden=false;for(const id of [...opened.keys()])closeTab(id);}
$('#logout').onclick=logout;
$('#login-form').onsubmit=async e=>{e.preventDefault();$('#login-button').disabled=true;$('#login-error').textContent='';try{const values=Object.fromEntries(new FormData(e.target));const response=await fetch('/session/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(values)});const result=await response.json();if(!response.ok)throw Error(result.detail||'登录失败');sessionToken=result.access_token;sessionStorage.setItem('salmon-portal-token',sessionToken);e.target.elements.password.value='';await loadCatalogue();}catch(error){$('#login-error').textContent=error.message;}finally{$('#login-button').disabled=false;}};
if(sessionToken)loadCatalogue().catch(e=>{$('#login-error').textContent=e.message;});

function openManagement(){const m=modules.get('ops-portal');if(!m)return toast('门户管理模块当前不可用');openApp('portal-management',{id:'portal-management',name:'门户配置管理',url:new URL(m.entry_url,location.origin).href,category:'门户管理',module_key:m.key});}
