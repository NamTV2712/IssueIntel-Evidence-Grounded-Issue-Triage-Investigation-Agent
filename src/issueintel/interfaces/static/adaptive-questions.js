/* Explicitly requested, source-bound questions from saved hypotheses and code. */
(() => {
  'use strict';
  const get=id=>document.getElementById(id),t=window.IssueIntelUI.t;
  const el=(tag,text,cls)=>{const item=document.createElement(tag);if(text!==undefined)item.textContent=text;if(cls)item.className=cls;return item;};
  const digest=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
  const jobId=value=>typeof value==='string'&&/^[a-f0-9]{24}$/.test(value);
  const lang=()=>document.documentElement.lang==='vi'?'vi':'en';
  let selected=null,binding=null,source=null,pack=null,allowed=false,eligible=false,version=0,timer=null;
  let busy=false,posting=false,posted=false,rows=null,activeJob=null,bundle=null,answers=new Map(),notice=null;
  const supplied=()=>bundle?bundle.questions.filter(question=>(answers.get(question.id)||'').trim()):[];
  function valid(value){
    if(value?.schema!=='adaptive_clarifications_v1'||!digest(value.sha256)||value.source?.source_job_id!==selected
      ||value.source.source_pack_sha256!==source?.source_pack_sha256||value.source.query_sha256!==source?.query_sha256
      ||value.source.snapshot_sha256!==pack?.snapshot?.snapshot_sha256||value.source.commit_sha!==pack?.snapshot?.commit_sha||!Array.isArray(value.questions)
      ||value.questions.length<1||value.questions.length>4)return false;
    const ids=new Set(),hypotheses=new Set((pack.annotation?.hypotheses||[]).map(item=>item.id)),evidence=new Set((pack.evidence||[]).map(item=>item.id));
    return value.questions.every(question=>{
      if(!question||!/^probe:q[1-4]$/.test(question.id)||ids.has(question.id))return false;ids.add(question.id);
      for(const field of ['prompts','purpose','answer_use'])for(const locale of ['vi','en'])
        if(typeof question[field]?.[locale]!=='string'||!question[field][locale].trim()||question[field][locale].length>6000)return false;
      return Array.isArray(question.hypothesis_ids)&&question.hypothesis_ids.length>=2&&question.hypothesis_ids.length<=5
        &&new Set(question.hypothesis_ids).size===question.hypothesis_ids.length&&question.hypothesis_ids.every(id=>hypotheses.has(id))
        &&Array.isArray(question.evidence_ids)&&question.evidence_ids.length>0&&question.evidence_ids.length<=6
        &&new Set(question.evidence_ids).size===question.evidence_ids.length&&question.evidence_ids.every(id=>evidence.has(id));
    });
  }
  function clearConsents(){get('adaptive-generate-consent').checked=false;get('adaptive-answer-groq').checked=false;}
  function controls(){
    const locked=busy||posting||posted;
    get('adaptive-load').disabled=busy||posting||!allowed;
    get('adaptive-generate').disabled=locked||!!activeJob||!eligible||!get('adaptive-generate-consent').checked;
    get('adaptive-generate-consent').disabled=locked||!!activeJob||!eligible;
    get('adaptive-cancel').hidden=!activeJob||!['queued','running'].includes(activeJob.status);
    get('adaptive-cancel').disabled=posting;
    get('adaptive-answer-submit').disabled=locked||!bundle||!supplied().length;
    get('adaptive-answer-groq').disabled=locked;get('adaptive-answer-depth').disabled=locked;
    for(const input of get('adaptive-questions').querySelectorAll('textarea'))input.disabled=locked;
    get('adaptive-count').textContent=bundle?t('Đã trả lời {count}/{total} câu',{count:supplied().length,total:bundle.questions.length}):'';
  }
  function render(){
    get('adaptive-parent').textContent=selected?t('Phiên nguồn: ')+selected:'';
    get('adaptive-message').textContent=notice?t(notice):'';
    get('adaptive-requires').hidden=eligible;
    const saved=get('adaptive-saved');saved.replaceChildren();
    if(rows){
      if(!rows.length)saved.append(el('p',t('Chưa có bộ câu hỏi riêng cho phiên nguồn này.'),'notice'));
      for(const row of rows){
        const line=el('div',undefined,'adaptive-saved-row'),button=el('button',t('Mở tác vụ câu hỏi {id}',{id:row.id}));button.type='button';button.disabled=busy||posting;
        button.onclick=()=>openJob(row.id);line.append(button,el('span',t(row.status),'badge'));saved.append(line);
      }
    }
    const progress=get('adaptive-progress');progress.replaceChildren();
    if(activeJob){
      progress.append(el('p',t('Tác vụ câu hỏi: {id} · {status}',{id:activeJob.id,status:t(activeJob.status)}),'hash-note'));
      if(activeJob.accounting&&window.IssueIntelUsage){const usage=el('div');window.IssueIntelUsage.render(usage,activeJob.accounting);progress.append(usage);}
      else progress.append(el('p',t('Chưa đọc được metadata chi phí.'),'notice'));
      if(['failed','interrupted','cancelled'].includes(activeJob.status))progress.append(el('p',t('Tác vụ chưa tạo được bộ câu hỏi hợp lệ; receipt được giữ lại. Mỗi báo cáo nguồn chỉ có một lượt tạo câu hỏi. Muốn thử lượt mới, cần tạo phiên điều tra nguồn mới.'),'notice'));
    }
    const area=get('adaptive-questions');area.replaceChildren();get('adaptive-answer-controls').hidden=!bundle;
    const provenance=get('adaptive-provenance');provenance.replaceChildren();
    if(bundle){
      provenance.append(el('p',t('SHA256 báo cáo nguồn: {sha}',{sha:source.source_pack_sha256}),'hash-note'),
        el('p','Commit '+bundle.source.commit_sha,'hash-note'),el('p','Query SHA256 '+bundle.source.query_sha256,'hash-note'),el('p','Snapshot SHA256 '+bundle.source.snapshot_sha256,'hash-note'),
        el('p',t('SHA256 bộ câu hỏi: {sha}',{sha:bundle.sha256}),'hash-note'));
      const download=el('button',t('Xuất bộ câu hỏi JSON'));download.type='button';
      const exportJob=activeJob.id;download.onclick=()=>downloadPath('/api/jobs/'+exportJob+'/questions.json','issueintel-'+exportJob+'-questions.json');provenance.append(download);
      for(const question of bundle.questions){
        const card=el('article',undefined,'adaptive-question'),label=el('label',question.prompts[lang()]),input=el('textarea');
        input.id='adaptive-answer-'+question.id.replace(':','-');input.rows=3;input.maxLength=4000;input.value=answers.get(question.id)||'';label.htmlFor=input.id;
        input.setAttribute('aria-describedby','adaptive-answer-note');
        input.oninput=()=>{answers.set(question.id,input.value);get('adaptive-answer-groq').checked=false;if(!busy&&!posting&&!posted){notice=null;get('adaptive-message').textContent='';}controls();};
        card.append(el('p',question.id,'eyebrow'),label,el('p',t('Vì sao cần hỏi: ')+question.purpose[lang()],'notice'),
          el('p',t('Câu trả lời giúp phân biệt: ')+question.answer_use[lang()],'notice'));
        const refs=el('details',undefined,'adaptive-references');refs.append(el('summary',t('Giả thuyết và bằng chứng liên quan')));
        for(const id of question.hypothesis_ids){const hypothesis=pack.annotation.hypotheses.find(item=>item.id===id);refs.append(el('p',id+' · '+hypothesis.statement,'notice'));}
        const citations=el('div',undefined,'adaptive-citations');
        for(const id of question.evidence_ids){const button=el('button',id,'citation');button.type='button';const sourceJob=selected;
          button.onclick=()=>document.dispatchEvent(new CustomEvent('issueintel:showevidence',{detail:{job_id:sourceJob,evidence_id:id}}));citations.append(button);}
        refs.append(citations);card.append(refs,input);area.append(card);
      }
    }
    controls();
  }
  function stopTimer(){if(timer!==null){clearTimeout(timer);timer=null;}}
  async function poll(identifier,current){
    try{
      const row=await api('/api/jobs/'+identifier);
      if(current!==version)return;
      if(row?.id!==identifier||!['queued','running','completed','failed','interrupted','cancelled'].includes(row.status))throw Error('Invalid question job');
      activeJob=row;
      if(row.status==='completed'){
        const value=await api('/api/jobs/'+identifier+'/questions.json');if(current!==version)return;
        if(!valid(value))throw Error('Invalid question source');
        bundle=value;answers.clear();busy=false;notice='Câu hỏi do model đề xuất, chưa được kiểm người. Trả lời những gì bạn biết hoặc ghi chưa biết.';render();return;
      }
      if(['queued','running'].includes(row.status)){busy=true;render();timer=setTimeout(()=>{timer=null;return poll(identifier,current);},1000);return;}
      busy=false;clearConsents();notice=null;render();
    }catch(_){if(current===version){busy=false;clearConsents();notice='Chưa đọc được tác vụ hoặc bộ câu hỏi có nguồn. Mở tác vụ đã lưu để kiểm tra; không tự gọi lại model.';render();}}
  }
  async function openJob(identifier){
    if(!allowed||busy||posting||!jobId(identifier))return;
    stopTimer();const current=++version;bundle=null;activeJob=null;answers.clear();posted=false;busy=true;notice=null;clearConsents();render();await poll(identifier,current);
  }
  document.addEventListener('issueintel:packchange',event=>{
    const detail=event.detail,next=detail.job_id,value=detail.intake,nextAllowed=!!detail.adaptive_available;
    const nextBinding=nextAllowed&&jobId(next)&&digest(value?.source_pack_sha256)&&digest(value?.query_sha256)&&digest(detail.pack?.snapshot?.snapshot_sha256)
      ?[next,value.source_pack_sha256,value.query_sha256,detail.pack.snapshot.snapshot_sha256].join(':'):null;
    get('adaptive-panel').hidden=!nextAllowed;
    if(next===selected&&nextAllowed===allowed&&nextBinding===binding)return;
    stopTimer();version++;selected=next;allowed=nextAllowed;binding=nextBinding;source=binding?value:null;pack=binding?detail.pack:null;
    eligible=!!binding&&(pack.annotation?.hypotheses?.length||0)>=2;busy=false;posting=false;posted=false;rows=null;activeJob=null;bundle=null;answers.clear();
    notice=allowed&&!binding?'Chưa đọc được bộ câu hỏi có nguồn. Mở lại phiên từ lịch sử để kiểm tra.':null;
    clearConsents();get('adaptive-answer-depth').value='quick';render();
  });
  document.addEventListener('issueintel:languagechange',()=>{clearConsents();render();});
  get('adaptive-generate-consent').onchange=controls;
  get('adaptive-answer-depth').onchange=()=>{get('adaptive-answer-groq').checked=false;};
  get('adaptive-load').onclick=async()=>{
    if(!allowed||!binding||busy||posting)return;
    const current=version;busy=true;notice=null;render();
    try{
      const value=await api('/api/jobs/'+selected+'/adaptive-questions');if(current!==version)return;
      if(value?.source_job_id!==selected||value.source_pack_sha256!==source.source_pack_sha256||!Array.isArray(value.question_jobs))throw Error('Invalid saved list');
      const ids=new Set();for(const row of value.question_jobs){if(!jobId(row?.id)||ids.has(row.id)||!['queued','running','completed','failed','interrupted','cancelled'].includes(row.status))throw Error('Invalid saved job');ids.add(row.id);}
      rows=value.question_jobs;
    }catch(_){if(current===version)notice='Chưa đọc được danh sách câu hỏi đã lưu. Không có lời gọi model mới.';}
    finally{if(current===version){busy=false;render();}}
  };
  get('adaptive-generate').onclick=async()=>{
    if(!eligible||!binding||busy||posting||posted||activeJob||!get('adaptive-generate-consent').checked)return;
    if(get('run-button').disabled){notice='Đang theo dõi tác vụ khác; kiểm tra lịch sử trước khi tiếp tục.';render();return;}
    const current=version,request={source_job_id:selected,source_pack_sha256:source.source_pack_sha256,confirmation:'generate_questions_with_api_cost'};
    busy=true;bundle=null;answers.clear();activeJob=null;clearConsents();notice='Đang tạo câu hỏi từ issue, giả thuyết và bằng chứng đã lưu…';render();
    try{
      const row=await api('/api/adaptive-questions',request);if(current!==version)return;
      if(!jobId(row?.id))throw Error('Invalid question job');
      await poll(row.id,current);
    }catch(_){if(current===version){busy=false;notice='Chưa xác nhận được tác vụ câu hỏi. Mở danh sách đã lưu và kiểm tra receipt; gửi lại cùng nguồn chỉ nhận lại tác vụ cũ, không tạo lượt gọi mới.';render();}}
  };
  get('adaptive-cancel').onclick=async()=>{
    if(!activeJob||!['queued','running'].includes(activeJob.status)||posting)return;
    const current=version,identifier=activeJob.id;posting=true;controls();
    try{await api('/api/jobs/'+identifier+'/cancel',{});if(current===version)notice='Đã yêu cầu hủy; lời gọi đang gửi có thể vẫn tính phí.';}
    catch(_){if(current===version)notice='Chưa gửi được yêu cầu hủy; mở lại tác vụ để kiểm tra trạng thái.';}
    finally{if(current===version){posting=false;render();}}
  };
  get('adaptive-answer-form').onsubmit=async event=>{
    event.preventDefault();if(!bundle||busy||posting||posted||!binding||!activeJob||activeJob.status!=='completed')return;
    const answered=supplied();if(!answered.length){notice='Trả lời ít nhất một câu, hoặc dùng phần bổ sung dữ kiện tự do.';render();return;}
    if(get('run-button').disabled){notice='Đang theo dõi tác vụ khác; kiểm tra lịch sử trước khi tiếp tục.';render();return;}
    if(answered.some(question=>answers.get(question.id).length>4000||answers.get(question.id).includes('\0'))){notice='Mỗi câu trả lời tối đa 4.000 ký tự và không chứa ký tự rỗng.';render();return;}
    const current=version,request={mode:'clarification',parent_job_id:selected,parent_pack_sha256:source.source_pack_sha256,
      question_job_id:activeJob.id,questions_sha256:bundle.sha256,language:lang(),
      answers:answered.map(question=>({question_id:question.id,answer:answers.get(question.id)})),
      provider:get('adaptive-answer-groq').checked?'groq':'none',depth:get('adaptive-answer-depth').value};
    posting=true;notice='Đang tạo phiên điều tra với câu trả lời của bạn…';render();
    try{
      const row=await api('/api/investigations',request);if(current!==version)return;
      if(!jobId(row?.id))throw Error('Invalid investigation job');posted=true;clearConsents();notice='Đã mở phiên mới; báo cáo nguồn được giữ nguyên.';
      document.dispatchEvent(new CustomEvent('issueintel:clarificationcreated',{detail:{job_id:row.id}}));
    }catch(_){if(current===version){clearConsents();notice='Chưa tạo được phiên. Câu trả lời được giữ lại; mở lại báo cáo nguồn từ lịch sử nếu hash đã đổi, rồi chủ động gửi lại.';}}
    finally{if(current===version){posting=false;render();}}
  };
})();
