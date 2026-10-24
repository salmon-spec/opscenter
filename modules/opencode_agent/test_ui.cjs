// Async behavior checks for the real page script, without browser/network dependencies.
const {readFileSync}=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const source=[...readFileSync(__dirname+'/index.html','utf8').matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m=>m[1]).join('\n');
function page(){
  const nodes=new Map();
  function node(){return {value:'',textContent:'',hidden:false,style:{},dataset:{},children:[],classList:{toggle(){},remove(){}},append(...children){this.children.push(...children)},replaceChildren(...children){this.children=children},querySelectorAll(){return []},setAttribute(){},addEventListener(){},focus(){}}}
  const context=vm.createContext({console,Map,Set,JSON,Date,Promise,setTimeout(){},setInterval(){},clearTimeout(){},clearInterval(){},parent:{postMessage(){}},window:{addEventListener(){}},document:{getElementById(id){if(!nodes.has(id))nodes.set(id,node());return nodes.get(id)},createElement:node,querySelectorAll(){return []}},ResizeObserver:class{observe(){}}});
  vm.runInContext(source,context);
  vm.runInContext(`renderMessages=()=>{};renderPending=()=>{};renderContext=()=>{};renderAttachments=()=>{};renderSessions=()=>{};welcome=()=>{};updateControls=()=>{};autoSize=()=>{};loadSessions=async()=>{};$('model').value='["agnes","agnes-2.5-flash"]';$('agent-mode').value='build';selected='ses_test';`,context);
  return {run:code=>vm.runInContext(code,context),nodes,context};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
async function main(){
  let p=page();
  p.run(`calls=[];call=(path,method)=>{calls.push([path,method]);return new Promise((resolve,reject)=>{resolveRequest=resolve;rejectRequest=reject})};$('prompt').value='hello';$('composer').onsubmit({preventDefault(){}});`);
  assert.equal(p.nodes.get('prompt').value,'');
  assert.equal(p.run('visibleMessages.at(-1).parts[0].text'),'hello');
  assert.equal(p.run('submitting'),true);
  p.run(`$('composer').onsubmit({preventDefault(){}})`);
  assert.equal(p.run('calls.length'),1,'Double send must not submit twice');
  p.run(`rejectRequest(new Error('Submission rejected'))`);await flush();
  assert.equal(p.nodes.get('prompt').value,'hello','Rejected submissions retain the draft');
  assert.equal(p.run('busy'),false);

  p=page();
  p.run(`call=async(path,method)=>{if(path.endsWith('/prompt'))return {accepted:true};throw new Error('Status temporarily unavailable')};$('prompt').value='accepted';$('composer').onsubmit({preventDefault(){}});`);await flush();
  assert.equal(p.nodes.get('prompt').value,'','An accepted task must not be restored as an unsent draft');
  assert.equal(p.run('busy'),true);
  assert.match(p.nodes.get('notice').textContent,/任务已提交/);

  p=page();
  p.run(`requestsByPath={};call=path=>new Promise(resolve=>requestsByPath[path]=resolve);oldPoll=poll();select('ses_new','New task');`);
  assert.ok(p.run(`Boolean(requestsByPath['/sessions/ses_new/state'])`),'New tasks must not wait for the previous poll');
  p.run(`requestsByPath['/sessions/ses_new/state']({session:{id:'ses_new'},messages:[],status:{type:'idle'},permissions:[],questions:[]})`);await flush();
  p.run(`requestsByPath['/sessions/ses_test/state']({session:{id:'ses_test'},messages:[],status:{type:'busy'},permissions:[],questions:[]})`);await flush();
  assert.equal(p.run('lastSession.id'),'ses_new','A late response must not overwrite the selected task');
  assert.equal(p.run('busy'),false);
  p=page();
  p.run(`visibleMessages=[{_live:true,info:{id:'msg_stream',role:'assistant'},parts:[{id:'prt_stream',type:'text',text:'newer streamed output'}]}];call=async()=>({session:{id:'ses_test'},messages:[{info:{id:'msg_stream',role:'assistant'},parts:[{id:'prt_stream',type:'text',text:'newer'}]}],status:{type:'busy'},permissions:[],questions:[]});poll();`);await flush();
  assert.equal(p.run("visibleMessages[0].parts[0].text"),'newer streamed output','Slow history snapshots must not erase newer streamed text');
  p=page();
  p.run(`submitting=true;busy=false;call=async()=>({cursor:2,parts:[{id:'p',messageID:'m',type:'text',text:'Already generating'}]});pullLive();`);await flush();
  assert.equal(p.run('visibleMessages[0].parts[0].text'),'Already generating','Live text must render while submission acknowledgement is pending');
  console.log('5 UI async behavior checks passed');
}
main().catch(error=>{console.error(error);process.exitCode=1});
