/* Deterministic questions from the saved intake; answers remain user claims. */
(() => {
  'use strict';
  const get=id=>document.getElementById(id),t=window.IssueIntelUI.t;
  const el=(tag,text,cls)=>{const item=document.createElement(tag);if(text!==undefined)item.textContent=text;if(cls)item.className=cls;return item;};
  const fields=new Set(['observed_behavior','expected_behavior','reproduction','environment']);
  const digest=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
  const language=()=>document.documentElement.lang==='vi'?'vi':'en';
  let selected=null,available=false,binding=null,bundle=null,intake=null,answers=new Map(),generation=0,posting=false,posted=false,notice=null;
  function valid(source,value){
    const questions=value?.clarifications,seen=new Set();
    if(!/^[a-f0-9]{24}$/.test(source)||value?.job_id!==source||!digest(value.source_pack_sha256)||!digest(value.query_sha256)
      ||questions?.schema!=='issue_clarifications_v1'||questions.query_sha256!==value.query_sha256||!digest(questions.sha256)
      ||!Array.isArray(questions.questions)||questions.questions.length>4)return false;
    return questions.questions.every(question=>{
      if(!question||!fields.has(question.field)||question.id!=='missing:'+question.field||seen.has(question.id)
        ||question.reason!=='cue_not_found'||!['en','vi'].every(locale=>typeof question.prompts?.[locale]==='string'
          &&question.prompts[locale].trim()&&question.prompts[locale].length<=2000))return false;
      seen.add(question.id);return true;
    });
  }
  function answered(){return bundle?bundle.questions.filter(question=>(answers.get(question.id)||'').trim()):[];}
  function controls(){
    const count=answered().length,busy=posting||posted;
    get('clarifications-submit').disabled=busy||!bundle||!count;
    get('clarifications-depth').disabled=busy;
    get('clarifications-groq').disabled=busy;
    get('clarifications-count').textContent=bundle?t('Đã trả lời {count}/{total} câu',{count,total:bundle.questions.length}):'';
    for(const input of get('clarifications-questions').querySelectorAll('textarea'))input.disabled=busy;
  }
  function render(){
    get('clarifications-parent').textContent=selected?t('Phiên nguồn: ')+selected:'';
    get('clarifications-message').textContent=notice?t(notice):'';
    const area=get('clarifications-questions');area.replaceChildren();
    get('clarifications-controls').hidden=!bundle?.questions.length;
    get('clarifications-empty').hidden=!bundle||!!bundle.questions.length;
    const provenance=get('clarifications-provenance');provenance.replaceChildren();
    if(bundle){
      provenance.append(el('p',t('SHA256 báo cáo nguồn: {sha}',{sha:intake.source_pack_sha256}),'hash-note'),
        el('p','Query SHA256 '+bundle.query_sha256,'hash-note'),el('p',t('SHA256 bộ câu hỏi: {sha}',{sha:bundle.sha256}),'hash-note'));
      for(const question of bundle.questions){
        const card=el('div',undefined,'clarification-question'),input=el('textarea'),label=el('label',question.prompts[language()]);
        input.id='clarification-'+question.field;input.rows=3;input.maxLength=4000;input.value=answers.get(question.id)||'';
        label.htmlFor=input.id;
        input.setAttribute('aria-describedby','clarifications-answer-note');
        input.oninput=()=>{
          answers.set(question.id,input.value);get('clarifications-groq').checked=false;
          if(!posting&&!posted){notice=null;get('clarifications-message').textContent='';}controls();
        };
        card.append(label,input);area.append(card);
      }
    }
    controls();
  }
  document.addEventListener('issueintel:packchange',event=>{
    const source=event.detail.job_id,value=event.detail.intake,allowed=!!event.detail.clarifications_available;
    const usable=allowed&&valid(source,value),next=usable?[source,value.source_pack_sha256,value.query_sha256,value.clarifications.sha256].join(':'):null;
    get('clarifications-panel').hidden=!allowed;
    if(source===selected&&allowed===available&&next===binding&&!!bundle===!!usable)return;
    selected=source;available=allowed;binding=next;generation++;intake=usable?value:null;bundle=usable?value.clarifications:null;
    answers.clear();posting=false;posted=false;notice=allowed&&!usable?'Chưa đọc được bộ câu hỏi có nguồn. Mở lại phiên từ lịch sử để kiểm tra.':null;
    get('clarifications-groq').checked=false;get('clarifications-depth').value='quick';render();
  });
  document.addEventListener('issueintel:languagechange',()=>{get('clarifications-groq').checked=false;render();});
  get('clarifications-depth').onchange=()=>{get('clarifications-groq').checked=false;};
  get('clarifications-form').onsubmit=async event=>{
    event.preventDefault();if(!bundle||posting||posted||!binding)return;
    const supplied=answered();
    if(!supplied.length){notice='Trả lời ít nhất một câu, hoặc dùng phần bổ sung dữ kiện tự do.';render();return;}
    if(get('run-button').disabled){notice='Đang theo dõi tác vụ khác; kiểm tra lịch sử trước khi tiếp tục.';render();return;}
    if(supplied.some(question=>{const answer=answers.get(question.id);return answer.length>4000||answer.includes('\0');})){
      notice='Mỗi câu trả lời tối đa 4.000 ký tự và không chứa ký tự rỗng.';render();return;
    }
    const source=selected,current=generation,request={mode:'clarification',parent_job_id:source,parent_pack_sha256:intake.source_pack_sha256,
      questions_sha256:bundle.sha256,language:language(),answers:supplied.map(question=>({question_id:question.id,answer:answers.get(question.id)})),
      provider:get('clarifications-groq').checked?'groq':'none',depth:get('clarifications-depth').value};
    posting=true;notice='Đang tạo phiên điều tra với câu trả lời của bạn…';render();
    try{
      const job=await api('/api/investigations',request);
      if(current!==generation||source!==selected)return;
      if(!/^[a-f0-9]{24}$/.test(job?.id))throw new Error('Invalid investigation job');
      posted=true;get('clarifications-groq').checked=false;notice='Đã mở phiên mới; báo cáo nguồn được giữ nguyên.';
      document.dispatchEvent(new CustomEvent('issueintel:clarificationcreated',{detail:{job_id:job.id}}));
    }catch(_){
      if(current===generation){get('clarifications-groq').checked=false;notice='Chưa tạo được phiên. Câu trả lời được giữ lại; mở lại báo cáo nguồn từ lịch sử nếu hash đã đổi, rồi chủ động gửi lại.';}
    }finally{if(current===generation){posting=false;render();}}
  };
})();
