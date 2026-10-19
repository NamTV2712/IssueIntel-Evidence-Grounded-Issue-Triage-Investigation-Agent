const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {webcrypto,createHash}=require('node:crypto');
const app=fs.readFileSync(path.join(__dirname,'../src/issueintel/interfaces/static/app.js'),'utf8');
const code=app.slice(app.indexOf('async function downloadHandoff('),app.indexOf('function node('));
const bytes=Buffer.from('Synthetic portable handoff bytes'),hash=createHash('sha256').update(bytes).digest('hex'),job='a'.repeat(24);
const preview={source:{dossier_sha256:'b'.repeat(64)},archive_sha256:hash,archive_bytes:bytes.length};
function boot({header=preview.source.dossier_sha256,content=bytes,ok=true,current=()=>true}={}){
 const calls=[],downloads=[];
 const context={token:'synthetic-session',crypto:webcrypto,Uint8Array,Blob,URL:{createObjectURL:blob=>{downloads.push(blob);return 'blob:synthetic';},revokeObjectURL:()=>{}},setTimeout:fn=>fn(),
  node:()=>({click(){}}),fetch:async(...args)=>{calls.push(args);return {ok,headers:{get:()=>header},arrayBuffer:async()=>Uint8Array.from(content).buffer};}};
 vm.runInNewContext(code,context);return {calls,downloads,run:()=>context.downloadHandoff(job,{confirmation:'export_selected_private_handoff'},preview,current)};
}
test('handoff download verifies exact source header, SHA256 and byte size before saving',async()=>{
 const a=boot();await a.run();assert.equal(a.calls.length,1);assert.equal(a.downloads.length,1);
 assert.equal(a.calls[0][0],'/api/jobs/'+job+'/handoff-export');assert.equal(a.calls[0][1].headers['X-IssueIntel-Token'],'synthetic-session');
 assert.equal(a.calls[0][1].method,'POST');assert.equal(a.downloads[0].size,bytes.length);
});
test('changed source, changed bytes and failed export never trigger a download or retry',async()=>{
 for(const options of [{header:'c'.repeat(64)},{content:Buffer.from('Changed bytes')},{ok:false}]){
  const a=boot(options);await assert.rejects(a.run());assert.equal(a.downloads.length,0);assert.equal(a.calls.length,1);
 }
});
test('a completed response after navigation or selection change is discarded',async()=>{
 const a=boot({current:()=>false});await a.run();assert.equal(a.downloads.length,0);assert.equal(a.calls.length,1);
});
