const assert=require('node:assert/strict'),crypto=require('node:crypto');
const {workdayUploadRequest,workdayAttachment}=require('../browser/workday-upload.cjs');
const pageUrl='https://example.wd5.myworkdayjobs.com/en-US/Site/job/US/Engineer_JR1/apply/applyManually';
const uploadUrl='https://example.wd5.myworkdayjobs.com/wday/calypso/cxs/common/example/attachments';
const bytes=Buffer.from('%PDF-1.4 synthetic resume'),sha256=crypto.createHash('sha256').update(bytes).digest('hex');
const frame={url:()=>pageUrl,page:()=>({mainFrame:()=>frame})};

// Build both complete multipart requests and Chrome's metadata-only intercepted file representation.
async function fixture(supplied=bytes,extra=false) {
 const form=new FormData();form.append('body',new Blob([supplied],{type:'application/pdf'}),'resume.pdf');
 if(extra)form.append('submit','true');
 const encoded=new Request(uploadUrl,{method:'POST',body:form}),data=Buffer.from(await encoded.arrayBuffer());
 const permit={pageUrl,uploadUrl,filename:'resume.pdf',size:bytes.length,bytes,sha256,generation:3,expires:Date.now()+30000,used:false};
 return {state:{generation:3,paused:false,workdayUpload:permit},permit,request:{frame:()=>frame,url:()=>uploadUrl,method:()=> 'POST',
 headers:()=>({'content-type':encoded.headers.get('content-type')}),postDataBuffer:()=>data}};
}
(async()=>{
 for(const supplied of [bytes,Buffer.alloc(0)]) {
  const {state,request}=await fixture(supplied),result=await workdayUploadRequest(request,state);assert(result);
  const decoded=await new Response(result.postData,{headers:result.headers}).formData();
  assert.deepEqual(Buffer.from(await decoded.get('body').arrayBuffer()),bytes);
  assert.equal(await workdayUploadRequest(request,state),false);
 }
 for(const mutation of [{method:()=> 'PUT'},{url:()=>uploadUrl+'?submit=true'},{url:()=>uploadUrl.replace('/example/','/other/')},
 {url:()=>uploadUrl.replace('/attachments','/submit')},{frame:()=>({...frame})}]) {
  const {state,request}=await fixture();assert.equal(await workdayUploadRequest({...request,...mutation},state),false);
 }
 for(const options of [[Buffer.from('wrong'),false],[bytes,true]]) {
  const {state,request}=await fixture(...options);assert.equal(await workdayUploadRequest(request,state),false);
 }
 const {state,request,permit}=await fixture();state.generation++;assert.equal(await workdayUploadRequest(request,state),false);
 assert.equal(workdayAttachment({id:'accepted-file'},permit).file,'accepted-file');
 for(const body of [{},{id:'file',errors:[{error:'rejected'}]},{id:'file',fileName:'other.pdf'},{id:'file',fileLength:0},
 {id:'file',contentType:{id:'text/plain'}},{id:'file',diagnosis:'infected'}])assert.throws(()=>workdayAttachment(body,permit),/did not confirm/);
 console.log('Workday upload binds exact PDF bytes, filename, employer, frame and generation; one use and response checks passed.');
})().catch(error=>{console.error(error);process.exitCode=1});
