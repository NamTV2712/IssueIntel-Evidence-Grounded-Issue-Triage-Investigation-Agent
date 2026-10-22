/* Source-bound case timeline. Reads and manual links never start a model. */
(() => {
  'use strict';
  const t=window.IssueIntelUI.t,cache=new Map();
  const node=(tag,text,cls)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;return el;};
  const sha=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
  const job=value=>typeof value==='string'&&/^[a-f0-9]{24}$/.test(value);
  let active=null;
  const kinds={investigation:'Phiên điều tra',check_plan:'Kế hoạch phân biệt',check_observation:'Quan sát cho kế hoạch',report_observation:'Quan sát cho báo cáo',hypothesis_link:'Liên kết giả thuyết tự báo',claim_review:'Kiểm chứng từng nhận định',issue_relation:'Đối chiếu hai issue'};
  const comparisons={matches_prediction:'Khớp nhãn dự đoán',conflicts_with_prediction:'Trái nhãn dự đoán',undetermined:'Chưa phân biệt được'};
  const revisions={unknown_commit:'Chưa biết commit đã thử',same_analysis_commit:'Cùng commit phân tích',different_commit:'Khác commit phân tích'};
  const relations={retained:'Giữ lại',refined:'Điều chỉnh',new:'Giả thuyết mới',not_carried:'Không mang sang'};
  const reasons={selected_reported_observations:'Phiên con dùng các quan sát đã chọn',user_context:'Phiên con dùng dữ kiện người dùng bổ sung',selected_repository_context:'Phiên con dùng nguồn repository đã chọn'};
  const safe=value=>value&&value.schema==='investigation_dossier_v1'&&sha(value.sha256)
    &&value.model_calls===0&&value.evaluation_performed===false&&sha(value.source?.identity_sha256)
    &&Array.isArray(value.runs)&&Array.isArray(value.timeline)&&Array.isArray(value.transitions)&&Array.isArray(value.warnings)
    &&Array.isArray(value.hypothesis_links?.events)&&sha(value.hypothesis_links.sha256)
    &&value.hypothesis_links.causal_verified===false&&value.hypothesis_links.independent_human===false;
  function matches(value,binding){
    const selected=value?.runs?.find(row=>row.job_id===binding.job);
    return safe(value)&&value.source.selected_job_id===binding.job&&value.source.selected_pack_sha256===binding.pack
      &&selected?.binding?.pack_sha256===binding.pack&&selected.report?.sha256===binding.report;
  }
  function cancel(){active=null;}
  function current(binding){return active===binding&&binding.target.isConnected!==false;}
  function button(label,fn){const item=node('button',t(label));item.type='button';item.onclick=fn;return item;}
  function detail(label,value){const item=node('details');item.append(node('summary',t(label)),node('pre',JSON.stringify(value,null,2)));return item;}
  function open(binding,id){if(current(binding)&&job(id))document.dispatchEvent(new CustomEvent('issueintel:dossieropen',{detail:{job_id:id}}));}
  function download(binding){
    if(!current(binding)||!matches(binding.entry.data,binding))return;
    const url=URL.createObjectURL(new Blob([JSON.stringify(binding.entry.data,null,2)+'\n'],{type:'application/json'}));
    const link=node('a');link.href=url;link.download='issueintel-'+binding.job+'-dossier.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  async function load(binding){
    if(binding.entry.loading)return;
    binding.entry.loading=true;binding.entry.error=null;paint(binding);
    try{
      const value=await api('/api/jobs/'+binding.job+'/dossier');
      if(!matches(value,binding))throw Error('Unbound dossier');
      binding.entry.data=value;
    }catch(_){binding.entry.error='Chưa đọc được hồ sơ khớp nguồn đang xem. Báo cáo gốc vẫn giữ nguyên.';}
    finally{binding.entry.loading=false;if(active?.entry===binding.entry)paint(active);}
  }
  function field(label,control){const area=node('label',t(label),'dossier-field');area.append(control);return area;}
  function choices(rows,value,change){
    const select=node('select');
    for(const [key,label] of rows){const option=node('option',label);option.value=key;select.append(option);}
    select.value=value;select.onchange=()=>change(select.value);return select;
  }
  function linkForm(binding,transition,parent,child){
    if(!parent.report.hypotheses.length&&!child.report.hypotheses.length)return null;
    const area=node('details',undefined,'dossier-link-form');area.append(node('summary',t('Giải thích liên kết giả thuyết')));
    const key=parent.job_id+':'+child.job_id,entry=binding.entry;
    const draft=entry.drafts[key]||(entry.drafts[key]={kind:parent.report.hypotheses.length&&child.report.hypotheses.length?'refined':child.report.hypotheses.length?'new':'not_carried',
      parent:parent.report.hypotheses[0]?.id||'',child:child.report.hypotheses[0]?.id||'',reason:'',refs:[],supersedes:''});
    area.open=draft.open===true;area.ontoggle=()=>{draft.open=area.open;};
    const form=node('form');area.append(node('p',t('Đây là diễn giải tự báo có nguồn; không xác nhận quan hệ nhân quả.'),'notice'),form);
    const kind=choices(Object.entries(relations).map(([key,label])=>[key,t(label)]),draft.kind,value=>{draft.kind=value;refresh();});
    form.append(field('Loại liên kết',kind));
    const parentSelect=choices([['',t('Không chọn')],...parent.report.hypotheses.map(h=>[h.id,h.id+' · '+h.statement.slice(0,140)])],draft.parent,value=>{draft.parent=value;});
    const childSelect=choices([['',t('Không chọn')],...child.report.hypotheses.map(h=>[h.id,h.id+' · '+h.statement.slice(0,140)])],draft.child,value=>{draft.child=value;});
    form.append(field('Giả thuyết ở phiên cha',parentSelect),field('Giả thuyết ở phiên con',childSelect));
    function refresh(){parentSelect.disabled=draft.kind==='new';childSelect.disabled=draft.kind==='not_carried';}
    refresh();
    const why=node('textarea');why.rows=3;why.maxLength=4000;why.required=true;why.value=draft.reason;why.oninput=()=>{draft.reason=why.value;};form.append(field('Lý do và điều còn chưa chắc chắn',why));
    const refs=node('fieldset');refs.append(node('legend',t('Chọn bằng chứng cho diễn giải (1–12)')));
    for(const [label,run] of [['Phiên cha',parent],['Phiên con',child]])for(const evidence of run.report.evidence){
      const key=run.job_id+':'+evidence.id,check=node('input');check.type='checkbox';check.checked=draft.refs.includes(key);
      check.onchange=()=>{draft.refs=check.checked?[...draft.refs,key]:draft.refs.filter(x=>x!==key);};
      const line=node('label',undefined,'dossier-reference');line.append(check,node('span',t(label)+' · '+evidence.id+' · '+evidence.locator));refs.append(line);
    }
    form.append(refs);
    const events=entry.data.hypothesis_links.events,superseded=new Set(events.map(e=>e.relation.supersedes));
    const earlier=events.filter(e=>e.relation.parent_job_id===parent.job_id&&e.relation.child_job_id===child.job_id&&!superseded.has(e.link_id));
    const correct=choices([['',t('Thêm diễn giải mới')],...earlier.map(e=>[e.link_id,e.link_id+' · '+e.relation.reason.slice(0,100)])],draft.supersedes,value=>{draft.supersedes=value;});
    form.append(field('Đính chính diễn giải trước (tùy chọn)',correct));
    const save=node('button',t('Lưu liên kết có nguồn'));save.type='submit';save.disabled=entry.saving;form.append(save);
    form.onsubmit=async event=>{
      event.preventDefault();if(!current(binding)||entry.saving)return;
      const values=deepDraft(draft);
      if(!values.reason.trim()||values.refs.length<1||values.refs.length>12){entry.error='Ghi lý do và chọn từ 1 đến 12 bằng chứng.';paint(binding);return;}
      const relation={parent_job_id:parent.job_id,child_job_id:child.job_id,kind:values.kind,
        parent_hypothesis_id:values.kind==='new'?null:values.parent||null,
        child_hypothesis_id:values.kind==='not_carried'?null:values.child||null,reason:values.reason,
        evidence_refs:values.refs.map(value=>{const at=value.indexOf(':');return {job_id:value.slice(0,at),evidence_id:value.slice(at+1)};}),supersedes:values.supersedes||null};
      const request={source_pack_sha256:binding.pack,expected_sha256:entry.data.hypothesis_links.sha256,relation};
      entry.saving=true;entry.error=null;save.disabled=true;
      try{
        const value=await api('/api/jobs/'+binding.job+'/dossier-links',request);
        if(!matches(value,binding))throw Error('Unbound relation result');
        entry.data=value;entry.drafts[key]={...draft,reason:'',refs:[],supersedes:''};
      }catch(_){entry.error='Chưa xác nhận được liên kết. Tải lại hồ sơ để kiểm tra lịch sử trước khi gửi lại; bản nháp được giữ.';}
      finally{entry.saving=false;if(active?.entry===entry)paint(active);}
    };
    return area;
  }
  const deepDraft=value=>JSON.parse(JSON.stringify(value));
  function handoffControls(binding){
    const entry=binding.entry,value=entry.data,area=node('details',undefined,'dossier-link-form');
    area.append(node('summary',t('Gói bàn giao ngoại tuyến')));
    area.open=entry.handoffOpen===true;area.ontoggle=()=>{entry.handoffOpen=area.open;};
    area.append(node('p',t('Chọn phiên cần gửi; phiên cha bắt buộc sẽ được thêm. Gói chứa nội dung issue, code và quan sát riêng tư. Chưa gửi dữ liệu ra ngoài.'),'notice'));
    if(!entry.handoffJobs)entry.handoffJobs=[binding.job];
    const choices=node('fieldset');choices.append(node('legend',t('Phiên được chọn')));
    for(const run of value.runs.filter(row=>row.report)){
      const check=node('input');check.type='checkbox';check.checked=entry.handoffJobs.includes(run.job_id);check.disabled=run.job_id===binding.job;
      check.onchange=()=>{entry.handoffJobs=check.checked?[...entry.handoffJobs,run.job_id]:entry.handoffJobs.filter(id=>id!==run.job_id);entry.handoffPreview=null;paint(binding);};
      const row=node('label',undefined,'dossier-reference');row.append(check,node('span',run.job_id+' · '+run.depth+' / '+run.provider));choices.append(row);
    }
    area.append(choices);
    const selection=()=>({source_pack_sha256:binding.pack,dossier_sha256:entry.data.sha256,job_ids:[...entry.handoffJobs].sort()});
    const preview=button('Xem trước nội dung và dung lượng',async()=>{
      if(!current(binding)||entry.handoffBusy)return;
      const selected=selection();entry.handoffOpen=true;entry.handoffBusy=true;entry.error=null;preview.disabled=true;
      try{
        const result=await api('/api/jobs/'+binding.job+'/handoff-preview',selected);
        if(result.schema!=='issueintel_handoff_preview_v1'||!sha(result.sha256)||!sha(result.archive_sha256)
          ||result.source?.selected_job_id!==binding.job||result.source?.selected_pack_sha256!==binding.pack
          ||result.source?.dossier_sha256!==selected.dossier_sha256||result.model_calls!==0
          ||result.contains_private_content!==true||!Array.isArray(result.entries)||!Number.isSafeInteger(result.archive_bytes))throw Error('Unbound handoff');
        if(JSON.stringify(selection())===JSON.stringify(selected))entry.handoffPreview={value:result,selection:selected};
      }catch(_){entry.error='Chưa xem trước được gói. Tải lại hồ sơ nếu nguồn đã đổi.';}
      finally{entry.handoffBusy=false;if(active?.entry===entry)paint(active);}
    });preview.disabled=entry.handoffBusy;area.append(preview);
    const saved=entry.handoffPreview;
    if(saved&&saved.selection.dossier_sha256===value.sha256){
      const p=saved.value;area.append(node('p',t('Dung lượng ZIP')+': '+p.archive_bytes.toLocaleString()+' bytes · '+t('Dung lượng giải nén')+': '+p.uncompressed_bytes.toLocaleString()+' bytes'));
      area.append(node('p',t('Phiên cha tự thêm')+': '+(p.automatic_ancestor_job_ids.join(', ')||'0'),'hash-note'));
      area.append(detail('Danh sách file và hash',p.entries),node('p','ZIP SHA256: '+p.archive_sha256,'hash-note'));
      const save=button('Xác nhận xuất gói riêng tư đã chọn',async()=>{
        if(!current(binding)||entry.handoffBusy||entry.handoffPreview!==saved)return;
        entry.handoffBusy=true;save.disabled=true;
        try{if(await downloadHandoff(binding.job,{selection:saved.selection,preview_sha256:p.sha256,confirmation:'export_selected_private_handoff'},p,
          ()=>current(binding)&&entry.handoffPreview===saved))entry.handoffExported=p.archive_sha256;}
        catch(_){entry.error='Chưa xuất được gói khớp preview. Xem trước lại trước khi thử lại.';}
        finally{entry.handoffBusy=false;if(active?.entry===entry)paint(active);}
      });save.disabled=entry.handoffBusy;area.append(save);
      if(entry.handoffExported===p.archive_sha256){const status=node('p',t('Đã kiểm hash ZIP và yêu cầu trình duyệt tải gói.'),'notice');status.setAttribute('role','status');area.append(status);}
      area.append(node('p',t('Giữ SHA256 ZIP để bên nhận kiểm tra. Mở index.html để đọc; chạy verify.py hoặc verify-handoff để kiểm hash. Hash không xác minh nguyên nhân hay tác giả.'),'notice'));
    }
    return area;
  }
  function hypotheses(run,label){
    const column=node('section');column.append(node('h4',t(label)));
    if(!run.report.hypotheses.length)column.append(node('p',t('Phiên này chỉ truy xuất bằng chứng.'),'notice'));
    for(const h of run.report.hypotheses)column.append(node('p',h.id+' · '+h.statement,'dossier-hypothesis'));
    return column;
  }
  function transitionCard(binding,transition,runs){
    const parent=runs.get(transition.parent_job_id),child=runs.get(transition.child_job_id);
    if(!parent?.report||!child?.report)return node('p',t('Chưa có đủ hai báo cáo để đối chiếu.'),'notice');
    const area=node('article',undefined,'dossier-transition');area.append(node('h3',t('Thay đổi giữa hai phiên')),
      node('p',parent.job_id.slice(0,12)+' → '+child.job_id.slice(0,12),'hash-note'),node('p',t(reasons[transition.reason]),'notice'));
    area.append(button('Mở phiên cha',()=>open(binding,parent.job_id)),button('Mở phiên con',()=>open(binding,child.job_id)));
    const columns=node('div',undefined,'dossier-hypotheses');columns.append(hypotheses(parent,'Giả thuyết ở phiên cha'),hypotheses(child,'Giả thuyết ở phiên con'));area.append(columns);
    if(transition.prediction_delta.length){
      area.append(node('p',t('Chỉ đối chiếu nhãn tự báo A/B với dự đoán đã lưu; điều kiện kiểm tra và nguyên nhân chưa được xác minh.'),'notice'));
      const wrapper=node('div',undefined,'dossier-table-scroll'),table=node('table'),head=node('thead'),titles=node('tr');
      for(const title of ['Quan sát / kiểm tra','Giả thuyết nguồn','Dự đoán','Tự báo','Đối chiếu','Revision đã thử'])titles.append(node('th',t(title)));head.append(titles);table.append(head);
      const body=node('tbody');
      for(const row of transition.prediction_delta){
        const tr=node('tr');tr.append(node('td',row.observation_id+' · '+row.check_id),node('td',row.hypothesis_id),node('td',row.predicted_outcome),node('td',row.reported_outcome),
          node('td',t(comparisons[row.comparison])),node('td',t(revisions[row.revision_applicability])));
        body.append(tr);
      }
      table.append(body);wrapper.append(table);area.append(wrapper,detail('Nguồn quan sát đã dùng',transition.observation_source));
      if(transition.prediction_delta.some(row=>row.superseded_later))area.append(node('p',t('Quan sát đã được đính chính sau khi tạo phiên con; phiên con giữ bản đã chọn.'),'notice'));
    }
    const changed=transition.comparison.evidence.filter(row=>row.status!=='unchanged');
    area.append(detail('Bằng chứng và thay đổi nguồn',changed));
    const links=binding.entry.data.hypothesis_links.events.filter(e=>e.relation.parent_job_id===parent.job_id&&e.relation.child_job_id===child.job_id);
    if(links.length)area.append(detail('Lịch sử liên kết và đính chính',links));
    const form=linkForm(binding,transition,parent,child);if(form)area.append(form);
    return area;
  }
  function paint(binding){
    if(!current(binding))return;
    const {target,entry}=binding;target.replaceChildren();
    const area=node('section',undefined,'dossier');target.append(area);
    area.append(node('h3',t('Hồ sơ xuyên suốt issue')),node('p',t('Các phiên, kế hoạch và quan sát giữ nguyên nguồn; mở hồ sơ không gọi model.'),'notice'));
    const controls=node('div',undefined,'dossier-controls'),reload=button('Tải lại hồ sơ',()=>load(binding));reload.disabled=entry.loading||entry.saving;controls.append(reload);
    if(entry.data)controls.append(button('Xuất hồ sơ JSON',()=>download(binding)));area.append(controls);
    if(entry.loading)area.append(node('p',t('Đang đọc hồ sơ đã lưu…'),'notice'));
    if(entry.error){const status=node('p',t(entry.error),'error');status.setAttribute('role','alert');area.append(status);}
    if(!entry.data)return;
    const value=entry.data,runs=new Map(value.runs.map(row=>[row.job_id,row]));
    area.append(node('p',value.issue.repository+(value.issue.issue_url?' · '+value.issue.issue_url:''),'hash-note'),
      node('p',t('Diễn giải thủ công, quan sát tự báo và dự đoán model chưa xác minh nguyên nhân.'),'notice'));
    if(value.warnings.length)area.append(detail('Nguồn chưa đọc lại được',value.warnings));
    area.append(handoffControls(binding));
    const list=node('ol',undefined,'dossier-timeline');
    for(const event of value.timeline){
      const run=runs.get(event.job_id),row=node('li');
      row.append(node('strong',t(kinds[event.kind])),node('small',event.occurred_at),node('p',event.job_id,'hash-note'));
      if(event.kind==='investigation')row.append(node('span',t(run.status)+' · '+run.depth+' / '+run.provider,'notice'));
      if(event.kind==='issue_relation')row.append(node('p',t('Tác vụ đối chiếu')+': '+event.relation_job_id,'hash-note'),node('span',t(event.status),'notice'));
      if(event.event){
        const value=event.event.observation||event.event.relation;
        row.append(node('p',event.event.observation_id||event.event.link_id),detail('Nội dung và nguồn đã ghi',value));
      }
      if(run?.report)row.append(button('Mở phiên này',()=>open(binding,event.job_id)));
      else row.append(node('p',t('Phiên chưa hoàn tất hoặc nguồn không đọc lại được; chưa có kết luận mới.'),'notice'));
      list.append(row);
    }
    area.append(list);
    if(!value.transitions.length)area.append(node('p',t('Chưa có cặp phiên cha–con hoàn tất để đối chiếu.'),'notice'));
    for(const transition of value.transitions)area.append(transitionCard(binding,transition,runs));
    if(value.claim_reviews?.length)area.append(detail('Các bản kiểm chứng nhận định đã lưu',value.claim_reviews));
    if(value.issue_relations?.length)area.append(detail('Các bản đối chiếu issue đã lưu',value.issue_relations));
    area.append(detail('Định danh và hash hồ sơ',{source:value.source,sha256:value.sha256,links_sha256:value.hypothesis_links.sha256}));
  }
  function render(target,id,expected){
    cancel();
    if(!expected?.available){target.append(node('p',t('Hồ sơ này chỉ dành cho điều tra product đã kiểm nguồn, ngoài benchmark.'),'notice'));return;}
    if(!job(id)||!sha(expected.pack_sha256)||!sha(expected.report_sha256))return;
    const key=id+':'+expected.pack_sha256+':'+expected.report_sha256;
    if(!cache.has(key))cache.set(key,{data:null,drafts:{},loading:false,saving:false,error:null});
    const binding={target,job:id,pack:expected.pack_sha256,report:expected.report_sha256,entry:cache.get(key)};active=binding;
    paint(binding);if(!binding.entry.data&&!binding.entry.loading)load(binding);
  }
  window.IssueIntelDossier={render,cancel};
})();
