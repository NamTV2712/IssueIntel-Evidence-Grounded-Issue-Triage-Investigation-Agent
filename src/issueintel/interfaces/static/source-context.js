/* Deliberate, hash-bound source selection from current local repository memory. */
(() => {
  'use strict';
  const get=id=>document.getElementById(id),t=window.IssueIntelUI.t;
  const el=(tag,text,cls)=>{const item=document.createElement(tag);if(text!==undefined)item.textContent=text;if(cls)item.className=cls;return item;};
  let selected=null,available=false,result=null,chosen=new Map(),generation=0,searching=false,posting=false,notice=null;
  const digest=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
  function sourceLink(value){
    try{const url=new URL(value);return url.protocol==='https:'&&url.hostname==='github.com'&&!url.username&&!url.password?url.href:null;}
    catch(_){return null;}
  }
  function updateControls(){
    const busy=searching||posting,count=chosen.size;
    get('source-context-search').disabled=busy||!available;
    get('source-context-query').disabled=busy;
    get('source-context-submit').disabled=busy||count===0||!result;
    get('source-context-groq').disabled=busy;
    get('source-context-depth').disabled=busy;
    get('source-context-count').textContent=t('Đã chọn {count}/6 nguồn',{count});
    for(const input of get('source-context-results').querySelectorAll('input[type="checkbox"]'))input.disabled=busy||(!input.checked&&count>=6);
  }
  function render(){
    get('source-context-parent').textContent=selected?t('Phiên nguồn: ')+selected:'';
    get('source-context-message').textContent=notice?t(notice):'';
    const area=get('source-context-results');area.replaceChildren();
    get('source-context-selection').hidden=!result?.results.length;
    if(result){
      const identity=el('div',undefined,'source-context-identity');
      identity.append(el('strong',result.repository),el('p',result.issue_url,'hash-note'));
      if(result.commit_sha)identity.append(el('p','Commit '+result.commit_sha,'hash-note'));
      identity.append(el('p',t('SHA256 báo cáo nguồn: {sha}',{sha:result.source_pack_sha256}),'hash-note'));
      area.append(identity);
      if(!result.scan_complete)area.append(el('p',t('Lịch sử repository chưa quét xong; kết quả chỉ bao phủ dữ liệu đã lưu.'),'notice'));
      if(result.pending_updates)area.append(el('p',t('Có cập nhật repository đang chờ; nguồn có thể đổi trước khi bắt đầu điều tra.'),'notice'));
      if(!result.results.length)area.append(el('p',t('Chưa tìm thấy nguồn phù hợp trong bộ nhớ local. Thử từ khóa khác hoặc đồng bộ repository.'),'notice'));
      for(const row of result.results){
        const card=el('article',undefined,'source-context-card');
        const label=el('label',undefined,'checkbox source-context-choice'),input=el('input');input.type='checkbox';
        input.checked=chosen.has(row.key);
        input.onchange=()=>{
          if(searching||posting){input.checked=chosen.has(row.key);return;}
          if(input.checked&&chosen.size<6)chosen.set(row.key,{key:row.key,record_sha256:row.record_sha256,receipt_sha256:row.receipt_sha256});else{chosen.delete(row.key);input.checked=false;}
          get('source-context-groq').checked=false;
          updateControls();
        };
        label.append(input,el('span',row.title||row.key));
        card.append(label,el('p',row.kind+' · '+row.key,'hash-note'),el('pre',row.excerpt));
        const url=sourceLink(row.url);
        if(url){const link=el('a',t('Mở nguồn GitHub'));link.href=url;link.target='_blank';link.rel='noreferrer noopener';card.append(link);}
        card.append(el('p','Record SHA256 '+row.record_sha256,'hash-note'),el('p','Receipt SHA256 '+row.receipt_sha256,'hash-note'));
        area.append(card);
      }
      area.append(el('p',t('SHA256 kết quả tìm kiếm: {sha}',{sha:result.sha256}),'hash-note'));
    }
    updateControls();
  }
  document.addEventListener('issueintel:packchange',event=>{
    const next=event.detail.job_id,allowed=!!event.detail.context_available;
    get('source-context-panel').hidden=!allowed;
    if(next===selected&&allowed===available)return;
    selected=next;available=allowed;generation++;result=null;chosen.clear();searching=false;posting=false;notice=null;
    get('source-context-query').value='';get('source-context-groq').checked=false;get('source-context-depth').value='quick';render();
  });
  document.addEventListener('issueintel:languagechange',()=>{get('source-context-groq').checked=false;render();});
  get('source-context-search-form').onsubmit=async event=>{
    event.preventDefault();if(!available||searching||posting||!selected)return;
    const query=get('source-context-query').value.trim();
    if(!query){notice='Nhập từ khóa để tìm nguồn liên quan.';render();return;}
    const source=selected,current=++generation,request={job_id:source,query};
    searching=true;result=null;chosen.clear();get('source-context-groq').checked=false;notice='Đang tìm nguồn trong bộ nhớ local; không gọi LLM.';render();
    try{
      const found=await api('/api/context-search',request);
      if(current!==generation||source!==selected)return;
      const rows=found.results,keys=new Set();
      if(found.job_id!==source||found.query!==query||!digest(found.source_pack_sha256)||!digest(found.sha256)||!Array.isArray(rows))throw new Error('Source binding unavailable');
      for(const row of rows){
        if(!row||typeof row.key!=='string'||keys.has(row.key)||!digest(row.record_sha256)||!digest(row.receipt_sha256)||typeof row.excerpt!=='string')throw new Error('Source binding unavailable');
        keys.add(row.key);
      }
      result=found;notice='Xem trích đoạn và hash rồi chọn tối đa 6 nguồn cho phiên điều tra mới.';
    }catch(_){if(current===generation)notice='Chưa tìm được nguồn cho phiên này. Kiểm tra repository đã đồng bộ và thử lại.';}
    finally{if(current===generation){searching=false;render();}}
  };
  get('source-context-form').onsubmit=async event=>{
    event.preventDefault();if(!available||searching||posting||!result||!chosen.size||chosen.size>6||result.job_id!==selected)return;
    if(get('run-button').disabled){notice='Đang theo dõi tác vụ khác; kiểm tra lịch sử trước khi tiếp tục.';render();return;}
    const source=selected,current=generation,request={mode:'context',parent_job_id:source,parent_pack_sha256:result.source_pack_sha256,
      selected_sources:[...chosen.values()].map(value=>({...value})),
      provider:get('source-context-groq').checked?'groq':'none',depth:get('source-context-depth').value};
    posting=true;notice='Đang mở phiên điều tra với các nguồn đã chọn…';render();
    try{
      const job=await api('/api/investigations',request);
      if(current===generation&&source===selected){
        if(!/^[a-f0-9]{24}$/.test(job.id))throw new Error('Invalid job');
        chosen.clear();get('source-context-groq').checked=false;notice='Đã mở phiên mới; báo cáo nguồn được giữ nguyên.';
        document.dispatchEvent(new CustomEvent('issueintel:contextcreated',{detail:{job_id:job.id}}));
      }
    }catch(_){if(current===generation){result=null;chosen.clear();get('source-context-groq').checked=false;notice='Chưa tạo được phiên. Nguồn có thể đã thay đổi; tìm lại và chọn lại trước khi gửi.';}}
    finally{if(current===generation){posting=false;render();}}
  };
})();
