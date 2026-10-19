/* Explicit, source-bound completion of saved paid stages. Never starts a model. */
(() => {
  const get=id=>document.getElementById(id),t=window.IssueIntelUI.t;
  const sha=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
  const jobId=value=>typeof value==='string'&&/^[a-f0-9]{24}$/.test(value);
  const node=(tag,text,cls)=>{const value=document.createElement(tag);if(text!==undefined)value.textContent=text;if(cls)value.className=cls;return value;};
  const attempts=new Map();
  let selected=null,identity=null,preview=null,revision=0,loading=false,notice=null;
  const terminal=['failed','interrupted','cancelled'];
  function source(row){
    if(!row||!jobId(row.id)||!terminal.includes(row.status)||!['example','local','github'].includes(row.mode)||row.provider!=='groq'||!['quick','deep'].includes(row.depth))return null;
    const input=row.stage_checkpoints?.verification_input?.sha256,verification=row.stage_checkpoints?.verification?.sha256;
    return sha(input)&&sha(verification)?{id:row.id,input,verification}:null;
  }
  function key(value){return value?[value.id,value.input,value.verification].join(':'):null;}
  function validPreview(value,bound){
    return value&&value.available===true&&value.source_job_id===bound.id
      &&value.verification_input_sha256===bound.input&&value.verification_sha256===bound.verification
      &&value.model_calls===0&&value.no_api_required===true
      &&typeof value.model==='string'&&value.model.length>0&&value.model.length<=200
      &&typeof value.commit_sha==='string'&&/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(value.commit_sha);
  }
  function openCreated(id){
    if(get('run-button')?.disabled){notice='Đang theo dõi tác vụ khác; mở lại phiên đã tạo sau khi tác vụ đó kết thúc.';render();return;}
    document.dispatchEvent(new CustomEvent('issueintel:finalizationcreated',{detail:{job_id:id}}));
  }
  function render(){
    const area=get('finalization-content');area.replaceChildren();if(!selected)return;
    const bound=selected,binding=identity,view=revision,attempt=attempts.get(binding),busy=loading||attempt?.pending===true;
    area.append(node('p',t('Phiên nguồn để kết xuất: {id}',{id:bound.id}),'hash-note'),
      node('p',t('Generation và verifier đã được lưu. Có thể tạo hồ sơ mới từ hai checkpoint này mà không gọi API hoặc đọc lại Git.'),'notice'));
    const status=node('p',undefined,'notice finalization-status');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
    if(attempt?.created){
      area.append(node('p',t('Đã tạo phiên kết xuất: {id}',{id:attempt.created}),'hash-note'));
      const open=node('button',t('Mở phiên kết xuất đã tạo'));open.type='button';open.onclick=()=>{if(identity===binding)openCreated(attempt.created);};area.append(open);
    }else{
      const check=node('button',t(loading?'Đang kiểm hai checkpoint đã lưu…':'Kiểm tra checkpoint để kết xuất'));check.type='button';check.disabled=busy;
      check.onclick=async()=>{
        if(loading||attempts.get(binding)?.pending||identity!==binding||view!==revision)return;
        loading=true;preview=null;notice=null;render();
        try{
          const value=await api('/api/jobs/'+bound.id+'/finalization');
          if(identity!==binding||view!==revision)return;
          if(validPreview(value,bound))preview=value;
          else notice=value?.available===false?'Chưa có đủ checkpoint hợp lệ để kết xuất phiên này.':'Nguồn hoặc checkpoint không còn khớp; mở lại phiên nguồn và kiểm tra trước khi kết xuất.';
        }catch(_){if(identity===binding&&view===revision)notice='Chưa kiểm tra được checkpoint đã lưu; kiểm tra server local rồi thử lại chủ động.';}
        finally{if(identity===binding&&view===revision){loading=false;render();}}
      };area.append(check);
      if(preview&&validPreview(preview,bound)){
        const approved=preview;
        area.append(node('p',t('Phiên kết xuất mới: 0 lời gọi model; không cần API key. Không chạy verifier mới.'),'finalization-zero-call'),
          node('p',t('Các lời gọi và chi phí lịch sử vẫn thuộc phiên nguồn; tạo hồ sơ này không hoàn lại hoặc đặt lại chúng.'),'notice'),
          node('p',t('Model của kết quả đã lưu: {model}',{model:preview.model}),'notice'),
          node('p','Commit '+preview.commit_sha,'hash-note'));
        const hashes=node('details',undefined,'finalization-bindings');hashes.append(node('summary',t('Hai checkpoint sẽ được dùng')),
          node('p','verification_input · SHA256 '+bound.input,'hash-note'),node('p','verification · SHA256 '+bound.verification,'hash-note'));area.append(hashes);
        const submit=node('button',t(attempt?.pending?'Đang tạo phiên kết xuất…':'Kết xuất hồ sơ từ checkpoint đã lưu'));submit.type='button';submit.disabled=busy;
        submit.onclick=async()=>{
          if(identity!==binding||view!==revision||preview!==approved||!validPreview(approved,bound)||attempts.get(binding)?.pending||attempts.get(binding)?.created)return;
          if(get('run-button')?.disabled){notice='Đang theo dõi tác vụ khác; mở lại phiên đã tạo sau khi tác vụ đó kết thúc.';render();return;}
          const pending={pending:true};attempts.set(binding,pending);notice=null;render();
          try{
            const value=await api('/api/jobs/'+bound.id+'/finalization',{verification_input_sha256:bound.input,verification_sha256:bound.verification,confirmation:'finalize_saved_checkpoints_without_api'});
            if(!jobId(value?.id)||value.id===bound.id||value.mode!=='finalization'||value.provider!=='none'||value.depth!=='export'||!['queued','running','completed','failed','cancelled','interrupted'].includes(value.status))throw Error('Invalid finalization job');
            pending.pending=false;pending.created=value.id;
            if(identity===binding&&view===revision)openCreated(value.id);
          }catch(_){
            pending.pending=false;
            if(identity===binding&&view===revision){preview=null;notice='Chưa xác nhận được phiên kết xuất. Kiểm tra lịch sử trước khi chủ động kiểm checkpoint và gửi lại.';}
          }finally{if(identity===binding&&view===revision)render();}
        };area.append(submit);
      }
    }
    if(notice)status.textContent=t(notice);
    else if(attempt?.pending)status.textContent=t('Đang tạo phiên kết xuất…');
    area.append(status);
  }
  function show(row){
    const value=source(row),next=key(value);
    get('finalization-panel').hidden=!value;
    if(next!==identity){selected=value;identity=next;preview=null;notice=null;loading=false;revision++;}
    render();
  }
  // Translations may redraw the preview, but never retrieve it or create a job.
  document.addEventListener('issueintel:languagechange',render);
  window.IssueIntelFinalization={show};
})();
