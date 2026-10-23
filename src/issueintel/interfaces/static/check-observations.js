/* Manual outcomes create explicit, source-bound children; they never certify a test. */
(() => {
  'use strict';
  const t=window.IssueIntelUI.t;
  const node=(tag,text,cls)=>{const item=document.createElement(tag);if(text!==undefined)item.textContent=text;if(cls)item.className=cls;return item;};
  const canonical=value=>Array.isArray(value)?value.map(canonical):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])):value;
  const same=(left,right)=>JSON.stringify(canonical(left))===JSON.stringify(canonical(right));
  const hash=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
  const job=value=>typeof value==='string'&&/^[a-f0-9]{24}$/.test(value);
  const flags={provenance:'user_report_unverified',executed_by_issueintel:false,causal_verified:false,fix_verified:false,independent_human:false,evaluation_performed:false};
  const outcomes={not_run:'Chưa chạy',A:'Tự báo kết quả A',B:'Tự báo kết quả B',other:'Kết quả khác A/B',inconclusive:'Chưa rõ kết quả'};
  let area=null,binding=null,source=null,plan=null,report=null,journal=null,draft=null,notice=null;
  let generation=0,view=0,loading=false,saving=false,creating=false,consent=false,depth='quick',created=null;
  const selected=new Set();
  const drafts=new Map();let draftState=null;

  function sourceFor(value,identifier,parent,parentId){
    if(value?.schema!=='product_check_plan_v1'||!job(identifier)||!job(parentId)||!hash(value.sha256)
      ||parent?.schema!=='investigation_report_v1'||parent.source?.job_id!==parentId||!hash(parent.sha256)
      ||value.source?.source_job_id!==parentId||value.source.source_pack_sha256!==parent.source.pack_sha256
      ||value.source.report_sha256!==parent.sha256||value.source.query_sha256!==parent.source.query_sha256
      ||value.source.snapshot_sha256!==parent.source.snapshot_sha256||value.source.commit_sha!==parent.source.commit_sha
      ||!['pack_sha256','query_sha256','snapshot_sha256'].every(key=>hash(parent.source[key]))
      ||!/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(parent.source.commit_sha)
      ||!hash(value.source.annotation_sha256)||!hash(value.source.sha256)||!Array.isArray(value.checks)
      ||!parent.issue||typeof parent.issue.case_id!=='string'||/^(?:verified:|live:)/.test(parent.issue.case_id)
      ||parent.source.protocol!=='benchmark_base'||parent.source.strict_issue_time_verified!==false)return null;
    return {plan_job_id:identifier,plan_sha256:value.sha256,source_job_id:parentId,source_pack_sha256:parent.source.pack_sha256,
      report_sha256:parent.sha256,query_sha256:parent.source.query_sha256,snapshot_sha256:parent.source.snapshot_sha256,
      commit_sha:parent.source.commit_sha,annotation_sha256:value.source.annotation_sha256,source_projection_sha256:value.source.sha256};
  }
  function valid(value,valuePlan=plan,identifier=source?.plan_job_id,parent=report,parentId=source?.source_job_id){
    try{
      const expected=sourceFor(valuePlan,identifier,parent,parentId);
      if(!expected||value?.schema!=='supplemental_check_observation_journal_v1'||!hash(value.sha256)||!same(value.source,expected)
        ||!same(value.checks,valuePlan.checks)||!Number.isInteger(value.revision)||value.revision<0||value.revision>100
        ||!Array.isArray(value.events)||value.events.length!==value.revision||!Object.entries(flags).every(([key,item])=>value[key]===item))return false;
      const seen=new Map(),superseded=new Set();
      for(const [index,event] of value.events.entries()){
        const observation=event.observation,check=valuePlan.checks.find(row=>row.id===observation?.check_id);
        if(event.schema!=='manual_check_plan_observation_v1'||event.revision!==index+1||event.observation_id!=='observation_'+(index+1)
          ||!same(event.source,expected)||!hash(event.sha256)||!hash(event.parent_sha256)||!check
          ||!same(Object.keys(event.check_binding||{}).sort(),['id','sha256'])||event.check_binding.id!==check.id||!hash(event.check_binding.sha256)
          ||!Object.entries(flags).every(([key,item])=>event[key]===item)||!Object.hasOwn(outcomes,observation.outcome)
          ||typeof event.recorded_at!=='string'||!Number.isFinite(Date.parse(event.recorded_at))
          ||!['conditions','observed_result','interpretation'].every(key=>typeof observation[key]==='string'&&!observation[key].includes('\0')&&observation[key].length<=(key==='observed_result'?4000:2000))
          ||!(observation.tested_commit_sha===null||/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(observation.tested_commit_sha)))return false;
        if(observation.outcome==='not_run'?(observation.observed_result.trim()||observation.tested_commit_sha!==null||!observation.interpretation.trim()):
          (!observation.conditions.trim()||!observation.observed_result.trim()))return false;
        if(observation.supersedes!==null){const previous=seen.get(observation.supersedes);
          if(!previous||superseded.has(observation.supersedes)||previous.observation.check_id!==observation.check_id)return false;superseded.add(observation.supersedes);}
        seen.set(event.observation_id,event);
      }
      return value.revision===0?value.parent_sha256===null:value.parent_sha256===value.events.at(-1).parent_sha256;
    }catch(_){return false;}
  }
  const activeEvents=()=>{const old=new Set((journal?.events||[]).map(row=>row.observation.supersedes));return (journal?.events||[]).filter(row=>!old.has(row.observation_id));};
  function freshDraft(){return {check_id:plan.checks[0]?.id||'',outcome:'not_run',conditions:'',observed_result:'',interpretation:'',tested_commit_sha:null,supersedes:null};}
  function current(token){return token===generation&&!!binding;}
  function rememberDraft(){if(draftState)Object.assign(draftState,{draft,depth,journal,saving,creating,created,notice});}
  function cancel(){rememberDraft();area=null;view++;consent=false;}
  function button(label,action,disabled=false,values={}){const target=area,revision=view,item=node('button',t(label,values));item.type='button';item.disabled=disabled;
    item.onclick=()=>{if(area===target&&view===revision&&!item.disabled)return action();};return item;}
  function citations(ids){const target=area,revision=view,list=node('div',undefined,'product-check-citations');for(const id of ids){const item=button('Xem bằng chứng {id}',()=>{
      if(area===target&&view===revision)document.dispatchEvent(new CustomEvent('issueintel:showevidence',{detail:{job_id:source.source_job_id,evidence_id:id}}));},false,{id});item.textContent=id;item.className='citation';list.append(item);}return list;}

  function redraw(){
    if(!area)return;view++;area.replaceChildren();area.append(node('h3',t('Quan sát kiểm tra phân biệt')));
    if(!binding){area.append(node('p',t('Cần kế hoạch kiểm tra đã lưu khớp báo cáo nguồn để ghi quan sát.'),'notice'));return;}
    area.append(node('p',t('Ghi nhận thủ công, chưa xác minh. IssueIntel không chạy code/test, xác nhận nguyên nhân hoặc bản sửa, và không tạo nhãn benchmark.'),'notice'),
      node('p',t('Quan sát chỉ đi vào phiên điều tra mới khi bạn chọn và chủ động tạo phiên. Báo cáo, kế hoạch và lịch sử gốc được giữ nguyên.'),'notice'),
      node('p',report.issue.repository+' · '+report.issue.case_id,'check-observation-identity'),node('p',t('Commit của báo cáo: ')+source.commit_sha,'hash-note'));
    const toolbar=node('div',undefined,'check-observation-actions');toolbar.append(button('Tải lại quan sát kiểm tra',load,loading||saving||creating));
    if(journal)for(const format of ['json','md'])toolbar.append(button(format==='json'?'Xuất quan sát kiểm tra JSON':'Xuất quan sát kiểm tra Markdown',()=>exportJournal(format),loading||saving));area.append(toolbar);
    if(loading)area.append(node('p',t('Đang đọc quan sát đã lưu; không gọi model.'),'notice'));
    if(journal){area.append(node('p',t('Phiên bản nhật ký: {revision}',{revision:journal.revision}),'hash-note'));
      if(plan.checks.length)renderEditor();else area.append(node('p',t('Kế hoạch chưa có kiểm tra bổ sung để ghi nhận.'),'notice'));
      renderHistory();renderFollowup();
      const provenance=node('details',undefined,'check-observation-provenance');provenance.append(node('summary',t('Nguồn và giới hạn nhật ký')),
        node('p',t('Lịch sử và hash phát hiện thay đổi so với nguồn đã lưu; không xác thực danh tính người ghi hay tính đúng của kết quả.'),'notice'));
      for(const [caption,value] of [['Check plan job',source.plan_job_id],['Report SHA256',source.report_sha256],['Check plan SHA256',source.plan_sha256],['Check observations SHA256',journal.sha256]])provenance.append(node('p',caption+' '+value,'hash-note'));area.append(provenance);
    }
    const status=node('p',notice?t(notice):'','notice');status.setAttribute('role','status');status.setAttribute('aria-live','polite');area.append(status);
  }
  function renderEditor(){
    draft=draft||freshDraft();const locked=loading||saving||creating||journal.revision>=100,target=area,revision=view;
    const form=node('form',undefined,'check-observation-form'),fields={},editor=node('details',undefined,'check-observation-editor');
    const field=(key,caption,tag='textarea')=>{const row=node('div',undefined,'check-observation-field'),label=node('label',t(caption)),input=node(tag);input.id='check-observation-'+key;label.htmlFor=input.id;
      if(tag==='textarea'){input.rows=3;input.maxLength=key==='observed_result'?4000:2000;}input.disabled=locked;fields[key]=input;row.append(label,input);form.append(row);return input;};
    const check=field('check_id','Kiểm tra bổ sung đã lưu','select');for(const item of plan.checks){const option=node('option',item.id+' · '+item.procedure.slice(0,120));option.value=item.id;check.append(option);}check.value=draft.check_id;check.disabled=locked||!!draft.supersedes;
    const preview=node('div',undefined,'check-observation-preview');form.append(preview);
    const show=()=>{const item=plan.checks.find(row=>row.id===check.value);preview.replaceChildren();if(!item)return;
      for(const [caption,key] of [['Cách kiểm tra','procedure'],['Giả định và điều kiện kiểm soát','assumptions'],['Kết quả A','outcome_a'],['Kết quả B','outcome_b']])preview.append(node('strong',t(caption)),node('p',item[key],'check-observation-source'));preview.append(citations(item.evidence_ids));};show();
    const outcome=field('outcome','Kết quả tự báo cáo','select');for(const [key,caption] of Object.entries(outcomes)){const option=node('option',t(caption));option.value=key;outcome.append(option);}outcome.value=draft.outcome;
    field('conditions','Điều kiện, đầu vào và môi trường thử');field('observed_result','Kết quả quan sát được');
    const commit=field('tested_commit_sha','Commit thực sự đã thử (tùy chọn)','input');commit.maxLength=64;commit.pattern='(?:[a-f0-9]{40}|[a-f0-9]{64})';commit.autocomplete='off';commit.spellcheck=false;
    form.append(node('p',t('Chỉ nhập full SHA nếu biết; để trống khi chưa biết. Không mặc định dùng commit báo cáo, không kiểm Git hoặc xác nhận bạn đã chạy.'),'field-note'));
    field('interpretation','Diễn giải hoặc lý do chưa chạy');for(const key of ['conditions','observed_result','interpretation'])fields[key].value=draft[key];commit.value=draft.tested_commit_sha||'';
    const usable=()=>area===target&&revision===view;
    const remember=()=>{if(!usable())return;for(const key of Object.keys(fields))draft[key]=key==='tested_commit_sha'?(fields[key].value||null):fields[key].value;};
    const requirements=()=>{const unrun=outcome.value==='not_run';fields.observed_result.disabled=locked||unrun;commit.disabled=locked||unrun;fields.conditions.required=!unrun;fields.observed_result.required=!unrun;fields.interpretation.required=unrun;};requirements();
    for(const input of Object.values(fields))input.oninput=remember;
    check.onchange=()=>{remember();show();};outcome.onchange=()=>{if(!usable())return;if(outcome.value==='not_run'){fields.observed_result.value='';commit.value='';}requirements();remember();};
    if(draft.supersedes)form.prepend(node('p',t('Đang đính chính {id}; bản ghi cũ vẫn được giữ.',{id:draft.supersedes}),'notice'));
    const actions=node('div',undefined,'check-observation-actions'),submit=node('button',t(saving?'Đang lưu quan sát…':'Lưu quan sát mới'));submit.type='submit';submit.disabled=locked;actions.append(submit);
    if(draft.supersedes)actions.append(button('Hủy đính chính',()=>{draft=freshDraft();redraw();},locked));form.append(actions);
    form.onsubmit=event=>{event.preventDefault();if(!usable()||locked)return;remember();return save();};
    editor.open=!journal.events.length||!!draft.supersedes||saving||['conditions','observed_result','interpretation'].some(key=>!!draft[key]);editor.append(node('summary',t(draft.supersedes?'Đính chính quan sát':'Ghi quan sát mới')),form);area.append(editor);
    if(journal.revision>=100)area.append(node('p',t('Nhật ký đã đạt giới hạn 100 bản ghi; vẫn có thể đọc và xuất.'),'notice'));
  }
  function renderHistory(){
    const history=node('section',undefined,'check-observation-history'),old=new Set(journal.events.map(row=>row.observation.supersedes));history.append(node('h4',t('Quan sát đã ghi nhận')));
    if(!journal.events.length)history.append(node('p',t('Chưa có quan sát thủ công. Bước đề xuất chưa đồng nghĩa đã chạy.'),'notice'));
    for(const event of journal.events.slice().reverse()){const observation=event.observation,item=node('details',undefined,'check-observation-event'),superseded=old.has(event.observation_id);
      item.append(node('summary',event.observation_id+' · '+observation.check_id+' · '+t(outcomes[observation.outcome])+(superseded?' · '+t('Đã được đính chính'):'')),
        node('p',t('Ghi lúc: ')+event.recorded_at,'hash-note'),node('p',t('Commit tự báo đã thử: ')+(observation.tested_commit_sha||t('Chưa cung cấp')),'hash-note'));
      if(observation.supersedes)item.append(node('p',t('Đính chính bản ghi: ')+observation.supersedes,'notice'));
      for(const [caption,key] of [['Điều kiện, đầu vào và môi trường thử','conditions'],['Kết quả quan sát được','observed_result'],['Diễn giải hoặc lý do chưa chạy','interpretation']])item.append(node('h5',t(caption)),node('p',observation[key]||t('Chưa cung cấp'),'check-observation-source'));
      if(!superseded)item.append(button('Đính chính quan sát',()=>{draft={...observation,supersedes:event.observation_id};consent=false;redraw();},loading||saving||creating||journal.revision>=100));
      item.append(node('p','Event SHA256 '+event.sha256,'hash-note'));history.append(item);
    }area.append(history);
  }
  function renderFollowup(){
    if(report?.execution?.status==='waiting_for_user'){
      area.append(node('p',t('Phiên Planned đang chờ. Dùng biểu mẫu trong Trace để giữ ngân sách của chuỗi điều tra.'),'notice'));
      return;
    }
    const section=node('section',undefined,'check-observation-followup'),eligible=activeEvents().filter(row=>row.observation.outcome!=='not_run'),locked=loading||saving||creating||!!created;
    section.append(node('h4',t('Điều tra tiếp từ quan sát đã chọn')),node('p',t('Chỉ chọn bản ghi đang có hiệu lực và đã báo kết quả. Phiên mới truy xuất lại code ở commit báo cáo; commit tự báo đã thử vẫn chỉ là dữ kiện chưa xác minh.'),'notice'));
    section.append(node('p',t('Chọn tối đa 3 quan sát đang có hiệu lực cho mỗi phiên tiếp nối.'),'field-note'));
    if(!eligible.length)section.append(node('p',t('Cần ít nhất một quan sát đã lưu khác “chưa chạy” để tiếp tục.'),'notice'));
    for(const event of eligible){const label=node('label',undefined,'checkbox'),input=node('input'),target=area,revision=view;input.type='checkbox';input.checked=selected.has(event.observation_id);input.disabled=locked||(!input.checked&&selected.size>=3);
      input.id='check-observation-select-'+event.observation_id;
      input.onchange=()=>{if(area!==target||view!==revision||input.disabled)return;if(input.checked){if(selected.size>=3)return;selected.add(event.observation_id);}else selected.delete(event.observation_id);consent=false;redraw();area?.querySelector?.('#'+input.id)?.focus?.();};label.append(input,node('span',event.observation_id+' · '+event.observation.check_id+' · '+t(outcomes[event.observation.outcome])));section.append(label);}
    const row=node('div',undefined,'check-observation-field'),label=node('label',t('Độ rộng điều tra tiếp')),input=node('select');input.id='check-observation-depth';label.htmlFor=input.id;for(const [value,caption] of [['quick','Nhanh'],['deep','Sâu']]){const option=node('option',t(caption));option.value=value;input.append(option);}input.value=depth;input.disabled=locked;
    const target=area,revision=view;input.onchange=()=>{if(area===target&&view===revision&&!locked&&['quick','deep'].includes(input.value)){depth=input.value;consent=false;redraw();area?.querySelector?.('#check-observation-depth')?.focus?.();}};row.append(label,input);section.append(row);
    const cost=node('label',undefined,'checkbox'),groq=node('input');groq.type='checkbox';groq.id='check-observation-groq';groq.checked=consent;groq.disabled=locked||!selected.size;
    groq.onchange=()=>{if(area===target&&view===revision&&!groq.disabled){consent=groq.checked;redraw();area?.querySelector?.('#check-observation-groq')?.focus?.();}};cost.append(groq,node('span',t('Tôi đồng ý gửi nguồn và các quan sát đã chọn tới Groq để điều tra tiếp, có chi phí API.')));section.append(cost,
      node('p',t('Mặc định không gọi model. Việc lưu hoặc xuất quan sát không tự mở điều tra.'),'field-note'),button(creating?'Đang tạo phiên từ quan sát…':'Tạo phiên từ quan sát đã chọn',createChild,locked||!selected.size));
    if(created)section.append(node('p',t('Đã tạo phiên từ quan sát: {id}',{id:created}),'hash-note'),button('Mở phiên đã tạo',()=>{
      document.dispatchEvent(new CustomEvent('issueintel:checkfollowupcreated',{detail:{job_id:created}}));}));area.append(section);
  }

  async function load(){
    if(!binding||loading||saving||creating)return;const token=generation;loading=true;notice=null;redraw();
    try{const value=await api('/api/jobs/'+source.plan_job_id+'/check-observations.json');if(!current(token))return;if(!valid(value))throw Error('Invalid source-bound observations');journal=value;
      const active=new Set(activeEvents().filter(row=>row.observation.outcome!=='not_run').map(row=>row.observation_id));for(const id of selected)if(!active.has(id))selected.delete(id);consent=false;
    }catch(_){if(current(token))notice='Chưa đọc được quan sát khớp kế hoạch. Tải lại trước khi ghi; nội dung đang nhập vẫn được giữ.';}
    finally{if(current(token)){loading=false;redraw();}}
  }
  async function save(){
    if(!binding||!journal||loading||saving||creating||journal.revision>=100||!draft)return;
    const observation={...draft},state=draftState,key=binding,savedPlan=plan,savedReport=report,savedSource={...source},previous=journal;
    if(!Object.hasOwn(outcomes,observation.outcome)||!plan.checks.some(row=>row.id===observation.check_id)
      ||['conditions','observed_result','interpretation'].some(key=>typeof observation[key]!=='string'||observation[key].includes('\0')||observation[key].length>(key==='observed_result'?4000:2000))
      ||!(observation.tested_commit_sha===null||/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(observation.tested_commit_sha))
      ||(observation.outcome==='not_run'?(!observation.interpretation.trim()||observation.observed_result.trim()||observation.tested_commit_sha!==null):(!observation.conditions.trim()||!observation.observed_result.trim()))){notice='Điền điều kiện và kết quả quan sát; nếu chưa chạy, ghi lý do và để trống kết quả/commit.';redraw();return;}
    saving=true;consent=false;notice=null;rememberDraft();redraw();
    try{const value=await api('/api/jobs/'+savedSource.plan_job_id+'/check-observations',{expected_sha256:previous.sha256,observation});
      if(!valid(value,savedPlan,savedSource.plan_job_id,savedReport,savedSource.source_job_id)
        ||value.revision!==previous.revision+1||value.parent_sha256!==previous.sha256
        ||!same(value.events.slice(0,-1),previous.events)||!same(value.events.at(-1).observation,observation))throw Error('Invalid saved observations');
      Object.assign(state,{journal:value,draft:null,created:null,notice:'Đã lưu quan sát thủ công; chưa xác minh kết quả.'});
    }catch(_){state.notice='Chưa lưu được. Tải lại nhật ký nếu phiên bản đã đổi; nội dung đang nhập vẫn được giữ.';}
    finally{state.saving=false;if(draftState===state&&binding===key){journal=state.journal;draft=state.draft||freshDraft();created=state.created;notice=state.notice;saving=false;selected.clear();redraw();}}
  }
  async function createChild(){
    if(!binding||!journal||loading||saving||creating||created||!selected.size||selected.size>3)return;
    if(document.getElementById('run-button')?.disabled){notice='Đang theo dõi tác vụ khác; kiểm tra lịch sử trước khi tiếp tục.';redraw();return;}
    const ids=activeEvents().filter(row=>row.observation.outcome!=='not_run'&&selected.has(row.observation_id)).map(row=>row.observation_id);if(ids.length!==selected.size)return;
    const request={plan_job_id:source.plan_job_id,plan_sha256:plan.sha256,journal_sha256:journal.sha256,observation_ids:ids,provider:consent?'groq':'none',depth};
    const state=draftState,key=binding;creating=true;consent=false;notice=null;rememberDraft();redraw();
    try{const value=await api('/api/check-followups',request);if(!job(value?.id))throw Error('Invalid child job');state.created=value.id;state.notice=null;
      if(draftState===state&&binding===key){created=value.id;notice=null;if(area)document.dispatchEvent(new CustomEvent('issueintel:checkfollowupcreated',{detail:{job_id:value.id}}));}}
    catch(_){state.notice='Chưa xác nhận được phiên mới. Mở lịch sử trước khi chủ động gửi lại; các quan sát vẫn được giữ.';}
    finally{state.creating=false;if(draftState===state&&binding===key){created=state.created;notice=state.notice;creating=false;redraw();}}
  }
  async function exportJournal(format){
    if(!['json','md'].includes(format)||!valid(journal))return;const token=generation,value=journal,identifier=source.plan_job_id;
    const matches=()=>current(token)&&journal===value;
    try{const response=await fetch('/api/jobs/'+identifier+'/check-observations.'+format,{headers:{'X-IssueIntel-Token':document.querySelector('meta[name="issueintel-session"]').content}});
      if(!matches())return;if(!response.ok||response.headers.get('X-IssueIntel-Report-SHA256')!==source.report_sha256||response.headers.get('X-IssueIntel-Check-Plan-SHA256')!==source.plan_sha256
        ||response.headers.get('X-IssueIntel-Check-Observations-SHA256')!==value.sha256)throw Error('Invalid exported observation source');
      let body;if(format==='json'){const exported=await response.json();if(!matches())return;if(exported.sha256!==value.sha256||!valid(exported))throw Error('Invalid export');body=new Blob([JSON.stringify(exported,null,2)],{type:'application/json'});}else body=await response.blob();
      if(!matches())return;const url=URL.createObjectURL(body),link=node('a');link.href=url;link.download='issueintel-'+identifier+'-check-observations.'+format;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }catch(_){if(matches()){notice='Chưa xuất được quan sát đúng phiên bản; tải lại để kiểm tra nguồn.';redraw();}}
  }
  function render(target,value,identifier,parent,parentId){
    const next=sourceFor(value,identifier,parent,parentId),key=next?JSON.stringify(next):null;
    if(key!==binding){rememberDraft();generation++;binding=key;source=next;plan=value;report=parent;
      if(key&&!drafts.has(key))drafts.set(key,{draft:null,depth:'quick',journal:null,saving:false,creating:false,created:null,notice:null});
      draftState=drafts.get(key)||null;draft=draftState?.draft||null;depth=draftState?.depth||'quick';
      saving=draftState?.saving===true;creating=draftState?.creating===true;created=draftState?.created||null;
      journal=saving||creating?draftState.journal:null;notice=journal?draftState?.notice||null:null;selected.clear();loading=false;consent=false;}
    area=node('section',undefined,'check-observations');target.append(area);redraw();if(binding&&!journal&&!loading&&!notice)return load();
  }
  document.addEventListener('issueintel:packchange',event=>{if(event.detail?.job_id!==source?.source_job_id||event.detail?.intake?.source_pack_sha256!==source?.source_pack_sha256){rememberDraft();generation++;binding=null;source=null;plan=null;report=null;journal=null;draft=null;draftState=null;selected.clear();cancel();}});
  document.addEventListener('issueintel:languagechange',()=>{consent=false;redraw();});
  window.IssueIntelCheckObservations=Object.freeze({render,valid,cancel});
})();
