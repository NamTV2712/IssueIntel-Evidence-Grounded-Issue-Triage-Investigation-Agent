/* Local inspection is deliberate and independent of the selected investigation. */
(() => {
  const panel=document.getElementById('system-readiness');
  if(!panel)return;
  const t=window.IssueIntelUI.t,$=id=>document.getElementById(id);
  const natural=v=>Number.isSafeInteger(v)&&v>=0;
  const text=(v,max=500)=>typeof v==='string'&&v.length<=max;
  const code=v=>text(v,100)&&/^[a-z][a-z0-9_]*$/.test(v);
  const codes=v=>Array.isArray(v)&&v.length<=100&&v.every(code);
  const timestamp=v=>text(v,50)&&Number.isFinite(Date.parse(v));
  const nullableCode=v=>v===null||code(v);
  const session=()=>document.querySelector('meta[name="issueintel-session"]').content;
  const cacheIds=['repositories','repository_index','repository_memory','saved_reports'];
  const capabilityIds=['offline','groq','jev','repository_history','github_app','saved_reports','batch','questions','check_plan','claim_review','stage_recovery','handoff','versions','releases','memory','followup','planned'];
  const categoryIds=['git','source','environment','tool_cache','data','artifacts','other'];
  const statusNames={available:'Có thể dùng cục bộ',blocked:'Bị chặn',conditional:'Có điều kiện',unknown:'Chưa xác minh',present:'Đã có',missing:'Chưa có',unavailable:'Chưa đọc được',unsafe:'Đường dẫn không an toàn',not_initialized:'Chưa khởi tạo'};
  const labels={offline:'Điều tra offline',groq:'Groq',jev:'Jev / Typesafe',repository_history:'Lịch sử repository',github_app:'GitHub App / HTTPS',saved_reports:'Báo cáo đã lưu',batch:'Điều tra theo lô',questions:'Câu hỏi phân biệt',check_plan:'Kế hoạch kiểm tra',claim_review:'Verifier nhận định',stage_recovery:'Phục hồi stage',handoff:'Gói bàn giao',versions:'Đối chiếu phiên bản',releases:'Quan sát bản phát hành',memory:'Bộ nhớ điều tra',followup:'Điều tra bổ sung',planned:'Điều tra nhiều bước',repositories:'Cache Git repository',repository_index:'Chỉ mục repository',repository_memory:'Bộ nhớ repository',git:'.git',source:'Mã nguồn và cấu hình',environment:'Môi trường Python',tool_cache:'Cache công cụ',data:'Dữ liệu cục bộ',artifacts:'Hồ sơ và receipt',other:'Khác'};
  const reasons={
    runtime_ready:'Runtime đủ điều kiện cục bộ.',runtime_not_ready:'Runtime chưa đủ điều kiện cục bộ.',
    python_too_old:'Phiên bản Python chưa đáp ứng yêu cầu.',source_incomplete:'Source checkout chưa đầy đủ.',
    git_missing:'Chưa có Git.',git_unavailable:'Chưa kiểm được Git.',fts5_unavailable:'SQLite FTS5 chưa khả dụng.',
    ready:'Các điều kiện cục bộ đã đủ; quyền gọi thật vẫn cần admission.',
    invalid_configuration:'Cấu hình provider chưa hợp lệ.',unsafe_configuration_path:'Đường dẫn cấu hình chưa an toàn.',
    invalid_or_stale_prices:'Bảng giá thiếu, không hợp lệ hoặc quá hạn.',invalid_per_call_bounds:'Cận từng lời gọi chưa hợp lệ.',
    missing_api_key:'Chưa cấu hình API key.',missing_spending_limits:'Chưa có giới hạn chi tiêu hoặc quyết định cho phép phù hợp.',
    next_call_budget_unchecked:'Ngân sách cho lời gọi tiếp theo chưa được kiểm.',groq_not_ready:'Groq chưa đủ điều kiện cấu hình.',
    local_index_metadata_only:'Chỉ đọc metadata của chỉ mục local; chưa xác minh nội dung.',repository_index_missing:'Chưa có chỉ mục repository.',
    saved_reports_not_verified:'Có nơi lưu báo cáo; từng hồ sơ chưa được kiểm.',no_saved_reports:'Chưa có nơi lưu báo cáo.',
    unsafe_local_path:'Đường dẫn cục bộ chưa an toàn.',local_state_unavailable:'Trạng thái cục bộ chưa đọc được.',
    app_https_permissions_not_verified:'Chưa nghiệm thu App, HTTPS và quyền truy cập thật.',app_not_configured:'Chưa cấu hình GitHub App.',
    offline_batch_available:'Có thể chạy lô offline; từng nguồn vẫn cần kiểm trước khi chạy.',
    eligible_source_required:'Cần hồ sơ product đủ điều kiện; màn hình này chưa kiểm phiên đang chọn.',
    eligible_recovery_source_required:'Cần checkpoint đủ proof để phục hồi; chưa kiểm từng phiên.',
    github_public_lookup_required:'Cần nguồn GitHub public và kiểm quyền khi bạn yêu cầu tra cứu.',
    product_source_required:'Cần nguồn product đã kiểm; chưa kiểm nguồn đang chọn.',
    index_not_initialized:'Chỉ mục chưa khởi tạo.',local_index_unavailable:'Chưa đọc được chỉ mục cục bộ.',sync_reported_error:'Đồng bộ có ghi nhận lỗi; kiểm lịch sử đồng bộ.',
    data_private:'Dữ liệu riêng tư cần giữ.',artifacts_provenance:'Hồ sơ và receipt provenance cần giữ.',git_history:'Lịch sử Git có thể là nguồn duy nhất.',
    environment_in_use:'Môi trường có thể đang chạy sản phẩm.',configuration:'Cấu hình và khóa cần giữ.',source_files:'Mã nguồn cần giữ.',protected_by_default:'Được bảo vệ mặc định.',
    cache_unknown_content:'Cache có nội dung ngoài danh sách cho phép.',cache_unsafe_entry:'Cache có entry chưa an toàn.',cache_incomplete:'Cache chưa được duyệt đầy đủ.',
    unsafe_root:'Gốc quét chưa an toàn.',unsafe_link:'Bỏ qua link hoặc reparse point.',filesystem_boundary:'Bỏ qua ranh giới filesystem.',metadata_error:'Có metadata chưa đọc được.',unsupported_entry:'Có loại entry chưa được hỗ trợ.',
    key_missing:'Chưa cấu hình API key.',missing_key:'Chưa cấu hình API key.',
    config_missing:'Chưa có cấu hình provider.',configuration_missing:'Chưa có cấu hình provider.',
    config_invalid:'Cấu hình provider chưa hợp lệ.',configuration_invalid:'Cấu hình provider chưa hợp lệ.',
    disabled:'Provider đang tắt trong cấu hình.',provider_disabled:'Provider đang tắt trong cấu hình.',
    price_missing:'Thiếu bảng giá có ngày.',prices_missing:'Thiếu bảng giá có ngày.',
    budget_not_checked:'Ngân sách cho lời gọi tiếp theo chưa được kiểm.',
    locally_ready:'Các điều kiện cục bộ đã đủ; quyền gọi thật vẫn cần admission.',
    local_configuration_ready:'Các điều kiện cục bộ đã đủ; quyền gọi thật vẫn cần admission.',
    python_bytecode:'Bytecode Python có thể tạo lại.',tool_cache:'Cache công cụ có thể tạo lại.',
    entry_limit:'Đã chạm giới hạn số entry.',depth_limit:'Đã chạm giới hạn độ sâu.',time_limit:'Đã chạm giới hạn thời gian.',
    max_entries:'Đã chạm giới hạn số entry.',max_depth:'Đã chạm giới hạn độ sâu.',max_seconds:'Đã chạm giới hạn thời gian.'
  };
  let epoch=0,readTurn=0,storageTurn=0,readValue=null,storageValue=null;
  let readLoading=false,storageLoading=false,readError=false,storageError=false;
  const node=(tag,value,cls)=>{const n=document.createElement(tag);if(value!==undefined)n.textContent=value;if(cls)n.className=cls;return n;};
  const sameIds=(values,ids)=>Array.isArray(values)&&values.length===ids.length&&new Set(values.map(v=>v?.id)).size===ids.length&&values.every(v=>ids.includes(v?.id));
  const relative=v=>text(v,2000)&&v.length>0&&!v.startsWith('/')&&!v.includes('\\')&&!v.includes(':')&&!v.split('/').some(part=>!part||part==='.'||part==='..');
  function validReadiness(v){
    try{return v?.schema==='product_readiness_v1'&&timestamp(v.checked_at)&&v.model_calls===0&&v.evaluation_performed===false&&v.live_services_verified===false
      &&typeof v.runtime.ready==='boolean'&&text(v.runtime.python,100)&&['git_available','fts5_available','source_complete'].every(k=>typeof v.runtime[k]==='boolean')&&codes(v.runtime.problems)
      &&(!v.runtime.ready||(v.runtime.git_available&&v.runtime.fts5_available&&v.runtime.source_complete&&!v.runtime.problems.length))
      &&['groq','typesafe'].every(id=>{const p=v.providers[id];return p&&typeof p.ready==='boolean'&&typeof p.key_configured==='boolean'&&(!p.ready||p.key_configured)
        &&text(p.model,200)&&code(p.reason)&&(p.price_date===null||text(p.price_date,50))&&(!p.ready||(p.model&&p.price_date!==null&&(id!=='typesafe'||p.enabled)))&&p.budget_checked===false&&p.live_verified===false&&(id!=='typesafe'||typeof p.enabled==='boolean');})
      &&['not_initialized','available','unavailable'].includes(v.sync.status)&&typeof v.sync.scheduler_running==='boolean'&&typeof v.sync.truncated==='boolean'
      &&Array.isArray(v.sync.connections)&&v.sync.connections.length<=1000&&v.sync.connections.every(c=>text(c.repository,600)&&code(c.phase)&&typeof c.paused==='boolean'
        &&['pending_tasks','pending_updates'].every(k=>c[k]===null||natural(c[k]))&&(c.last_updated_at===null||(typeof c.last_updated_at==='number'&&Number.isFinite(c.last_updated_at)&&c.last_updated_at>=0&&c.last_updated_at<=8640000000000))&&nullableCode(c.reason))
      &&sameIds(v.cache,cacheIds)&&v.cache.every(c=>['present','missing','unavailable','unsafe'].includes(c.status))
      &&sameIds(v.capabilities,capabilityIds)&&v.capabilities.every(c=>['available','blocked','conditional','unknown'].includes(c.status)&&code(c.reason));
    }catch(_){return false;}
  }
  function validStorage(v){
    try{return v?.schema==='product_storage_preview_v1'&&timestamp(v.scanned_at)&&v.deletion_supported===false&&v.model_calls===0
      &&['measured_bytes','files','directories','skipped_links','errors'].every(k=>natural(v[k]))&&typeof v.complete==='boolean'&&codes(v.limit_reasons)
      &&(!v.complete||(!v.limit_reasons.length&&!v.errors&&!v.skipped_links))
      &&sameIds(v.categories,categoryIds)&&v.categories.every(c=>['measured_bytes','files','directories'].every(k=>natural(c[k])))
      &&['measured_bytes','files','directories'].every(k=>v.categories.reduce((sum,c)=>sum+c[k],0)===v[k])
      &&Array.isArray(v.candidates)&&v.candidates.length<=25000&&v.candidates.every(c=>relative(c.path)&&natural(c.measured_bytes)&&natural(c.files)&&['python_bytecode','tool_cache'].includes(c.reason)&&typeof c.complete==='boolean'
        &&(c.reason==='tool_cache'?/^\.(pytest|ruff|mypy)_cache$/.test(c.path):/^(?:(?:src|tests)\/(?:[^/]+\/)*)?__pycache__$/.test(c.path)))
      &&Array.isArray(v.protected)&&v.protected.length<=25000&&v.protected.every(p=>(relative(p.path)||(p.path==='.'&&['unsafe_root','unsafe_link','filesystem_boundary'].includes(p.reason)))&&code(p.reason))
      &&(v.volume===null||(natural(v.volume.total_bytes)&&natural(v.volume.free_bytes)&&v.volume.free_bytes<=v.volume.total_bytes))
      &&natural(v.limits.max_entries)&&v.limits.max_entries>0&&v.limits.max_entries<=25000&&natural(v.limits.max_depth)&&v.limits.max_depth<=40&&typeof v.limits.max_seconds==='number'&&Number.isFinite(v.limits.max_seconds)&&v.limits.max_seconds>0&&v.limits.max_seconds<=3
      &&natural(v.scan.entries)&&natural(v.scan.elapsed_ms);
    }catch(_){return false;}
  }
  const number=value=>value.toLocaleString(window.IssueIntelUI.locale?.()||undefined);
  const bytes=value=>{if(value<1024)return number(value)+' B';const unit=Math.min(4,Math.floor(Math.log(value)/Math.log(1024)));
    return (value/1024**unit).toLocaleString(window.IssueIntelUI.locale?.()||undefined,{maximumFractionDigits:2})+' '+['B','KiB','MiB','GiB','TiB'][unit]+' ('+number(value)+' B)';};
  function field(list,label,value){const row=node('div');row.append(node('dt',t(label)),node('dd',value));list.append(row);}
  function badge(status){return node('span',t(statusNames[status]||'Chưa xác minh'),'readiness-state readiness-'+status);}
  function reasonDetail(value){const n=node('span',reasons[value]?t(reasons[value]):t('Xem mã trạng thái cục bộ.'));n.append(node('code',value,'readiness-code'));return n;}
  function detailsCodes(label,values){const d=node('details');d.append(node('summary',t(label)));const ul=node('ul');for(const value of values)ul.append(node('li',undefined,'readiness-reason'));for(let i=0;i<values.length;i++)ul.children[i].append(reasonDetail(values[i]));d.append(ul);return d;}
  function paintReadiness(){
    $('readiness-refresh').disabled=readLoading;
    $('readiness-content').setAttribute('aria-busy',String(readLoading));
    $('readiness-message').textContent=readLoading?t('Đang kiểm tra điều kiện cục bộ…'):readError?t('Chưa đọc được trạng thái hệ thống. Làm mới để kiểm lại; không có API nào được gọi.') :'';
    $('readiness-message').className=readError?'error':'notice';
    const content=$('readiness-content');content.replaceChildren();if(!readValue)return;
    const v=readValue;content.append(node('p',t('Kiểm tra lúc: {time}',{time:v.checked_at}),'hash-note'));
    const runtime=node('section',undefined,'readiness-block');runtime.append(node('h3',t('Runtime cục bộ')),badge(v.runtime.ready?'available':'blocked'));
    const runtimeList=node('dl',undefined,'readiness-fields');field(runtimeList,'Python',v.runtime.python);field(runtimeList,'Git',t(v.runtime.git_available?'Đã có':'Chưa có'));field(runtimeList,'SQLite FTS5',t(v.runtime.fts5_available?'Đã có':'Chưa có'));field(runtimeList,'Source đầy đủ',t(v.runtime.source_complete?'Đã có':'Chưa có'));runtime.append(runtimeList);
    if(v.runtime.problems.length)runtime.append(detailsCodes('Lý do runtime chưa đủ',v.runtime.problems));content.append(runtime);
    const caps=node('section',undefined,'readiness-block');caps.append(node('h3',t('Khả năng theo cấu hình hiện có')),node('p',t('Đây là trạng thái chung; chưa kiểm điều kiện của issue, pack hoặc checkpoint đang chọn.'),'notice'));const capList=node('ul',undefined,'readiness-capabilities');
    for(const capability of v.capabilities){const row=node('li');row.append(node('strong',t(labels[capability.id])),badge(capability.status),reasonDetail(capability.reason));capList.append(row);}caps.append(capList);content.append(caps);
    const providers=node('section',undefined,'readiness-block');providers.append(node('h3',t('Provider và điều kiện gọi API')),node('p',t('Có key không đồng nghĩa gọi được. Chưa kiểm ngân sách cho lời gọi tiếp theo, hiệu lực key hoặc kết nối dịch vụ.'),'notice'));
    const grid=node('div',undefined,'readiness-provider-grid');for(const id of ['groq','typesafe']){const p=v.providers[id],card=node('section',undefined,'readiness-provider');card.append(node('h4',id==='groq'?'Groq':'Jev / Typesafe'));
      const dl=node('dl',undefined,'readiness-fields');field(dl,'API key',t(p.key_configured?'Đã cấu hình; không hiển thị khóa':'Chưa cấu hình'));if(id==='typesafe')field(dl,'Bật trong cấu hình',t(p.enabled?'Có':'Không'));
      field(dl,'Model',p.model||t('Chưa biết'));field(dl,'Bảng giá có ngày',p.price_date||t('Chưa có'));field(dl,'Điều kiện cục bộ',t(p.ready?'Đủ điều kiện cấu hình':'Chưa đủ điều kiện cấu hình'));card.append(dl,reasonDetail(p.reason));grid.append(card);}providers.append(grid);content.append(providers);
    const sync=node('section',undefined,'readiness-block');sync.append(node('h3',t('Đồng bộ repository')),badge(v.sync.status),node('p',t(v.sync.scheduler_running?'Scheduler local đang chạy.':'Scheduler local chưa chạy.'),'notice'),node('p',t('GitHub App và webhook HTTPS chưa được xác minh bởi màn hình này. Đây chưa phải nghiệm thu tích hợp thật.'),'notice'));
    if(!v.sync.connections.length)sync.append(node('p',t('Chưa có kết nối repository đọc được.'),'notice'));
    for(const c of v.sync.connections){const card=node('article',undefined,'readiness-connection');card.append(node('h4',c.repository));const dl=node('dl',undefined,'readiness-fields');field(dl,'Phase local',c.phase);field(dl,'Tạm dừng',t(c.paused?'Có':'Không'));field(dl,'Tác vụ chờ',c.pending_tasks===null?t('Chưa biết'):number(c.pending_tasks));field(dl,'Cập nhật chờ',c.pending_updates===null?t('Chưa biết'):number(c.pending_updates));field(dl,'Cập nhật lần cuối',c.last_updated_at===null?t('Chưa biết'):new Date(c.last_updated_at*1000).toISOString());card.append(dl);if(c.reason)card.append(reasonDetail(c.reason));sync.append(card);}
    if(v.sync.truncated)sync.append(node('p',t('Danh sách kết nối bị giới hạn; các kết nối ngoài danh sách chưa được kiểm.'),'notice'));content.append(sync);
    const cache=node('section',undefined,'readiness-block');cache.append(node('h3',t('Cache và báo cáo cục bộ')));const list=node('ul',undefined,'readiness-cache');for(const item of v.cache){const row=node('li');row.append(node('span',t(labels[item.id])),badge(item.status));list.append(row);}cache.append(list,node('p',t('Sự hiện diện của cache chưa chứng minh nội dung hợp lệ hoặc proof lịch sử đầy đủ.'),'notice'));content.append(cache);
  }
  function paintStorage(){
    $('storage-preview').disabled=storageLoading;$('storage-content').setAttribute('aria-busy',String(storageLoading));
    $('storage-message').textContent=storageLoading?t('Đang đo metadata dung lượng trong giới hạn…'):storageError?t('Chưa đo được dung lượng hợp lệ. Bản đo trước đã bỏ khỏi màn hình; có thể thử lại.'):'';
    $('storage-message').className=storageError?'error':'notice';const content=$('storage-content');content.replaceChildren();if(!storageValue)return;
    const v=storageValue;content.append(node('p',t('Đo lúc: {time}',{time:v.scanned_at}),'hash-note'),node('p',t(v.complete?'Đã duyệt hết phạm vi cục bộ an toàn.':'Đo một phần; chưa phải tổng dung lượng project.'),v.complete?'notice':'readiness-partial'));
    const counts=node('dl',undefined,'readiness-storage-counts');field(counts,v.complete?'Dung lượng logic đã đo':'Dung lượng logic đã đo một phần',bytes(v.measured_bytes));field(counts,'File đã đo',number(v.files));field(counts,'Thư mục đã thấy',number(v.directories));field(counts,'Link / reparse đã bỏ qua',number(v.skipped_links));field(counts,'Lỗi đọc metadata',number(v.errors));content.append(counts);
    content.append(node('p',t('Đây là độ dài logic của các file đã đo, không phải dung lượng có thể giải phóng. Không đọc nội dung và không xóa file.'),'notice'));
    if(v.limit_reasons.length)content.append(detailsCodes('Giới hạn của bản đo',v.limit_reasons));
    const categories=node('ul',undefined,'readiness-storage-categories');for(const category of v.categories){const row=node('li');row.append(node('span',t(labels[category.id])),node('strong',bytes(category.measured_bytes)),node('small',t('{files} file · {directories} thư mục',{files:number(category.files),directories:number(category.directories)})));categories.append(row);}content.append(categories);
    const protectedList=node('details',undefined,'readiness-protected');protectedList.append(node('summary',t('Nguồn được bảo vệ: {count}',{count:v.protected.length})),node('p',t('Dữ liệu, artifact, receipt, cache Git, môi trường, cấu hình và source có thể là nguồn duy nhất. Màn hình này không đề xuất xóa chúng.'),'notice'));
    const protectedRows=node('ul');for(const p of v.protected.slice(0,200)){const row=node('li');row.append(node('code',p.path),reasonDetail(p.reason));protectedRows.append(row);}protectedList.append(protectedRows);if(v.protected.length>200)protectedList.append(node('p',t('Hiển thị {shown}/{total} đường dẫn để giao diện gọn; các đường dẫn còn lại vẫn được bảo vệ.',{shown:200,total:number(v.protected.length)}),'notice'));content.append(protectedList);
    const candidates=node('section',undefined,'readiness-block');candidates.append(node('h3',t('Cache có thể tạo lại — chỉ xem trước')),node('p',t('Danh sách cho phép rất hẹp. Không có nút xóa; mục không đủ điều kiện vẫn được giữ nguyên.'),'notice'));
    if(!v.candidates.length)candidates.append(node('p',t('Không có cache đủ điều kiện trong phạm vi đã đo.'),'notice'));
    for(const c of v.candidates.slice(0,200)){const row=node('article',undefined,'readiness-candidate');row.append(node('code',c.path),node('p',bytes(c.measured_bytes)+' · '+t('{files} file',{files:number(c.files)})),reasonDetail(c.reason));if(!c.complete)row.append(node('p',t('Ứng viên mới đo một phần; không suy ra có thể dọn an toàn.'),'notice'));candidates.append(row);}if(v.candidates.length>200)candidates.append(node('p',t('Hiển thị {shown}/{total} cache đủ điều kiện; đây chỉ là danh sách xem trước.',{shown:200,total:number(v.candidates.length)}),'notice'));content.append(candidates);
    const scope=node('details');scope.append(node('summary',t('Phạm vi đo và ổ đĩa')));const dl=node('dl',undefined,'readiness-fields');field(dl,'Giới hạn entry',number(v.limits.max_entries));field(dl,'Giới hạn độ sâu',number(v.limits.max_depth));field(dl,'Giới hạn thời gian (giây)',String(v.limits.max_seconds));field(dl,'Entry đã duyệt',number(v.scan.entries));field(dl,'Thời gian đã đo (ms)',number(v.scan.elapsed_ms));if(v.volume){field(dl,'Toàn ổ đĩa',bytes(v.volume.total_bytes));field(dl,'Ổ đĩa còn trống',bytes(v.volume.free_bytes));}else field(dl,'Ổ đĩa còn trống',t('Chưa biết'));scope.append(dl,node('p',t('Số liệu ổ đĩa áp dụng toàn volume; không phải riêng project.'),'notice'));content.append(scope);
  }
  const bound=(generation,token)=>panel.open&&generation===epoch&&token===session();
  async function loadReadiness(){
    if(!panel.open)return;const generation=epoch,turn=++readTurn,token=session();readValue=null;readError=false;readLoading=true;paintReadiness();
    try{const value=await api('/api/readiness');if(!bound(generation,token)||turn!==readTurn)return;if(!validReadiness(value))throw Error('Invalid readiness');readValue=value;
    }catch(_){if(bound(generation,token)&&turn===readTurn){readValue=null;readError=true;}}
    finally{if(bound(generation,token)&&turn===readTurn){readLoading=false;paintReadiness();}}
  }
  async function loadStorage(){
    if(!panel.open)return;const generation=epoch,turn=++storageTurn,token=session();storageValue=null;storageError=false;storageLoading=true;paintStorage();
    try{const value=await api('/api/storage/preview',{});if(!bound(generation,token)||turn!==storageTurn)return;if(!validStorage(value))throw Error('Invalid storage preview');storageValue=value;
    }catch(_){if(bound(generation,token)&&turn===storageTurn){storageValue=null;storageError=true;}}
    finally{if(bound(generation,token)&&turn===storageTurn){storageLoading=false;paintStorage();}}
  }
  panel.addEventListener('toggle',()=>{epoch++;readTurn++;storageTurn++;readValue=null;storageValue=null;readLoading=false;storageLoading=false;readError=false;storageError=false;paintReadiness();paintStorage();if(panel.open)return loadReadiness();});
  $('readiness-refresh').onclick=loadReadiness;$('storage-preview').onclick=loadStorage;
  document.addEventListener('issueintel:languagechange',()=>{if(panel.open){paintReadiness();paintStorage();}});
})();
