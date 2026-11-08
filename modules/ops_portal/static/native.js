// Built-in documents contain their complete UI bundle. Load the public document
// directly into the opaque frame, keeping navigation and credentials in the portal.
export async function loadNativeDocument(frame,url,signal){
 const response=await fetch(url,{credentials:'omit',cache:'no-store',signal});
 if(!response.ok)throw Error('应用页面暂不可用，请刷新重试');
 const html=await response.text();
 if(!signal.aborted)frame.srcdoc=html;
}
