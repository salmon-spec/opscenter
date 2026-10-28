'use strict';
const root = document.getElementById('ops-automation-preview');
const $ = selector => root.querySelector(selector);
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const embedded = parent !== window, requests = new Map();
let nonce = '', ready = false, sequence = 0;
let token = embedded ? '' : sessionStorage.getItem('automation-token') || '', state = null, tab = 'templates', loading = false, mutating = false, logTaskId = null;
const statusLabels = {success:'成功', error:'失败', failed:'失败', running:'运行中', waiting:'等待中', starting:'启动中', stopping:'停止中', stopped:'引擎已停止'};
const activeStatuses = ['running', 'waiting', 'starting', 'stopping'];
const date = value => value ? new Date(value).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai', hour12:false}) : '—';
const lookup = (kind, id) => state[kind].find(x => x.id === id);
const nameOf = (kind, id) => lookup(kind, id)?.name || `#${id}`;
const statusBadge = status => `<span class="op-status ${['error','failed'].includes(status)?'failed':activeStatuses.includes(status)?'running':''}">${escapeHtml(statusLabels[status] || status)}</span>`;
const field = (label, control) => `<label>${label}${control}</label>`;
const input = (name, value = '', attributes = '') => `<input class="op-field" name="${name}" value="${escapeHtml(value)}" ${attributes}>`;
function select(name, rows, selected) { return `<select class="op-field" name="${name}" required>${rows.map(r => `<option value="${r.id}" ${r.id === selected ? 'selected' : ''}>${escapeHtml(r.name)}</option>`).join('')}</select>`; }
function actions(text = '保存') { return `<div class="op-drawer-actions"><button class="op-button" type="button" data-action="close">取消</button><button class="op-button primary" type="submit">${text}</button></div>`; }
async function api(path, method = 'GET', body) {
  if (embedded) {
    if (!ready) throw new Error('正在连接运维工作台');
    const id = 'automation-' + (++sequence);
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => { requests.delete(id); reject(new Error('请求超时，请刷新核对执行结果')); }, 35000);
      requests.set(id, {resolve, reject, timeout});
      // shortcut: the existing host bridge supports GET/POST; edit endpoints also accept POST.
      parent.postMessage({type:'ops-module-api-request', nonce, id, path:'/modules/automation/api/v1/' + path, method:method === 'PUT' ? 'POST' : method, body, timeoutMs:30000}, '*');
    });
  }
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 30000);
  try {
    const response = await fetch('/api/v1/' + path, {method, headers:{Authorization:'Bearer ' + token, ...(body !== undefined ? {'Content-Type':'application/json'} : {})}, body:body === undefined ? undefined : JSON.stringify(body), signal:controller.signal, credentials:'omit'});
    const data = await response.json();
    if (response.status === 401) { logout(); throw new Error('登录已失效，请重新输入模块令牌。'); }
    if (!response.ok) throw new Error(data.detail || '请求失败');
    return data;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('请求超时，操作结果可能已生效。请刷新核对，勿重复提交。');
    throw error;
  } finally { clearTimeout(timer); }
}
function logout() { token = ''; state = null; sessionStorage.removeItem('automation-token'); $('#application').hidden = true; $('#login').hidden = false; if ($('#drawer').open) $('#drawer').close(); }
async function refresh() {
  if (loading) return;
  loading = true; root.querySelectorAll('[data-action=refresh]').forEach(b => b.disabled = true);
  const requestedToken = token;
  try {
    const data = await api('state');
    if (token !== requestedToken) return;
    state = data;
    $('#login').hidden = true; $('#application').hidden = false; $('#page-error').hidden = true;
    $('#last-sync').textContent = '最近同步 ' + date(data.collected_at) + ' · 北京时间'; render();
    if ($('#drawer').open && logTaskId !== null) await updateLog();
  } catch (error) {
    const el = state || embedded ? $('#page-error') : $('#login-error'); el.textContent = error.message; el.hidden = false;
    if (state) $('#last-sync').textContent = '刷新失败 · 当前保留上次成功数据';
    throw error;
  } finally { loading = false; root.querySelectorAll('[data-action=refresh]').forEach(b => b.disabled = false); }
}
function table(headers, rows) { return `<div class="op-panel"><div class="op-table-wrap"><table><thead><tr>${headers.map(x=>`<th>${x}</th>`).join('')}</tr></thead><tbody>${rows || `<tr><td colspan="${headers.length}" class="op-empty">暂无记录</td></tr>`}</tbody></table></div></div>`; }
function taskRows(tasks) { return tasks.map(t => `<tr><td><span class="op-name">${escapeHtml(nameOf('templates',t.template_id))}</span><div class="op-secondary">#${t.id} · ${t.dry_run === true ? '检查模式' : t.dry_run === false ? '执行模式' : '模式未提供'}</div></td><td>${statusBadge(t.status)}</td><td>${escapeHtml(date(t.created))}</td><td>${t.start && t.end ? Math.max(0,Math.round((new Date(t.end)-new Date(t.start))/1000))+' 秒':'—'}</td><td><button class="op-link" data-log="${t.id}">查看日志</button>${activeStatuses.includes(t.status)?`<button class="op-link" data-stop="${t.id}">停止</button>`:''}</td></tr>`).join(''); }
function render() {
  if (!state) return;
  $('#mode-banner').hidden = state.mode !== 'demo';
  $('#mode-banner').textContent = '演示模式 · 运行仅生成模拟记录，定时计划不会实际触发。';
  $('[data-action=create]').hidden = tab !== 'templates';
  $('#search').placeholder = {templates:'搜索模板名称或脚本',tasks:'搜索任务名称或状态',inventories:'搜索清单名称',schedules:'搜索计划名称'}[tab];
  root.querySelectorAll('[data-tab]').forEach(b=>{b.classList.toggle('active',b.dataset.tab===tab);if(b.getAttribute('role')==='tab')b.setAttribute('aria-selected',String(b.dataset.tab===tab));});
  $('#content').setAttribute('aria-labelledby','tab-'+tab); $('#status-filter').hidden = tab !== 'tasks';
  const add = $('#add-resource'); add.hidden = !['inventories','schedules'].includes(tab); add.textContent = tab==='inventories'?'＋ 新建主机清单':'＋ 新建计划';
  const query = $('#search').value.toLowerCase(); let rows, list = [...state[tab]];
  list = list.filter(x => [x.name,x.description,x.playbook,x.status,x.template_id && nameOf('templates',x.template_id)].filter(Boolean).join(' ').toLowerCase().includes(query));
  if(tab==='tasks')list=list.filter(x=>!$('#status-filter').value||x.status===$('#status-filter').value).sort((a,b)=>b.id-a.id);
  $('#result-count').textContent = `共 ${list.length} ${ {templates:'个模板',tasks:'条执行记录',inventories:'个清单',schedules:'个计划'}[tab]}`;
  if(tab==='templates') {
    rows=list.map(t=>{const last=[...state.tasks].sort((a,b)=>b.id-a.id).find(r=>r.template_id===t.id);return `<tr><td><button class="op-link op-name au-name" data-template="${t.id}">${escapeHtml(t.name)}</button><div class="op-secondary">${escapeHtml(t.description || t.playbook)}</div></td><td>${escapeHtml(t.app || 'ansible')}</td><td>${escapeHtml(nameOf('inventories',t.inventory_id))}</td><td>${last?statusBadge(last.status):'<span class="op-small">未执行</span>'}</td><td><div class="op-row-actions"><button class="op-link" data-run="${t.id}" ${t.app!=='ansible'?'disabled':''}>运行</button><button class="op-link" data-script="${t.id}">编辑脚本</button><button class="op-link" data-template="${t.id}">详情</button></div></td></tr>`}).join('');
    $('#content').innerHTML=table(['模板名称','类型','执行范围','最近执行','操作'],rows);
  } else if(tab==='tasks') $('#content').innerHTML=table(['任务','状态','开始时间','耗时','操作'],taskRows(list));
  else if(tab==='inventories') $('#content').innerHTML=table(['清单名称','格式','SSH 凭据','操作'],list.map(i=>`<tr><td class="op-name">${escapeHtml(i.name)}</td><td>${escapeHtml(i.type)}</td><td>${escapeHtml(nameOf('keys',i.ssh_key_id))}</td><td><button class="op-link" data-inventory="${i.id}">查看清单</button></td></tr>`).join(''));
  else $('#content').innerHTML=table(['计划名称','作业模板','Cron','状态','操作'],list.map(s=>`<tr><td class="op-name">${escapeHtml(s.name)}</td><td>${escapeHtml(nameOf('templates',s.template_id))}</td><td><code>${escapeHtml(s.cron_format)}</code></td><td>${s.active?'已启用':'已暂停'}</td><td><button class="op-link" data-schedule="${s.id}">${s.active?'暂停':'启用'}</button></td></tr>`).join(''));
}
function open(title,subtitle,body) { logTaskId=null;$('#drawer-title').textContent=title;$('#drawer-subtitle').textContent=subtitle;$('#drawer-body').innerHTML=body;$('#drawer-error').textContent='';if(!$('#drawer').open)$('#drawer').showModal(); }
function templateForm(item) {
  if(!state.inventories.length||!state.repositories.length||!state.environments.length){open('准备模板资源','需要主机清单、仓库与变量环境','<p class="op-note">请先在 Semaphore 项目配置仓库、变量环境和 SSH 凭据，并在本模块建立清单。配置完成后刷新。</p>');return;}
  const t=item||{};
  open(item?'编辑模板':'新建模板','Ansible 作业',`<form data-form="template" data-id="${t.id||''}">${field('模板名称',input('name',t.name,'required maxlength="100"'))}${field('描述',input('description',t.description,'maxlength="500"'))}${field('作业仓库',select('repository_id',state.repositories,t.repository_id))}${field('Playbook 路径',input('playbook',t.playbook,'required maxlength="250" placeholder="playbooks/host-health.yml"'))}${field('目标主机清单',select('inventory_id',state.inventories,t.inventory_id))}${field('变量环境',select('environment_id',state.environments,t.environment_id))}${actions()}</form>`);
}
async function scriptForm(id, path) {
  const script=await api(`templates/${id}/script`+(path?'?path='+encodeURIComponent(path):''));
  open('编辑作业脚本',nameOf('templates',id),`<form data-form="script" data-id="${id}"><label>脚本文件<select class="op-field" id="script-file" data-template-id="${id}">${script.files.map(p=>`<option value="${escapeHtml(p)}" ${p===script.path?'selected':''}>${escapeHtml(p)}</option>`).join('')}</select></label><input type="hidden" name="path" value="${escapeHtml(script.path)}"><input type="hidden" name="revision" value="${script.revision}">${field('脚本正文',`<textarea class="op-field au-code" name="content" rows="22" required maxlength="128000" spellcheck="false">${escapeHtml(script.content)}</textarea>`)}<p class="op-note">保存后生成 Git 版本，后续作业使用新版本。凭据请放在受保护文件中。</p>${actions('保存脚本')}</form>`);
}
async function runForm(id) {
  const t=lookup('templates',id), wg=t.playbook==='wireguard-install.yml';
  let controls=field('运行参数（JSON 对象）','<textarea class="op-field" name="parameters" rows="5" spellcheck="false" placeholder="{&quot;version&quot;:&quot;1.0&quot;}">{}</textarea>')+'<p class="op-note">参数作为 Ansible extra vars 传入；请勿填写密码、私钥或令牌。</p>';
  if(wg){
    const result=await api('hosts'), hosts=result.items.map(h=>({id:h.id,name:h.name+' · '+h.host})), defaults=result.defaults||{};
    if(!hosts.length)throw new Error('暂无可管理的主机');
    controls=field('目标主机',select('server_id',hosts))+field('WireGuard Hub',select('hub_id',hosts,defaults.hub_id))+field('接口名称',input('interface','wg0','required maxlength="15"'))+field('自动分配地址池',input('pool','10.66.66.0/24','required'))+field('Hub Endpoint',input('endpoint',defaults.endpoint||'','placeholder="公网域名或IP:51820"'))+'<p class="op-note">自动避开 Hub Peer 与主机台账已用地址。保留已有隧道配置，握手及连通性通过才判成功。</p>';
  }
  open('运行作业',t.name,`<form data-form="run" data-wg="${wg}"><input name="template_id" type="hidden" value="${id}"><div class="op-kv"><span>执行范围</span><span>${escapeHtml(nameOf('inventories',t.inventory_id))}</span><span>作业脚本</span><code>${escapeHtml(t.playbook)}</code></div>${controls}${field('执行说明',input('message','','maxlength="300" placeholder="填写本次操作原因"'))}<label class="au-check"><input name="dry_run" type="checkbox" checked>检查模式${wg?'（仅预览 WireGuard 分配方案）':'（Ansible --check）'}</label><p class="op-note">${state.mode==='demo'?'演示模式不执行脚本。':'检查模式仍会连接真实主机；执行前请核对目标及脚本。'}</p>${actions(state.mode==='demo'?'模拟运行':'提交执行')}</form>`);
}
function inventoryForm() { if(!state.keys.length){open('先配置 SSH 凭据','Semaphore 项目配置','<p>请先在 Semaphore 项目中创建 SSH 凭据。模块只引用凭据 ID，不接收或展示私钥。</p>');return;}
  open('新建主机清单','使用独立执行凭据',`<form data-form="inventory">${field('清单名称',input('name','','required maxlength="100"'))}${field('格式','<select class="op-field" name="type"><option value="static">INI 静态清单</option><option value="static-yaml">YAML 静态清单</option></select>')}${field('SSH 凭据',select('ssh_key_id',state.keys))}${field('清单内容','<textarea class="op-field" name="inventory" rows="8" required maxlength="32000" placeholder="[servers]&#10;host1 ansible_host=192.0.2.10"></textarea>')}<p class="op-note">密码和私钥通过 Semaphore 凭据管理，不要写入清单。</p>${actions()}</form>`);
}
function scheduleForm() { if(!state.templates.length){templateForm();return;}
  open('新建定时计划','调度由 Semaphore 执行',`<form data-form="schedule">${field('计划名称',input('name','','required maxlength="100"'))}${field('作业模板',select('template_id',state.templates.filter(t=>t.app==='ansible')))}${field('Cron 表达式',input('cron_format','0 9 * * *','required maxlength="120"'))}<p class="op-note">五段 Cron；真实执行时区以 Semaphore 服务配置为准，请将该服务配置为 Asia/Shanghai。</p><p class="op-note">新计划默认暂停。${state.mode==='demo'?'本地演示不会定时执行。':''}</p>${actions('保存为暂停')}</form>`);
}
async function updateLog(){const requestedId=logTaskId;const result=await api(`tasks/${requestedId}/output`);if(logTaskId===requestedId&&$('#log-output'))$('#log-output').textContent=result.lines.join('\n')||'暂无输出';}
async function mutate(path,method,body){if(mutating)return;mutating=true;$('#drawer').querySelectorAll('button').forEach(b=>b.disabled=true);try{const result=await api(path,method,body);$('#drawer').close();$('#notice').textContent=result?.execution_may_continue?'停止请求已受理；目标上已启动的命令可能继续执行，请检查目标状态。':'操作已提交。';await refresh();}catch(error){const target=$('#drawer').open?$('#drawer-error'):$('#notice');target.textContent=error.message;}finally{mutating=false;$('#drawer').querySelectorAll('button').forEach(b=>b.disabled=false);}}
root.addEventListener('click',async e=>{const b=e.target.closest('button');if(!b||b.disabled)return;try{
  if(b.dataset.action==='close'){$('#drawer').close();return;}
  if(b.dataset.action==='logout'){logout();return;}
  if(b.dataset.action==='refresh'){await refresh();return;}
  if(!state)return;
  if(b.dataset.tab){tab=b.dataset.tab;$('#search').value='';$('#notice').textContent='';render();return;}
  if(b.dataset.action==='create'){templateForm();return;}
  if(b.id==='add-resource'){tab==='inventories'?inventoryForm():scheduleForm();return;}
  if(b.dataset.script){await scriptForm(Number(b.dataset.script));return;}
  if(b.dataset.run){await runForm(Number(b.dataset.run));return;}
  if(b.dataset.template){const t=lookup('templates',Number(b.dataset.template));open(t.name,'模板详情',`<div class="op-kv"><span>执行范围</span><span>${escapeHtml(nameOf('inventories',t.inventory_id))}</span><span>脚本</span><code>${escapeHtml(t.playbook)}</code><span>仓库</span><span>${escapeHtml(nameOf('repositories',t.repository_id))}</span><span>变量环境</span><span>${escapeHtml(nameOf('environments',t.environment_id))}</span></div><p>${escapeHtml(t.description)}</p><div class="op-drawer-actions"><button class="op-button" data-edit="${t.id}" ${t.app!=='ansible'?'disabled':''}>编辑模板</button><button class="op-button primary" data-run="${t.id}" ${t.app!=='ansible'?'disabled':''}>运行作业</button></div>`);return;}
  if(b.dataset.edit){templateForm(lookup('templates',Number(b.dataset.edit)));return;}
  if(b.dataset.inventory){const i=lookup('inventories',Number(b.dataset.inventory));open(i.name,'主机清单 · '+i.type,'<pre class="op-log" id="inventory-text"></pre>');$('#inventory-text').textContent=i.inventory||'此清单由 Semaphore 动态解析。';return;}
  if(b.dataset.log){const id=Number(b.dataset.log),t=lookup('tasks',id);open(`执行 #${id}`,nameOf('templates',t.template_id),`<div class="op-kv"><span>状态</span><span>${statusBadge(t.status)}</span><span>创建时间</span><span>${escapeHtml(date(t.created))}</span><span>执行说明</span><span>${escapeHtml(t.message)}</span></div><pre class="op-log" id="log-output">正在读取日志…</pre><p class="op-note">${t.status==='stopped'?'引擎已停止调度；目标上已启动的命令仍可能执行，请核对目标状态。':'日志可能含敏感运维数据，请限制访问范围。'}</p>`);logTaskId=id;await updateLog();return;}
  if(b.dataset.stop){const id=Number(b.dataset.stop);open('停止作业',`执行 #${id}`,`<form data-form="stop" data-id="${id}"><p>将请求 Semaphore 停止任务。目标上已经启动的命令可能继续执行，已有变更不会自动回滚；请随后核对目标状态。</p>${actions('请求停止')}</form>`);return;}
  if(b.dataset.schedule){const s=lookup('schedules',Number(b.dataset.schedule));open(s.active?'暂停计划':'启用计划',s.name,`<form data-form="active" data-id="${s.id}" data-active="${!s.active}"><p>${s.active?'暂停后将不再产生新的定时任务。':state.mode==='demo'?'演示计划仅保存启用状态，不触发执行。':'启用后会按 Semaphore 服务时区自动执行，可能修改目标主机。'}</p>${actions('确认'+(s.active?'暂停':'启用'))}</form>`);}
}catch(error){($('#drawer').open?$('#drawer-error'):$('#notice')).textContent=error.message;}});
root.addEventListener('submit',async e=>{e.preventDefault();const f=e.target;
  if(f.id==='login-form'){if(loading)return;token=f.elements.token.value.trim();$('#login-error').textContent='';try{await refresh();sessionStorage.setItem('automation-token',token);f.reset();}catch{}return;}
  if(!f.dataset.form||mutating)return;
  const data=Object.fromEntries(new FormData(f));
  for(const key of ['template_id','inventory_id','repository_id','environment_id','ssh_key_id'])if(key in data)data[key]=Number(data[key]);
  const type=f.dataset.form;
  if(type==='template'){data.app='ansible';await mutate('templates'+(f.dataset.id?'/'+f.dataset.id:''),f.dataset.id?'PUT':'POST',data);}
  if(type==='script')await mutate(`templates/${f.dataset.id}/script`,'POST',data);
  if(type==='inventory')await mutate('inventories','POST',data);
  if(type==='schedule')await mutate('schedules','POST',{...data,active:false,type:''});
  if(type==='run'){
    try{
      if(f.dataset.wg==='true'){
        data.parameters=Object.fromEntries(['server_id','hub_id','interface','pool','endpoint'].map(key=>[key,data[key]]));
        for(const key of Object.keys(data.parameters))delete data[key];
        if(data.parameters.server_id===data.parameters.hub_id)throw new Error('目标主机不能是 Hub 本身');
      }else{
        data.parameters=JSON.parse(data.parameters);
        if(!data.parameters||Array.isArray(data.parameters)||typeof data.parameters!=='object')throw new Error('参数须为 JSON 对象');
      }
      data.dry_run=f.elements.dry_run.checked;await mutate('tasks','POST',data);
    }catch(error){$('#drawer-error').textContent=error.message;}
  }
  if(type==='stop')await mutate(`tasks/${f.dataset.id}/stop`,'POST',{});
  if(type==='active')await mutate(`schedules/${f.dataset.id}/active`,'PUT',{active:f.dataset.active==='true'});
});
$('#drawer').addEventListener('close',()=>{logTaskId=null;});
$('#search').addEventListener('input',render);$('#status-filter').addEventListener('change',render);
setInterval(()=>{if(!document.hidden&&state&&!loading&&!mutating&&(tab==='tasks'||logTaskId!==null||state.tasks.some(t=>activeStatuses.includes(t.status))))refresh().catch(()=>{});},5000);
if(token)refresh().catch(()=>{});
if (embedded) {
  $('#login').hidden = true;
  $('#application').hidden = false;
  root.querySelectorAll('[data-action=logout]').forEach(b => b.hidden = true);
  window.addEventListener('message', event => {
    if (event.source !== parent) return;
    const data = event.data || {};
    if (data.type === 'ops-module-info' && data.module?.key === 'automation') {
      nonce = data.nonce;
      if (!ready) { ready = true; refresh().catch(() => {}); }
    } else if (data.type === 'ops-module-api-response' && data.nonce === nonce) {
      const request = requests.get(data.id);
      if (!request) return;
      requests.delete(data.id); clearTimeout(request.timeout);
      data.ok ? request.resolve(data.data) : request.reject(new Error(data.data?.message || '模块请求失败'));
    }
  });
  const handshake = setInterval(() => { if (ready) clearInterval(handshake); else parent.postMessage({type:'ops-module-ready'}, '*'); }, 500);
  parent.postMessage({type:'ops-module-ready'}, '*');
  window.addEventListener('pagehide', () => { clearInterval(handshake); for (const request of requests.values()) clearTimeout(request.timeout); });
}

root.addEventListener('change',async e=>{if(e.target.id!=='script-file')return;const picker=e.target;try{await scriptForm(Number(picker.dataset.templateId),picker.value);}catch(error){$('#drawer-error').textContent=error.message;}});
