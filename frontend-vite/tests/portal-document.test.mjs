import test from 'node:test'
import assert from 'node:assert/strict'
import {loadNativeDocument} from '../../modules/ops_portal/static/native.js'

test('native document loading keeps credentials out of the opaque document',async()=>{
 let options;globalThis.fetch=async(url,opts)=>{options=opts;assert.equal(url,'/host/core-overview/ui/');return {ok:true,text:async()=>'<main>native business page</main>'}};
 const frame={},controller=new AbortController();await loadNativeDocument(frame,'/host/core-overview/ui/',controller.signal);
 assert.equal(options.credentials,'omit');assert.equal(options.headers,undefined);
 assert.equal(frame.srcdoc,'<main>native business page</main>');assert.equal(frame.src,undefined);
});

test('a cancelled or failed document cannot replace the current application',async()=>{
 const frame={srcdoc:'current'},controller=new AbortController();
 globalThis.fetch=async()=>({ok:true,text:async()=>{controller.abort();return 'stale'}});
 await loadNativeDocument(frame,'/host/core-hosts/ui/',controller.signal);assert.equal(frame.srcdoc,'current');
 globalThis.fetch=async()=>({ok:false});
 await assert.rejects(loadNativeDocument(frame,'/host/core-hosts/ui/',new AbortController().signal),/暂不可用/);assert.equal(frame.srcdoc,'current');
});
