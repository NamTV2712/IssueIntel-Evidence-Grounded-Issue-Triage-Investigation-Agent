/* Explicit, checkpoint-bound verifier continuation; never automatic spending. */
(() => {
  const get=id=>document.getElementById(id),t=window.IssueIntelUI.t;
  const el=(tag,text,cls)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(cls)node.className=cls;return node;};
  let selected=null,preview=null,version=0,busy=false,notice=null;
  function render(){
    const area=get('resume-content');area.replaceChildren();if(!selected)return;
    area.append(el('p',t('Phiên nguồn: ')+selected,'hash-note'));
    const check=el('button',t('Kiểm tra checkpoint để tiếp tục'));check.type='button';check.disabled=busy;
    check.onclick=async()=>{
      const source=selected,current=++version;busy=true;notice=null;render();
      try{const value=await api('/api/jobs/'+source+'/resume');if(current===version){preview=value;notice=value.available?null:'Chưa có checkpoint verifier hợp lệ cho phiên này.';}}
      catch(_){if(current===version)notice='Chưa có checkpoint verifier hợp lệ cho phiên này.';}
      finally{if(current===version){busy=false;render();}}
    };area.append(check);
    if(notice)area.append(el('p',t(notice),'notice'));
    if(!preview?.available)return;
    area.append(el('p',t('Dùng lại {count} giả thuyết đã lưu; chỉ tiếp tục verifier.',{count:preview.hypotheses}),'notice'),
      el('p','Commit '+preview.commit_sha,'hash-note'),el('p',preview.model+' · '+t('Cận output: {tokens} token',{tokens:preview.max_output_tokens}),'notice'),
      el('p',t('Bước tiếp theo gọi API và có thể tính phí. Lượt trước bị lỗi hoặc gián đoạn vẫn có thể đã bị tính phí.'),'notice'));
    const label=el('label',undefined,'checkbox'),approve=el('input');approve.type='checkbox';approve.id='resume-approval';
    label.append(approve,el('span',t('Tôi đồng ý tiếp tục verifier với chi phí API theo cấu hình hiện tại.')));
    const submit=el('button',t('Tiếp tục verifier'));submit.type='button';submit.disabled=true;approve.onchange=()=>submit.disabled=!approve.checked||busy;
    const bound=preview,source=selected,current=version;
    submit.onclick=async()=>{
      if(!approve.checked||busy||source!==selected)return;
      if(get('run-button').disabled){notice='Đang theo dõi tác vụ khác; kiểm tra lịch sử trước khi tiếp tục.';render();return;}
      busy=true;submit.disabled=true;check.disabled=true;
      try{
        const job=await api('/api/jobs/'+source+'/resume',{checkpoint_sha256:bound.checkpoint_sha256,confirmation:'resume_verification_with_api_cost'});
        if(current===version){notice='Đã mở tác vụ tiếp tục; không sinh lại giả thuyết.';document.dispatchEvent(new CustomEvent('issueintel:resumecreated',{detail:{job_id:job.id}}));}
      }catch(_){if(current===version)notice='Chưa tiếp tục được; kiểm tra checkpoint, model hoặc hàng đợi.';}
      finally{if(current===version){busy=false;render();}}
    };area.append(label,submit);
  }
  function show(row){
    const allowed=['failed','interrupted','cancelled'].includes(row.status)&&['example','local','github'].includes(row.mode)&&!!row.stage_checkpoints?.verification_input&&!row.stage_checkpoints?.verification;
    get('resume-panel').hidden=!allowed;
    if(selected!==row.id){selected=row.id;preview=null;notice=null;busy=false;version++;}
    if(allowed)render();
  }
  document.addEventListener('issueintel:languagechange',render);
  window.IssueIntelResume={show};
})();
