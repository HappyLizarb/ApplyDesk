const crypto=require('node:crypto');
const path=require('node:path');

// Workday's DSe/XQ upload sends exactly one multipart body to the current employer's attachments API.
async function workdayUploadRequest(request,state) {
  const permit=state.workdayUpload;
  try {
    const frame=request.frame(),url=new URL(request.url());
    if(!permit||permit.used||state.paused||state.generation!==permit.generation||Date.now()>=permit.expires
        ||request.method()!=='POST'||frame!==frame.page().mainFrame()||frame.url()!==permit.pageUrl
        ||url.href!==permit.uploadUrl) return false;
    const form=await new Response(request.postDataBuffer(),{headers:{'content-type':request.headers()['content-type']}}).formData();
    if([...form.keys()].length!==1||form.getAll('body').length!==1)return false;
    const file=form.get('body');
    if(!file||typeof file.arrayBuffer!=='function'||file.name!==permit.filename||file.type!=='application/pdf')return false;
    const supplied=Buffer.from(await file.arrayBuffer());
    if(supplied.length&&(supplied.length!==permit.bytes.length||crypto.createHash('sha256').update(supplied).digest('hex')!==permit.sha256))return false;
    if(crypto.createHash('sha256').update(permit.bytes).digest('hex')!==permit.sha256)return false;
    // Chrome may omit intercepted file bytes; forward the independently hashed local document exactly.
    form.set('body',new Blob([permit.bytes],{type:'application/pdf'}),permit.filename);
    const encoded=new Request(url.href,{method:'POST',body:form}),postData=Buffer.from(await encoded.arrayBuffer());
    if(state.paused||state.workdayUpload!==permit||state.generation!==permit.generation)return false;
    const headers={...request.headers(),'content-type':encoded.headers.get('content-type')};delete headers['content-length'];
    Object.assign(permit,{used:true,request});
    return {headers,postData};
  }catch{return false;}
}

// Bind the returned storage ID to the exact accepted upload; reject contradictory echoed metadata.
function workdayAttachment(body,permit) {
  if(!body||Array.isArray(body)||body.errors?.length||body.error||typeof body.id!=='string'||!body.id.trim()||body.id.length>500
      ||body.fileName!==undefined&&body.fileName!==permit.filename||body.fileLength!==undefined&&body.fileLength!==permit.size
      ||body.contentType!==undefined&&body.contentType?.id!=='Content_Type_ID=application/pdf'
      ||body.diagnosis!==undefined&&body.diagnosis!=='clean'||body.containsMacros===true)throw new Error('Workday did not confirm the expected resume upload.');
  return {file:body.id,fileName:permit.filename,fileLength:permit.size,
    contentType:body.contentType||{id:'Content_Type_ID=application/pdf'}};
}

// The caller supplies the already inspected resume container; no global filename search is used.
function workdayUploadVisible(root,filename) {
  const names=[...root.querySelectorAll('[data-automation-id="file-upload-item-name"]')];
  return root.isConnected&&root.getClientRects().length>0&&names.length===1&&names[0].textContent.trim()===filename
    &&root.querySelectorAll('[data-automation-id="file-upload-successful"]').length===1
    &&!root.querySelector('[data-automation-id="file-loading-dots"],[data-automation-id="file-upload-failed"]');
}

// File bytes, live input and containing resume widget must all belong to this operation.
async function uploadWorkdayResume(page,state,field,input,group,filePath,sha256,bytes,check) {
  const current=new URL(page.url()),tenant=/^([a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com$/.exec(current.hostname)?.[1];
  const filename=path.basename(filePath),size=bytes.length;
  if(field.widget!=='workday_resume'||!tenant||current.protocol!=='https:'||current.port||current.username||current.password
      ||!/\/job\/[^/]+\/[^/]+_JR\d+\/apply\/applyManually$/.test(current.pathname)
      ||path.extname(filename).toLowerCase()!=='.pdf'||!size||size>5242880
      ||crypto.createHash('sha256').update(bytes).digest('hex')!==sha256)throw new Error('The Workday resume upload scope is invalid.');
  const previous=(state.workdayFiles||[]).find(item=>item.pageUrl===page.url()&&item.field.field_id===field.field_id);
  if(previous) {
    if(previous.sha256!==sha256||!await previous.group.evaluate(workdayUploadVisible,filename))throw new Error('Review the existing Workday resume before replacing it.');
    return {field_id:field.field_id,matches:true,files:[{name:filename,size}],sha256,upload_confirmed:true};
  }
  if(!input||!group||!await input.evaluate((element,root)=>element.type==='file'&&root.contains(element),group))throw new Error('The resume input does not belong to the inspected container.');
  const permit={pageUrl:page.url(),uploadUrl:current.origin+`/wday/calypso/cxs/common/${tenant}/attachments`,generation:state.generation,
    filename,size,sha256,bytes,expires:Date.now()+30000,used:false};state.workdayUpload=permit;
  const response=page.waitForResponse(response=>response.request()===permit.request,{timeout:30000});response.catch(()=>{});
  try {
    check();await input.setInputFiles(filePath,{signal:check.signal});const uploaded=await response;check();
    if(!uploaded.ok()||page.url()!==permit.pageUrl)throw new Error('Workday resume storage rejected the upload or the application changed.');
    const attachment=workdayAttachment(await uploaded.json(),permit),deadline=Date.now()+10000;
    while(!await group.evaluate(workdayUploadVisible,filename)&&Date.now()<deadline){check();await page.waitForTimeout(100);}
    if(!await group.evaluate(workdayUploadVisible,filename))throw new Error('The accepted Workday resume is not visibly attached.');
    check();state.workdayFiles||=[];state.workdayFiles.push({field,group,pageUrl:page.url(),filename,size,sha256,attachment});
    return {field_id:field.field_id,matches:true,files:[{name:filename,size}],sha256,upload_confirmed:true};
  }finally{if(state.workdayUpload===permit)state.workdayUpload=null;}
}

// Run after native File hashing: Workday may clear its input once its accepted attachment is visible.
async function appendWorkdayUploads(page,state,snapshot) {
  for(const saved of state.workdayFiles||[]) {
    if(saved.pageUrl!==page.url())continue;
    const visible=await saved.group.evaluate(workdayUploadVisible,saved.filename).catch(()=>false);
    if(!visible)continue;
    const field=snapshot.fields.find(field=>field.field_id===saved.field.field_id);
    const metadata={remote_uploaded:true,files:[{name:saved.filename,size:saved.size,type:'application/pdf',sha256:saved.sha256}]};
    if(field)Object.assign(field,metadata);
    else snapshot.fields.push({...saved.field,...metadata});
  }
}
module.exports={workdayUploadRequest,workdayAttachment,workdayUploadVisible,uploadWorkdayResume,appendWorkdayUploads};
