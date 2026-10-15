/* Current repository context stays separate from investigation/strict evidence. */
(() => {
  'use strict';
  const t = window.IssueIntelUI.t;
  const get = id => document.getElementById(id);
  const el = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  };
  let coverage=null, results=null, detail=null, jobs=[], active=null, notice=null, busy=false, readVersion=0;
  let connectionState=null, automaticRepository=null, webhookConfigured=null;
  const repository = () => get('index-repository').value.trim();
  const statusOnly = report => { const {tasks,...rest}=report; return rest; };
  function message(key) { notice=key; get('index-message').textContent=t(key); }
  function controls(locked) {
    busy=locked;
    for (const id of ['index-connect','index-sync','index-status','index-repository','index-revision','index-budget','index-refresh']) get(id).disabled=locked;
    get('index-cancel').hidden=!active;
  }
  function sourceLink(url, label) {
    const parsed = new URL(url);
    if(parsed.protocol!=='https:' || parsed.hostname!=='github.com' || parsed.username || parsed.password) throw Error('Invalid source URL');
    const link=el('a',label);link.href=url;link.target='_blank';link.rel='noreferrer noopener';return link;
  }
  function renderCoverage() {
    const area=get('index-coverage');area.replaceChildren();
    if(!coverage)return;
    const run=coverage.run;
    area.append(el('h3',coverage.repository));
    get('index-pause-auto').hidden=!connectionState || connectionState.paused || connectionState.repository!==coverage.repository;
    if(connectionState?.repository===coverage.repository){
      area.append(el('p',connectionState.paused?t('Đồng bộ tự động đang tạm dừng.'):t('Đồng bộ tự động đã bật; không cần mở trang để tiếp tục.'),'notice'));
      if(connectionState.error)area.append(el('p',t('Trạng thái kết nối: ')+connectionState.error+(connectionState.next_attempt?' · '+new Date(connectionState.next_attempt*1000).toLocaleString():''),'notice'));
      if(webhookConfigured===false)area.append(el('p',t('Webhook chưa cấu hình; cần thiết lập kết nối GitHub một lần để nhận sự kiện.'),'notice'));
    }
    if(!run){area.append(el('p',t('Repository chưa được đồng bộ.'),'notice'));return;}
    area.append(el('p',coverage.scan_complete?t('Đã đi hết các trang API trong scan.'):t('Scan chưa hoàn tất; bấm tiếp tục để lấy các trang còn lại.'),'notice'));
    const snapshot=coverage.current_snapshot || run.snapshot;
    const counts=el('dl',undefined,'repository-counts');
    for(const [label,value] of [[t('File code'),snapshot?.file_count ?? '—'],[t('Issue'),coverage.counts.issue || 0],['PR',coverage.counts.pull || 0],
      [t('Bình luận'),(coverage.counts.comments || 0)+(coverage.counts.review_comments || 0)],['PR commits',coverage.counts.commits || 0],['PR files',coverage.counts.files || 0]]){
      const cell=el('div');cell.append(el('dt',label),el('dd',String(value)));counts.append(cell);
    }
    area.append(counts,el('p',t('Trang đã lưu: {pages} · tác vụ còn lại: {pending}',{pages:run.pages,pending:coverage.pending_tasks}),'notice'),
      el('p','Revision '+(snapshot?.commit_sha || '—'),'hash-note'),el('p','Scan '+run.id+' · '+run.started_at,'hash-note'));
    if(coverage.pending_updates)area.append(el('p',t('Webhook đang chờ: {count}',{count:coverage.pending_updates}),'notice'));
    if(run.snapshot?.skipped_count)area.append(el('p',t('Snapshot đã bỏ qua {count} file theo giới hạn.',{count:run.snapshot.skipped_count}),'notice'));
    if(run.api_caps.length)area.append(el('p',t('Đã chạm giới hạn API ở một số PR; xem coverage trước khi kết luận đầy đủ.'),'notice'));
    const raw=el('details');raw.append(el('summary',t('Nguồn, phạm vi và giới hạn')),el('pre',JSON.stringify(coverage,null,2)));area.append(raw);
  }
  function renderResults() {
    const area=get('index-results');area.replaceChildren();
    if(!results)return;
    area.append(el('h3',results.repository+' · '+results.query));
    if(results.commit_sha)area.append(el('p','Revision '+results.commit_sha,'hash-note'));
    if(!results.results.length)area.append(el('p',t('Không có kết quả trong phạm vi đã lưu.'),'notice'));
    for(const hit of results.results){
      const card=el('article',undefined,'evidence-card');
      if(results.code){
        card.append(el('h3',hit.locator,'locator'),el('pre',hit.text),el('p','SHA256 '+hit.sha256,'hash-note'));
        const span=hit.locator.match(/^(.*):(\d+)-(\d+)$/);
        if(span){
          const path=span[1].split('/').map(encodeURIComponent).join('/');
          card.append(sourceLink('https://github.com/'+results.repository+'/blob/'+results.commit_sha+'/'+path+'#L'+span[2]+'-L'+span[3],t('Mở nguồn GitHub')));
        }
      }else{
        card.append(el('h3',hit.kind+' #'+hit.number+' · '+hit.title),el('pre',hit.snippet.text),el('p','SHA256 '+hit.record_sha256,'hash-note'),
          el('p',t('Đọc lúc: ')+hit.fetched_at,'hash-note'));
        if(hit.last_seen_run!==results.index_status.run?.id)card.append(el('p',t('Nguồn từ scan trước; chưa thấy lại trong scan hiện tại.'),'notice'));
        const open=el('button',t('Xem nội dung và nguồn đã lưu'));open.type='button';
        const selected=results.repository;
        open.onclick=async()=>{
          const version=++readVersion;open.disabled=true;
          try {const value=await api('/api/repositories/item',{repository:selected,key:hit.key});if(version===readVersion){detail=value;renderDetail();}}
          catch(error){message('Không đọc được index; kiểm tra đầu vào, nguồn và hash.');}
          finally{open.disabled=false;}
        };
        card.append(sourceLink(hit.url,t('Mở nguồn GitHub')),document.createTextNode(' '),open);
      }
      area.append(card);
    }
  }
  function renderDetail() {
    const area=get('index-detail');area.replaceChildren();if(!detail)return;
    const record=detail.record, box=el('article',undefined,'evidence-card');
    box.append(el('h3',record.repository+' · '+record.key),el('p',record.title),el('pre',record.body),
      el('p','SHA256 '+detail.record_sha256,'hash-note'),sourceLink(record.url,t('Mở nguồn GitHub')));
    const source=el('details');source.append(el('summary',t('Nguồn, phạm vi và giới hạn')),el('pre',JSON.stringify(detail,null,2)));box.append(source);
    // Fill the ordinary GitHub form only; no automatic model/network run.
    if(record.kind==='issue'){
      const use=el('button',t('Điền issue này vào form điều tra'));use.type='button';
      use.onclick=()=>{
        get('mode').value='github';get('mode').dispatchEvent(new Event('change'));
        get('github-url').value=record.url;
        get('github-revision').value=results?.index_status.current_snapshot?.commit_sha || results?.index_status.run?.snapshot?.commit_sha || 'HEAD';
        get('use-groq').checked=false;get('github-url').focus();
      };box.append(document.createTextNode(' '),use);
    }
    area.append(box);
  }
  function renderJobs() {
    const area=get('index-jobs');area.replaceChildren();
    if(!jobs.length)area.append(el('p',t('Chưa có lượt đồng bộ.'),'notice'));
    for(const job of jobs){
      const row=el('button',undefined,'history-row');row.type='button';
      row.append(el('strong',job.repository || job.sync_report?.repository || 'Repository'),el('span',t(job.status)),el('small',job.id.slice(0,8)));
      row.disabled=!!active;
      row.onclick=async()=>{
        if(['queued','running'].includes(job.status)){active=job.id;controls(true);await poll();}
        else if(job.sync_report){coverage=job.sync_report;renderCoverage();message('Đang xem checkpoint của lượt đồng bộ đã chọn.');}
        else message('Lượt đồng bộ chưa có checkpoint; xem trạng thái local để tiếp tục.');
      };area.append(row);
    }
  }
  async function history() {try{jobs=await api('/api/repositories/jobs');renderJobs();}catch(error){message('Không đọc được index; kiểm tra đầu vào, nguồn và hash.');}}
  async function poll() {
    try{
      for(;;){
        const job=await api('/api/jobs/'+active);
        if(job.sync_report){coverage=job.sync_report;renderCoverage();}
        if(['completed','failed','cancelled','interrupted'].includes(job.status)){
          message(job.status==='completed'?(coverage?.scan_complete?'Lượt đồng bộ đã hoàn tất.':'Đã lưu checkpoint; scan còn thiếu trang. Bấm tiếp tục khi cần.'):
            'Đồng bộ dừng; các trang đã lưu được giữ lại. Kiểm tra trạng thái và tiếp tục chủ động.');
          break;
        }
        message('Đang đọc GitHub và lưu checkpoint; không gọi LLM.');
        await new Promise(resolve=>setTimeout(resolve,700));
      }
    }catch(error){message('Mất kết nối theo dõi. Xem lịch sử đồng bộ trước khi tạo lượt mới.');}
    finally{active=null;controls(false);await history();}
  }
  get('repository-form').onsubmit=async event=>{
    event.preventDefault();if(busy)return;
    controls(true);results=null;detail=null;++readVersion;renderResults();renderDetail();
    try{
      const job=await api('/api/repositories/sync',{repository:repository(),revision:get('index-revision').value,
        max_requests:Number(get('index-budget').value),refresh:get('index-refresh').checked});
      get('index-refresh').checked=false;
      active=job.id;controls(true);await poll();
    }catch(error){message('Không bắt đầu được: kiểm tra repo, revision, giới hạn hoặc hàng đợi.');controls(false);}
  };
  get('index-cancel').onclick=async()=>{
    if(!active)return;get('index-cancel').disabled=true;
    try{if(automaticRepository)await api('/api/repositories/pause',{repository:automaticRepository,paused:true});else await api('/api/jobs/'+active+'/cancel',{});message('Đã yêu cầu dừng sau trang hiện tại; checkpoint được giữ lại.');}
    catch(error){message('Không đọc được index; kiểm tra đầu vào, nguồn và hash.');}
    finally{get('index-cancel').disabled=false;}
  };
  get('index-status').onclick=async()=>{
    const version=++readVersion;controls(true);
    try{const report=await api('/api/repositories/connection',{repository:repository()});if(version===readVersion){connectionState=report.connection;webhookConfigured=report.webhook_configured;coverage=statusOnly(report.index);renderCoverage();message('Đã đọc trạng thái local; không gọi GitHub hoặc LLM.');}}
    catch(error){message('Không đọc được index; kiểm tra đầu vào, nguồn và hash.');}
    finally{controls(false);}
  };
  get('index-connect').onclick=async()=>{
    if(busy || !repository())return;controls(true);++readVersion;results=null;detail=null;renderResults();renderDetail();
    const selected=repository();
    try{
      await api('/api/repositories/connect',{repository:selected,revision:get('index-revision').value});
      automaticRepository=selected;active='automatic';controls(true);
      for(;;){
        const report=await api('/api/repositories/connection',{repository:selected});
        connectionState=report.connection;webhookConfigured=report.webhook_configured;coverage=statusOnly(report.index);renderCoverage();
        if(!connectionState || connectionState.paused || connectionState.next_attempt>Date.now()/1000){message('Đã lưu kết nối và checkpoint; hệ thống hiển thị thời gian chờ hoặc trạng thái tạm dừng.');break;}
        if(connectionState.phase==='ready'){message('Repository đã sẵn sàng; cập nhật tiếp theo sẽ qua webhook.');break;}
        message('Đang tự đồng bộ repository; có thể đóng trang, server vẫn tiếp tục.');
        await new Promise(resolve=>setTimeout(resolve,1000));
      }
    }catch(error){message('Không bắt đầu được: kiểm tra repo, revision, giới hạn hoặc hàng đợi.');}
    finally{automaticRepository=null;active=null;controls(false);await history();}
  };
  get('index-pause-auto').onclick=async()=>{
    if(!connectionState)return;
    try{const value=await api('/api/repositories/pause',{repository:connectionState.repository,paused:true});connectionState=value.connection;coverage=statusOnly(value.index);renderCoverage();}
    catch(error){message('Không đọc được index; kiểm tra đầu vào, nguồn và hash.');}
  };
  get('repository-search-form').onsubmit=async event=>{
    event.preventDefault();const version=++readVersion;get('index-search').disabled=true;
    detail=null;results=null;renderDetail();renderResults();
    const query=get('index-query').value,code=get('index-kind').value==='code';
    try{const report=await api('/api/repositories/search',{repository:repository(),query,code,limit:10});if(version===readVersion){results={...report,code,query};coverage=statusOnly(report.index_status);renderCoverage();renderResults();}}
    catch(error){message('Không đọc được index; kiểm tra đầu vào, nguồn và hash.');}
    finally{get('index-search').disabled=false;}
  };
  document.addEventListener('issueintel:languagechange',()=>{renderCoverage();renderResults();renderDetail();renderJobs();if(notice)message(notice);});
  history(); // Local job metadata only. Never starts or resumes a GitHub scan.
})();
