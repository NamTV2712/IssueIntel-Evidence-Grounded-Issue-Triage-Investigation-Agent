/* Manual reports stay separate from model claims and benchmark labels. */
(() => {
  'use strict';
  const t=window.IssueIntelUI.t;
  const node=(tag,text,cls)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;return el;};
  const same=(a,b)=>JSON.stringify(canonical(a))===JSON.stringify(canonical(b));
  const canonical=value=>Array.isArray(value)?value.map(canonical):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])):value;
  const hash=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
  const outcomes={not_run:'Chưa chạy',inconclusive:'Chưa rõ kết quả',reported_match:'Tự báo khớp mong đợi',reported_difference:'Tự báo khác mong đợi'};
  const flags={provenance:'user_report_unverified',executed_by_issueintel:false,causal_verified:false,fix_verified:false,independent_human:false,evaluation_performed:false};
  const drafts=new Map(),pending=new Set();let active=null;
  const keyFor=(job,report)=>job+':'+report.sha256;

  function valid(journal,job,report,expected){
    try{
      if(!journal||!report||journal.schema!=='check_journal_v1'||report.schema!=='investigation_report_v1'
        ||!hash(journal.sha256)||!hash(report.sha256)||!Number.isInteger(journal.revision)||journal.revision<0||journal.revision>100
        ||journal.source.job_id!==job||report.source.job_id!==job||!/^[a-f0-9]{24}$/.test(job)
        ||journal.source.protocol!=='benchmark_base'||journal.source.strict_issue_time_verified!==false
        ||!same(journal.source,{...report.source,report_sha256:report.sha256})
        ||!same(journal.issue,{case_id:report.issue.case_id,repository:report.issue.repository,issue_url:report.issue.issue_url})
        ||!same(journal.checks,report.next_checks)||!Array.isArray(journal.events)||journal.revision!==journal.events.length
        ||!Object.entries(flags).every(([name,value])=>journal[name]===value)
        ||!expected||!['pack_sha256','query_sha256','snapshot_sha256','commit_sha'].every(name=>journal.source[name]===expected[name]))return false;
      const seen=new Map(),superseded=new Set();
      for(const [index,event] of journal.events.entries()){
        const observation=event.observation,check=journal.checks.find(row=>row.id===observation?.check_id);
        if(event.schema!=='manual_check_observation_v1'||event.revision!==index+1||event.observation_id!=='observation_'+(index+1)
          ||!same(event.source,journal.source)||!hash(event.sha256)||!hash(event.parent_sha256)||!check
          ||!Object.entries(flags).every(([name,value])=>event[name]===value)
          ||!hash(event.check_binding?.text_sha256)||event.check_binding.id!==check.id
          ||!same(event.check_binding.hypothesis_ids,check.hypothesis_ids)||!same(event.check_binding.evidence_ids,check.evidence_ids)
          ||!Object.hasOwn(outcomes,observation.outcome)||typeof event.recorded_at!=='string'||!Number.isFinite(Date.parse(event.recorded_at))
          ||!['conditions','expected_result','observed_result','interpretation'].every(name=>typeof observation[name]==='string')
          ||!(observation.tested_commit_sha===null||/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(observation.tested_commit_sha)))return false;
        if(observation.outcome==='not_run'?(observation.observed_result.trim()||observation.tested_commit_sha!==null||!observation.interpretation.trim()):
          ['conditions','expected_result','observed_result'].some(name=>!observation[name].trim()))return false;
        if(observation.supersedes!==null){const target=seen.get(observation.supersedes);
          if(!target||superseded.has(observation.supersedes)||target.observation.check_id!==observation.check_id)return false;
          superseded.add(observation.supersedes);}
        seen.set(event.observation_id,event);
      }
      return journal.revision===0?journal.parent_sha256===null:journal.parent_sha256===journal.events.at(-1).parent_sha256;
    }catch(_){return false;}
  }
  function cancel(){active=null;}
  function completed(job,report){const key=keyFor(job,report);pending.delete(key);drafts.delete(key);}
  function citations(ids,binding){const area=node('div',undefined,'journal-citations');for(const id of ids){const button=node('button',id,'citation');button.type='button';
    button.setAttribute('aria-label',t('Xem bằng chứng {id}',{id}));button.onclick=()=>{if(active===binding)document.dispatchEvent(new CustomEvent('issueintel:showevidence',{detail:{job_id:binding.job,evidence_id:id}}));};area.append(button);}return area;}

  function render(target,journal,job,report,expected,options={}){
    cancel();const area=node('section',undefined,'check-journal');target.append(area);area.append(node('h3',t('Nhật ký kiểm tra giả thuyết')));
    if(options.available===false){area.append(node('p',t('Nhật ký dành cho điều tra sản phẩm ngoài benchmark. Checklist hiện có vẫn giữ riêng.'),'notice'));return;}
    if(!valid(journal,job,report,expected)){area.append(node('p',t('Chưa đọc được nhật ký khớp báo cáo. Tải lại trước khi ghi nhận; dữ liệu đang nhập được giữ trong phiên trình duyệt.'),'notice'));
      if(options.reload){const retry=node('button',t('Tải lại nhật ký'));retry.type='button';retry.onclick=options.reload;area.append(retry);}return;}
    const binding={job,report:report.sha256,journal:journal.sha256},key=keyFor(job,report);active=binding;
    area.append(node('p',t('Ghi nhận thủ công, chưa xác minh. IssueIntel không chạy code/test, xác nhận nguyên nhân hoặc bản sửa, và không tạo nhãn benchmark.'),'notice'),
      node('p',journal.issue.repository+' · '+journal.issue.case_id,'journal-identity'),node('p',t('Commit của báo cáo: ')+journal.source.commit_sha,'hash-note'));
    const toolbar=node('div',undefined,'journal-actions');
    for(const [label,callback] of [['Tải lại nhật ký',options.reload],['Xuất nhật ký JSON',()=>options.export?.('json')],['Xuất báo cáo kèm nhật ký',()=>options.export?.('md')]]){
      const button=node('button',t(label));button.type='button';button.onclick=()=>{if(active===binding)callback?.();};toolbar.append(button);}
    area.append(toolbar,node('p',t('Phiên bản nhật ký: {revision}',{revision:journal.revision}),'hash-note'));
    if(!journal.checks.length){area.append(node('p',t('Báo cáo chưa có bước kiểm tra để ghi nhận.'),'notice'));return;}
    const draft=drafts.get(key)||{check_id:journal.checks[0].id,outcome:'not_run',conditions:'',expected_result:'',observed_result:'',interpretation:'',tested_commit_sha:null,supersedes:null};
    drafts.set(key,draft);
    const form=node('form',undefined,'journal-form'),fields={},notice=node('p',undefined,'notice');notice.setAttribute('role','status');notice.setAttribute('aria-live','polite');
    const field=(name,label,tag='textarea',max=2000)=>{const box=node('div',undefined,'journal-field'),caption=node('label',t(label)),input=node(tag);
      input.id='journal-'+name;caption.htmlFor=input.id;if(tag==='textarea'){input.rows=3;input.maxLength=max;}fields[name]=input;box.append(caption,input);form.append(box);return input;};
    const selected=field('check_id','Bước kiểm tra trong báo cáo','select');
    for(const check of journal.checks){const option=node('option',check.id+' · '+(check.text.length>120?check.text.slice(0,120)+'…':check.text));option.value=check.id;selected.append(option);}selected.value=draft.check_id;selected.disabled=Boolean(draft.supersedes);
    const context=node('div',undefined,'journal-check-context');form.append(context);
    const showCheck=()=>{const check=journal.checks.find(row=>row.id===selected.value);context.replaceChildren(node('p',check.text,'journal-source'),
      node('p',t('Giả thuyết liên quan: ')+(check.hypothesis_ids.join(' · ')||t('Chưa có giả thuyết')),'notice'),citations(check.evidence_ids,binding));};showCheck();
    const outcome=field('outcome','Kết quả tự báo cáo','select');for(const [value,label] of Object.entries(outcomes)){const option=node('option',t(label));option.value=value;outcome.append(option);}outcome.value=draft.outcome;
    field('conditions','Điều kiện, đầu vào và môi trường thử');field('expected_result','Kết quả mong đợi');field('observed_result','Kết quả quan sát được','textarea',4000);
    const commit=field('tested_commit_sha','Commit thực sự đã thử (tùy chọn)','input');commit.maxLength=64;commit.pattern='([a-f0-9]{40}|[a-f0-9]{64})';commit.autocomplete='off';commit.spellcheck=false;
    form.append(node('p',t('Chỉ nhập full SHA nếu biết; để trống khi chưa biết. Không mặc định dùng commit báo cáo, không kiểm Git hoặc xác nhận bạn đã chạy.'),'notice'));
    field('interpretation','Diễn giải hoặc lý do chưa chạy');
    for(const name of ['conditions','expected_result','observed_result','interpretation'])fields[name].value=draft[name];commit.value=draft.tested_commit_sha||'';
    const remember=()=>{for(const name of Object.keys(fields))draft[name]=fields[name].value;draft.tested_commit_sha=commit.value||null;drafts.set(key,draft);};
    const requirements=()=>{const unrun=outcome.value==='not_run';fields.observed_result.disabled=unrun;commit.disabled=unrun;
      for(const name of ['conditions','expected_result','observed_result'])fields[name].required=!unrun;fields.interpretation.required=unrun;};requirements();
    for(const input of Object.values(fields))input.oninput=remember;
    selected.onchange=()=>{remember();showCheck();};outcome.onchange=()=>{if(outcome.value==='not_run'){fields.observed_result.value='';commit.value='';}requirements();remember();};
    const buttons=node('div',undefined,'journal-actions'),save=node('button',t(pending.has(key)?'Đang lưu quan sát…':'Lưu quan sát mới'));save.type='submit';save.disabled=pending.has(key)||journal.revision>=100;buttons.append(save);
    if(draft.supersedes){form.prepend(node('p',t('Đang đính chính {id}; bản ghi cũ vẫn được giữ.',{id:draft.supersedes}),'notice'));
      const reset=node('button',t('Hủy đính chính'));reset.type='button';reset.disabled=pending.has(key);reset.onclick=()=>{if(active===binding){drafts.delete(key);options.rerender?.();}};buttons.append(reset);}
    if(pending.has(key))for(const input of Object.values(fields))input.disabled=true;
    form.append(buttons,notice);
    const editor=node('details',undefined,'journal-editor');editor.open=!journal.events.length||Boolean(draft.supersedes)||pending.has(key)
      ||['conditions','expected_result','observed_result','interpretation'].some(name=>Boolean(draft[name]))||draft.check_id!==journal.checks[0].id;
    editor.append(node('summary',t(draft.supersedes?'Đính chính quan sát':'Ghi quan sát mới')),form);area.append(editor);
    if(journal.revision>=100)area.append(node('p',t('Nhật ký đã đạt giới hạn 100 bản ghi; vẫn có thể đọc và xuất.'),'notice'));
    form.onsubmit=async event=>{event.preventDefault();if(active!==binding||pending.has(key)||journal.revision>=100)return;remember();
      pending.add(key);save.disabled=true;for(const input of Object.values(fields))input.disabled=true;save.textContent=t('Đang lưu quan sát…');
      try{await options.save?.({expected_sha256:journal.sha256,observation:{...draft}});}
      catch(_){pending.delete(key);if(active===binding){for(const input of Object.values(fields))input.disabled=false;selected.disabled=Boolean(draft.supersedes);requirements();save.disabled=false;save.textContent=t('Lưu quan sát mới');
        notice.textContent=t('Chưa lưu được. Tải lại nhật ký nếu phiên bản đã đổi; nội dung đang nhập vẫn được giữ.');}}
    };
    const superseded=new Set(journal.events.map(event=>event.observation.supersedes));
    const history=node('section',undefined,'journal-history');history.append(node('h4',t('Quan sát đã ghi nhận')));area.append(history);
    if(!journal.events.length)history.append(node('p',t('Chưa có quan sát thủ công. Bước đề xuất chưa đồng nghĩa đã chạy.'),'notice'));
    for(const event of journal.events.slice().reverse()){
      const item=node('details',undefined,'journal-observation'),observation=event.observation,old=superseded.has(event.observation_id);
      item.append(node('summary',event.observation_id+' · '+observation.check_id+' · '+t(outcomes[observation.outcome])+(old?' · '+t('Đã được đính chính'):'')),
        node('p',t('Ghi lúc: ')+event.recorded_at,'hash-note'),node('p',t('Commit tự báo đã thử: ')+(observation.tested_commit_sha||t('Chưa cung cấp')),'hash-note'));
      if(observation.supersedes)item.append(node('p',t('Đính chính bản ghi: ')+observation.supersedes,'notice'));
      for(const [label,name] of [['Điều kiện, đầu vào và môi trường thử','conditions'],['Kết quả mong đợi','expected_result'],['Kết quả quan sát được','observed_result'],['Diễn giải hoặc lý do chưa chạy','interpretation']])item.append(node('h5',t(label)),node('p',observation[name]||t('Chưa cung cấp'),'journal-source'));
      item.append(citations(event.check_binding.evidence_ids,binding));
      if(!old){const correct=node('button',t('Đính chính quan sát'));correct.type='button';correct.disabled=pending.has(key);correct.onclick=()=>{if(active===binding){drafts.set(key,{...observation,supersedes:event.observation_id});options.rerender?.();}};item.append(correct);}
      item.append(node('p','Event SHA256 '+event.sha256,'hash-note'));history.append(item);
    }
    const provenance=node('details');provenance.append(node('summary',t('Nguồn và giới hạn nhật ký')),node('p',t('Lịch sử và hash phát hiện thay đổi so với nguồn đã lưu; không xác thực danh tính người ghi hay tính đúng của kết quả.'),'notice'));
    for(const [name,value] of [['Job',job],['Pack SHA256',journal.source.pack_sha256],['Report SHA256',report.sha256],['Journal SHA256',journal.sha256]])provenance.append(node('p',name+' '+value,'hash-note'));area.append(provenance);
  }
  document.addEventListener('issueintel:packchange',cancel);
  window.IssueIntelCheckJournal=Object.freeze({render,valid,cancel,completed,failed:(job,report)=>pending.delete(keyFor(job,report))});
})();
