# Kiến trúc IssueIntel

Phase hiện hành (13/10/2026): [Strict DEV](STRICT_DEV_PROTOCOL.md) tách eligibility
khỏi artifact generation. `benchmarks.strict_dev` và `strict_dev_acquisition`
kiểm public provenance, source/reservations, opening query và snapshot-only;
không chạy graph/gold localization/LLM/evaluation. Release V2 bên dưới giữ nguyên
semantics cũ. Phase này chỉ kiểm đúng 400 Main Dev gốc và freeze N ≤ 400.
`main_dev_artifact_routing_v1` có cổng cohort Main Dev đã freeze và pilot trước bulk.

Milestone B: `benchmarks.dev_artifacts` replay/build deterministic graph, public
evidence và nhánh localization riêng; `benchmarks.dev_generation` đăng ký source,
policy và receipt theo stage chỉ trên accepted Main Dev. [DEV_ARTIFACTS.md](DEV_ARTIFACTS.md)
mô tả cổng pilot, stage exceptions, failure/resume và giới hạn nhãn.

## Pipeline chung

`investigation.claim_review` cung cấp `product_claim_review_v1`, source projection
riêng `claim_review_source_v1` và rubric `claim_sufficiency_v1`. Các trường raw
model reviews được giữ riêng với assessment tái tính; validator tái dựng để chặn
sửa kết quả rồi rehash. Quote của claim khớp nguyên văn statement; quote support,
counter và assumption khớp evidence đúng SHA/revision. Offset theo Unicode code
point, đầu gồm/cuối không gồm; không tự map ID hay sửa quote. Mỗi hypothesis có
đúng một review, 0–4 claim không chồng nhau; phần câu chưa được phủ vẫn hiển thị.
Code kiểm citation riêng với applicability/inferential-link do model nhận xét.
Thiếu exact support thì unsupported; thiếu applicability, mắt xích cơ chế/hành
vi, code cho suy luận hoặc còn unverified assumptions thì uncertain. Counter
không rõ ngữ cảnh/căn cứ cũng không được coi contradicted. Causal support giữ
uncertain khi không có proof thực thi độc lập. Mọi semantic/causal/human flags
vẫn unverified/false; kết quả không phải nhãn người hay đánh giá chất lượng.

`source_jobs.SourceJobs` là runner chung cho hai adapter cụ thể CheckPlanJobs và
ClaimReviewJobs: giữ nguyên DB/path/request contract của check plan, dùng DB/
result key riêng cho claims. Durable intent khóa source job + pack SHA, một target
kể cả lỗi/restart. Trước provider phải replay Git snapshot; trước mỗi call/output
repair kiểm lại product authority và source bindings; mỗi call giữ receipt owned stage. Tối đa một
output repair; lỗi transport/admission và cancel không tạo paid retry. Đọc/export
không reacquire Git, load API config hay construct provider. Claims được thêm vào
dossier/handoff bằng nguồn đã replay; portable verifier kiểm owner/quote bindings
trên included bytes, không đánh giá ngữ nghĩa. API/UI tách claim sidecar khỏi
whole-hypothesis critique cũ và không rewrite generation/verifier/report v1.

`investigation.issue_relations` cung cấp `product_issue_relation_v1` và
`issue_relation_sufficiency_v1` cho hai issue khác nhau. `issue_relation_source_v1`
giữ hai projection riêng đã seal, mỗi phía có identity, reference, commit/snapshot
khi có và các issue/code/context excerpts. Không cần annotation/hypotheses đã sinh.
Citation dùng `side + source_id + start/end + quote`; offset là Unicode code point,
đầu gồm/cuối không gồm. Raw model comparison giữ riêng assessment được tái tính
cho symptoms/mechanism/version/differences. Cơ chế phải có nguồn cả hai phía;
quote trùng triệu chứng không tự thành reported mechanism. Counter/giả định chưa
giải quyết hoặc khác biệt có ý nghĩa chặn nâng lên `possible_duplicate`. Phiên
bản ảnh hưởng chưa biết giữ gap và có thể cần hỏi thêm, không tự phủ nhận một
ứng viên có cơ chế được dẫn nguồn; commit snapshot không chứng minh affected
release hoặc fix. Mọi classification vẫn automatic/unverified, không confirmed
duplicate, causal proof hay nhãn người. Model payload dùng public projection để
không gửi machine path, job ID hoặc pack digest không cần thiết.

`issue_relation_jobs.IssueRelationJobs` là service cặp nguồn riêng, không dùng
single-source runner để suy quyền cho nguồn thứ hai. Candidate chỉ là saved
product investigation hoặc synced **issue**, không PR/comment. Server replay cả
hai authority/repository identity, từ chối self/same-query local mơ hồ,
cross-repository và benchmark/strict trước paid call. Hai GitHub issue có URL
canonical khác nhau vẫn có thể có nội dung giống hệt nhau. Preview khóa source
và policy; SQLite intent theo đúng selection/hash/policy giữ một target qua
restart, tối đa 20 intent cho mỗi source pack và hai call kể cả output repair.
Admission replay Git snapshot trước provider; guard trước/sau call và trước
publication kiểm lại authority, source/policy, cancellation và owned attempts/
receipts. Transport/admission lỗi không được dùng như quyền retry.

Intent lưu `issue_relation_inputs_v1`: exact UTF-8 pack bytes dưới dạng JSON string
và job authority cho phía local, hoặc full record/receipt đã chọn cho phía synced.
Saved reopen kiểm local pack/authority và phần synced đã khóa; không reacquire
Git, đọc index mới hoặc tạo provider/config. Vì vậy refresh index không đổi
comparison cũ; nguồn local bị sửa vẫn bị phát hiện. Sidecar ở
`artifacts/demo/issue_relation_runs/`, tách khỏi pack/report gốc. Dossier gắn
comparison vào source run, không biến candidate thành ancestry của source.

Handoff thêm `relations/<job>/inputs.json`, `comparison.json` và `comparison.md`;
preview dung lượng tính cả raw candidate inputs có thể nằm ngoài dossier đã chọn.
Standalone verifier dựng lại side projection từ included pack/record/receipt,
kiểm quotes, source/job references, policy/request binding, cận attempts khai báo
và tính lại sufficiency. Chỉ có bytes
được gửi mới được kiểm; không thay thế full candidate Git/ancestry replay, xác
thực tác giả, chứng nhận provider thực sự chạy hay đánh giá duplicate đúng/sai.
Viewer hiển thị kết quả và giới hạn này mà không gọi model hoặc tải nguồn mới.

`investigation.handoff.Handoffs` xuất một selection riêng từ dossier đã replay,
giữ pack bytes/report bindings, tự thêm ancestry và chỉ lấy checkpoint recovery
được con tham chiếu. Không sao chép folder job, database, `.env` hoặc Git cache.
Preview khóa dossier, lựa chọn, SHA ZIP và dung lượng; export tái dựng deterministic
ZIP rồi so đúng preview. Source/journal đổi thì từ chối. HTTP POST giữ session/
Origin guards; browser kiểm header, byte size và SHA256 trước download, chặn stale.
HTML tĩnh escape nội dung, CSP không script/network; giữ cảnh báo chưa kiểm hash.
`portable_verify.py` dùng thư viện chuẩn, được chép vào ZIP làm `verify.py` và
dùng chung bởi CLI `verify-handoff` (không load env). Verifier không extract hoặc
execute bundle, giới hạn 512 file/100 MB, từ chối path/link/duplicate/entry lạ,
kiểm manifest, nguồn report/pack/excerpt/ancestor/recovery. Kết quả chỉ khẳng định
included-byte consistency, không full Git replay, authorship hay semantic truth.
SHA ZIP phải trao qua kênh tin cậy nếu cần đối chiếu bên gửi; manifest tự hash
không phải chữ ký. Private bundles ở ignored artifacts/downloads, không vào CI.

```mermaid
flowchart LR
    A[CLI hoặc giao diện local] --> B[Public case boundary]
    B --> S[Git snapshot chỉ đọc]
    S --> G[Python AST graph]
    G --> R[BM25 + symbol + graph expansion]
    R --> E[Evidence có ID/hash/revision]
    E --> I[3–5 hypotheses]
    I --> V[Verifier pass riêng]
    V --> P[Investigation pack]
    C[Controller rules hoặc Jev adapter] --> V
    L[Ledger token/USD] --> I
    L --> V
    S --> D[Dataset construction]
    G --> D
    H[Restricted gold] --> D
    H --> J[Offline annotation judge]
    D --> Q[Localization và leakage audit]
```

`investigation.pipeline.investigate()` là application entry point. Nó nhận case công khai và repository đã chọn revision; không đọc gold, human rationale hoặc nhãn tham chiếu. Dataset preparation dùng cùng `build_snapshot()` và `build_graph()`, rồi mới tạo localization ở nhánh riêng.

Workbench bổ sung intake, follow-up, so sánh, truy xuất lịch sử và checklist qua
shared modules trong `investigation`/`retrieval`. Follow-up có lineage pack hash,
giữ commit và tạo query mới unverified. Related search có tìm ứng viên local và
synced issue; model comparison là thao tác riêng sau preview/consent, không nhập
vào strict inference. `investigation.plan.PlanStore` dùng SQLite local
riêng cho user-reported checks, hash chain và optimistic concurrency; không dùng
PostgreSQL hoặc sửa benchmark receipts. Interface chỉ render và gọi các service.

`investigation.check_journal.CheckJournalStore` lưu manual observations trong
table riêng cùng DB checklist, giữ nguyên plan v1. Check IDs lấy từ
`InvestigationReport.next_checks` đã dedup, không ánh xạ theo vị trí `step_N`
của checklist. Mỗi event khóa report/pack/query/snapshot/job, text SHA và đúng
hypothesis/evidence IDs do server lấy; recorded_at là lúc ghi, không proof chạy.
Full SHA người dùng tự báo đã thử giữ riêng commit phân tích, không resolve Git
hoặc mặc định bằng source SHA. Outcome là not_run/inconclusive/reported_match/
reported_difference; không chuyển thành semantic/causal/fix verification hoặc
independent human labels. Correction append-only cùng check, trỏ về record chưa
bị supersede; mọi record cũ còn trong export. `BEGIN IMMEDIATE` và expected SHA
chặn ghi đè/gửi lặp cùng phiên bản; giới hạn 100 event mỗi journal. Replay kiểm
schema/type, source/check bindings và toàn chuỗi, không xác thực danh tính tác
giả hay bảo đảm chống người kiểm soát toàn DB viết lại lịch sử. Workbench replay
product authority xuyên follow-up/context/resume, cấm benchmark/strict trước
đọc hoặc ghi journal. Read/export không lấy Git/network/model, không sửa nguồn.
JSON journal và Markdown report+journal có header report SHA và journal SHA;
UI kiểm cả hai khi tải để không xuất nhầm revision. Observations không tự đi
vào inference, GitHub draft hoặc dataset; pack/report v1/receipts giữ nguyên.

`investigation.report` tạo projection `investigation_report_v1` có JSON Schema
riêng từ pack đã lưu và SHA256 của đúng bytes nguồn. Projection không đọc Git,
network, gold hoặc gọi model; Workbench replay export và lineage trước khi dựng.
Web Overview, Markdown và nháp GitHub dùng cùng fields/citations/truth levels.
`execution.status=completed` chỉ nói lượt điều tra đã hoàn tất;
`waiting_for_user` biểu thị một pack Planned v3 đã lưu nhưng đang chờ dữ kiện.
Model critique
không chuyển `causal_status` hoặc semantic support sang verified. Severity giữ
unassessed, confidence probability giữ null, checks giữ not-executed. Excerpt
có hash riêng và dấu truncated; link code chỉ dùng repo khớp issue, commit đầy
đủ và line range. Report có digest riêng, không nằm trong inference pack.
Standalone CLI export thêm `report.json`; đọc pack cũ chỉ derive view, không sửa
pack/report.md/receipt. API tải Markdown mới tách khỏi route legacy đã lưu.
Nháp GitHub thu gọn có giới hạn, báo rõ chi tiết bị bỏ và vẫn duyệt đúng body SHA.
Schema được kiểm bằng jsonschema trong dev/CI; runtime chỉ dùng thư viện chuẩn.

`investigation.hypothesis_review` dựng `hypothesis_review_v1` thuần dữ liệu từ
report đã qua `validate_report`, giữ report SHA cùng toàn bộ source binding.
Mỗi hypothesis giữ support/counter, model review, check IDs và gap codes từ
metadata được ghi: chưa có counter trong phạm vi truy xuất, chưa có citation
code hỗ trợ hoặc model review thiếu/unsupported/contradicted/uncertain. Cặp giả
thuyết dùng giao ID theo thứ tự evidence gốc để lộ nguồn hỗ trợ/phản bác dùng
chung và citation khác vai trò. Không gộp các ID khác nhau chỉ vì nội dung giống,
không suy ra quan hệ nhân quả, phụ thuộc, độc lập hoặc mâu thuẫn ngữ nghĩa.
Validator tái dựng projection từ report và so toàn bộ để chặn sửa rồi tính lại
hash. UI đọc cùng lượt mở saved job, cache khi đổi ngôn ngữ và chặn citation/
download stale theo job/report/pack/query/snapshot. JSON/Markdown HTTP mang
`X-IssueIntel-Report-SHA256` từ chính projection được gửi. CLI export sidecar;
đọc bản cũ không tạo/sửa pack, report hay receipts. Không có Git/network/model,
evaluation hoặc xếp hạng chất lượng trong lớp đối chiếu này.

`investigation.clarifications` giữ template v1 Việt/Anh cố định cho bốn mục intake;
không gọi model. Workbench khóa parent pack/query/question bundle, lưu nguyên Q/A
theo ngôn ngữ đã xem và replay receipt khi đọc/export. Chỉ product và các hậu duệ
product được dùng; request authority được kiểm xuyên follow-up/context/resume,
không dùng benchmark dù follow-up đã bỏ nhãn strict. Trước provider phải replay
snapshot descriptor và excerpt từ Git ở đúng SHA. Query mới lấy code lại, không
kế thừa context repository đang khóa query cũ, quyền đăng GitHub hay checkpoint
verifier. Tám thế hệ dùng chung với follow-up/context. Câu hỏi tự sinh bị loại
khỏi lexical cues: intake lưu projection nội dung người dùng, hash và nguồn dòng
riêng; query SHA vẫn khóa toàn bộ đầu vào. “Có cue” và “đã trả lời” không chứng
minh thông tin đầy đủ/đúng. Template v1 phải được giữ để replay artifact cũ.

`investigation.finalization` và `finalization_jobs.FinalizationJobs` phục hồi
export khi generation/verifier product Quick/Deep đã lưu nhưng job thất bại,
ngắt hoặc bị hủy. Service xác thực source request, stage paths/bytes/SHA,
canonical type/schema và policy trước khi nhận, trong worker và trước publish.
Assembly dùng `finish_pack` với annotation và verifier nguyên vẹn; không gọi
provider, đọc config/API key hoặc truy cập Git. Snapshot chỉ replay descriptor
đã lưu, receipt ghi `full_git_replay_performed: false` và 0 call mới. SQLite
intent khóa một child cho mỗi source, enqueue/cancel dùng queue chung; export
sang child directory mới và không sở hữu stage/usage receipts của nguồn.
`finalized_from` khóa source request, hai raw stage SHA, prepared/verifier
payload và policy; mọi lần đọc dựng lại pack dự kiến rồi so toàn bộ digest.
Admission dùng policy hiện tại, bản đã hoàn tất dùng policy được ghi, không
recertify theo code mới. Product authority traversal không đòi pack nguồn đã
export; benchmark/Planned/follow-up và quyền GitHub write không đi qua luồng này.

`investigation.dossier.Dossiers` dựng timeline từ các product jobs cùng định danh
issue, kiểm original request với original case/stage và replay từng report trước
khi đưa vào hồ sơ. Jobs chưa xong chỉ là metadata, nguồn lỗi được ghi rõ; Main
Dev/strict và lineage benchmark bị chặn. Mỗi transition giữ hai pack/report
bindings và receipt quan sát lịch sử của child, so nhãn A/B với dự đoán saved
plan; commit không biết/khác commit và later correction vẫn tách riêng. Không
suy ra semantic correspondence từ ID giống nhau. Liên kết giả thuyết do người
dùng ghi vào `dossiers.sqlite` với exact evidence refs, lý do, hash chain và
expected SHA; correction append-only, mọi truth flags vẫn unverified. Đọc/xuất
không dùng Git/provider/config; source hay policy benchmark không bị sửa.

`investigation.adaptive_clarifications` thêm câu hỏi riêng dựa trên public issue,
evidence và hypotheses đã lưu, với prompt v1/hash và bilingual structured output.
Mỗi câu trỏ tới ít nhất hai hypothesis ID và một code citation; validation kiểm
reference/schema/hash, không xác nhận chất lượng phân biệt hay causal truth.
`question_jobs.QuestionJobs` dùng queue và receipt accounting chung; trước
provider replay snapshot/excerpts, khóa source pack/query/annotation/evidence.
SQLite intent giữ một attempt cho mỗi source job+pack SHA, kể cả failed/cancelled;
không auto retry, repair, regeneration hoặc resume. Bundle private tách khỏi
pack gốc, đọc lại dùng saved source, không mutable index/model. Adaptive answers
có schema riêng, giữ nguyên prompt/purpose/answer-use/IDs; lineage replay cả
question job và parent. Tám thế hệ và các cổng product-only giữ như template.

`retrieval.repository_context` có local search và explicit collector: 1–6 nguồn
khóa record+receipt SHA/global repo ID, không auto-expand. Context mode của
Workbench chỉ nhận parent GitHub product, replay parent pack/snapshot trước
provider, giữ query và commit, và tạo `context_selection` lineage hash-bound.
Preview/inference dùng cùng issue query để cắt excerpt; từ khóa tìm kiếm chỉ chọn
ứng viên. Read/export replay saved records/receipts không consult mutable index.
Giới hạn tám thế hệ đi chung với follow-up, context mode không được resume paid
verifier hoặc kế thừa GitHub write approval.

## Quyền sở hữu của các package

| Package | Trách nhiệm | Ranh giới |
|---|---|---|
| `repository` | Nhận GitHub URL/local Git; đọc Git objects; AST; cache blob có hash | Không checkout, import hoặc chạy test/code của repo đích |
| `retrieval` | BM25/identifier, loại chunk trùng, mở rộng cạnh typed có giới hạn | Điểm liên quan không phải xác suất đúng |
| `investigation` | Hypotheses, controller, critique, investigation pack | Chỉ public evidence; không gold judge |
| `annotations` | Schema, sinh nhãn, synthetic variants, blind review; judge tách riêng | Provenance LLM/synthetic/source-human/human riêng biệt |
| `benchmarks` | Pin/import nguồn; projection; temporal audit; asset/annotation workflow | Gold chỉ ở dataset construction/evaluator; default dev |
| `evaluation` | Metric localization và capability đọc holdout theo release | Cần ủy quyền evaluation mới; file patch là proxy, giữ file ngoài snapshot trong mẫu số |
| `runtime` | Cấu hình, hash, ghi atomic, sổ ngân sách SQLite | Thiếu limit/key/price thì từ chối; không có unlimited ngầm |
| `interfaces` | CLI gọi application code | Không nhét logic truy xuất/gán nhãn vào giao diện |

## Snapshot và provenance

Snapshot chứa commit, tree, Git blob OID, SHA256 raw bytes/canonical text, protocol, cutoff, skipped files và hash toàn snapshot. Cat-file batch đọc các blob trong một tiến trình, giới hạn 10.000 file, 512 KB/file và 25 MB/source snapshot. Symlink, submodule, binary, encoding lỗi và phần vượt giới hạn đều có lý do bỏ qua.

Graph có node file/class/function; cạnh contains/imports/inherits/calls. Quan hệ call tĩnh là suy luận từ syntax, không chứng minh hành vi runtime. Dynamic dispatch và tên mơ hồ giữ unresolved. Graph phải khớp revision/hash snapshot; snapshot được truyền vào pipeline phải khớp case và protocol.

Retrieval chỉ trích các dòng nguyên vẹn trong giới hạn excerpt; dòng vượt giới
hạn không bị cắt giữa dòng rồi gắn locator của cả dòng. Excerpt rỗng bị bỏ qua,
và phần còn lại giữ đúng start/end để replay với snapshot. Không thay hash của
artifact cũ khi thay đổi quy tắc retrieval; recipe cũ vẫn phải qua integrity gate.

Cache giữ text theo SHA256 trong blob gzip dùng chung; index snapshot và graph nén. Investigation pack chỉ chứa code excerpts và metadata liên quan, không chép toàn repository. Có thể rebuild derived cache từ nguồn, nhưng phải kiểm receipt/inventory trước khi coi một ca hoàn tất; không thay hash mong đợi để che lỗi.

## Temporal và gold isolation

`repository.memory` tạo `repository_architecture_memory_v1` từ snapshot/AST,
không nhận issue, annotation, gold hoặc model output. Capture giữ định nghĩa và
quan hệ calls/imports/inherits có đích exact; quan hệ vẫn là static inference,
không proof runtime. Tối đa 1.000 observations, mặc định 500; chọn luân phiên
loại để definitions không chiếm hết cận, ghi tổng và số bị bỏ. Mỗi observation
giữ node/span SHA cùng dependency file hashes; source giữ commit, snapshot,
parser và graph implementation SHA. Không sao chép code vào từng observation.

Saved replay kiểm source spans và bindings bằng snapshot cache, không chạy Git,
model hoặc parser hiện tại; receipt parser cũ vẫn xem được. Assessment riêng
xây AST ở revision đích: unchanged dependency chưa đủ nếu cách resolve quan hệ
đã đổi. Kết quả phân biệt changed/missing/unavailable, producer change và static
revalidation, giữ nguyên capture gốc. Historical eligibility còn cần receipt
đầy đủ của cả nguồn và revision đích, `available_at < cutoff`; Git dates hoặc
receipt SHA không có nội dung bị từ chối. Authenticity giữ trusted-caller boundary
hiện có.

`repository.memory_store.MemoryStore` giữ các capture bất biến trong SQLite,
payload dedup theo hash, pack witness gzip và snapshot cache dùng chung. SQLite
transaction serialize writes; cùng authority/source/payload trả receipt cũ qua
restart và không nhân bản source blobs. Gzip witnesses được publish nguyên khối
bằng atomic create sau fsync; ngắt trước transaction commit không để source
hash nửa chừng. Target đã có bytes xung đột bị từ chối, không ghi đè. Read/search replay receipt, object,
pack và source spans từ bytes đã lưu; không Git/parser/provider/config. FTS5
chỉ index tối đa 16.000 ký tự/capture, trả bounded local candidates, chưa đo
semantic recall. Receipt ghi caller-supplied source; Workbench phải xác minh
owned job authority trước khi gọi store, không suy ra authentic ownership từ
một ID tự truyền vào. Hash không là chữ ký hoặc chứng nhận người tạo.

`repository_investigation_lesson_v1` chỉ giữ những hypothesis ID được chọn theo
thứ tự nguồn, statement/falsification/support/counter verbatim, note chưa xác
minh và exact pack SHA. Không copy source-quality/judgment/correctness thành
nhãn đúng. Lesson luôn `unverified_prior_investigation`, không runtime/causal/
human verification và không historical eligibility. Code dependency assessment
giữ changed/missing/unavailable/no-code riêng; nguồn còn nguyên không xác minh
giả thuyết hoặc tính áp dụng cho issue mới. Không dùng recorded_at làm proof.

CLI `memory-capture/list/show/assess` gọi cùng store, không nạp `.env` hay model
config. Product-pack preflight từ chối split/instance IDs, restricted case IDs,
strict protocol/audit trước Git replay. Capture/assessment replay actual commit
bytes, show/list không cần checkout còn trên máy. Memory chỉ vào product prompt
khi người dùng chọn exact items, xem preview và xác nhận tạo phiên riêng; không
tự đưa vào benchmark prompts.

`investigation.product_memory.ProductMemory` là adapter Workbench cho cùng
store. Trước đọc pack, adapter kiểm completed product job và request metadata;
sau đó replay product lineage cùng canonical origin authority của dossier.
Binding gồm source job/pack SHA, repository và commit/snapshot đang xem.
`GET /api/jobs/{id}/memory-status` và các POST `memory-search/capture/open/assess/source`
dùng guard session/origin hiện có. Capture/open/assessment khóa exact receipt
SHA; capture thêm source job và pack witness. Repository scope khác bị từ chối.
Chỉ capture/assessment replay Git; hai thao tác này acquire/release shared work
capacity kể cả khi lỗi. Source preview dùng cached snapshot, giữ exact node/text
hash và line endings khi đủ chỗ, trích tối đa 80 LF lines/6.000 ký tự.

`static.memory` render tab product, source/revision identity, capture forms,
bounded local search, prior-hypothesis truth levels, dependency assessment và
source excerpts. Trạng thái/draft khóa theo job+pack SHA; stale controls không
được tác động phiên mới. Refresh/reopen/language không tạo capture hoặc gọi
model; ack lỗi giữ draft và yêu cầu đọc lại danh sách, queue-full có trạng thái
riêng. Assessment mới xóa status cũ trước khi chạy để lỗi không giữ badge thành
công. Chạy inference là thao tác xác nhận riêng từ preview; không tạo citation
code bằng cách sao chép nhãn hỗ trợ/phản bác của memory cũ.

`investigation.memory_selection.preview_memory_selection` chuẩn bị exact bounded
memory hints cho một product pack/snapshot. Mỗi lựa chọn khóa capture ID, receipt
SHA và item ID/SHA, tối đa sáu mục; duplicate, repo khác, source/receipt đổi hoặc
context vượt 16.000 ký tự đều bị từ chối, không tự bỏ item để làm vừa cận.
Architecture item phải qua current AST/producer/dependency revalidation; item
không còn khớp trả rejection có reason/status riêng, không có context từng phần.
Prior hypotheses giữ statement/falsification/note chưa xác minh, source query và
commit riêng, kèm dependency status; không mang nhãn correctness/human hoặc old
support/counter IDs vào public hints. Public repository name loại local machine
path; selection receipt giữ private lineage riêng. Preview luôn khai no runtime/
causal/historical eligibility, model_calls=0 và automatic_inference=false.

POST `memory-preview` kiểm canonical product authority/pack SHA, chặn waiting
Planned forks rồi acquire shared capacity, replay Git và chọn đúng immutable
items. Memory open/capture có `selectable_items` SHA để UI không tự bịa item hash.
UI giữ selection xuyên capture nhưng khóa vào viewed job/pack; thao tác chọn mới
xóa context/status cũ và giữ focus checkbox. Preview response phải khớp từng
item, nguồn, cận và truth flags. Preview không enqueue job, không gọi provider,
không viết lên pack/query hoặc tự trở thành inference.

`investigation.memory_followups.MemoryFollowups` nhận POST
`/api/memory-investigations` với parent job/pack SHA, selection SHA, exact items,
provider/depth và confirmation riêng cho offline/Groq. Trước enqueue và từng
call, service kiểm product ancestry, source captures, current snapshot/AST và
policy fingerprint. Kiểm lại sau call chặn repair/critic/publication nếu nguồn
đổi trong call; receipt đã ghi vẫn giữ nguyên. Waiting Planned không được mở
nhánh reset budget, Main Dev/strict bị từ chối. Dùng chung product pipeline;
code được truy xuất lại tại commit cha, không kế thừa verifier/nhãn đúng sai.

SQLite `memory_followups.sqlite` ghi một intent cho mỗi parent/pack/selection;
provider/depth không nằm trong khóa nên đổi chúng không mint thêm attempt.
Intent lưu exact selection bytes. Gửi lặp/restart trả cùng job; lỗi/hủy không tự
retry. Saved read kiểm selection/query/snapshot/job request/durable intent mà
không dùng Git/current AST/policy hoặc memory store hiện tại. Generated memory
không được tính vào intake cue của người báo. Phiên con giữ selected-memory
trace, report provenance và lineage; subsequent product follow-ups vẫn dùng
canonical origin và bound tám đời hiện có.

UI preview có offline mặc định, Quick/Deep, Groq với consent riêng; lost ack
khóa exact request và cho kiểm lại cùng intent. Chuyển source/đổi lựa chọn loại
stale execution controls. Saved memory được trình bày riêng trên phiên con.
Handoff thêm exact `memory-selection.json` cùng pack/report và tổ tiên. Verifier
standalone kiểm attachment, item/receipt/source bindings, frozen v1 projection,
query nối và truth flags. Không đọc original capture store hoặc replay AST;
hash consistency không xác thực tác giả hay độc lập chứng minh static semantics,
causal truth, historical availability hoặc hóa đơn provider.

Product repository memory nằm riêng trong `integrations.github_index`:
`RepositoryIndex` đồng bộ REST read-only có page receipts/cursor transaction,
global repository ID và OS writer lock; lưu private SQLite/FTS5, snapshot/graph
theo hash. CLI và workbench gọi cùng service cho sync/status/search/item;
sync jobs dùng chung queue/capacity nhưng giữ lịch sử riêng với investigation.
Progress/cancel kiểm tại ranh giới stage/page; ngắt không xóa page đã commit.
UI hiển thị source/scan/commit riêng với controls và hỗ trợ Việt/Anh, sáng/tối.
Refresh đối soát
thủ công; dữ liệu current REST không được đưa vào benchmark pipeline hoặc
coi là historical proof. Không lưu patch/diff, không gọi model. Xem
[REPOSITORY_INDEX.md](REPOSITORY_INDEX.md) về coverage/caps và replay.

`integrations.github_sync` giữ connection lifecycle private trong SQLite;
scheduler workbench chia đợt 20 GET trên cùng queue để tự hoàn tất initial scan.
`repository_updates` xác minh HMAC trước JSON, bind App/installation/repository
global ID, dedup delivery và đọc lại REST canonical theo event. Page receipt,
cursor và mutation index commit cùng transaction. Unavailable records giữ audit
và bị loại khỏi search; PR membership thay đổi chỉ đối soát khi listing không
chạm cap. Push resolve tracked ref hiện tại, không dùng event cũ để rollback.
Network/rate-limit có cooldown qua restart; identity/integrity lỗi tạm dừng.
Stop server giữ phase tự động; pause của người dùng giữ riêng và resume đối soát.

`retrieval.repository_context` lấy tối đa 6 nguồn/7.200 ký tự excerpt từ repo đã
kết nối, ưu tiên linked records rồi lexical hits. `record_links` phân biệt text
mention chưa xác minh, comment membership và PR–file/commit do API trả về.
Pipeline replay source/receipt/query bindings trước model; full source records
chỉ ở pack private, model projection nhận excerpts. Context luôn current,
`available_at=None`; bị chặn với split benchmark, strict và prepared evidence.
Comparison và comment preview giữ nguồn/hash/links của context đã chọn.

Hai protocol: `benchmark_base` đọc base_commit của upstream và khai báo thời gian chưa xác minh; `issue_time` từ chối nếu không có receipt xác minh đúng revision với `available_at < issue_created_at`. Hash đúng không chứng minh lịch sử đúng. Receipt authenticity thuộc trách nhiệm caller; audit lưu giới hạn này.

Target issue là truy vấn tại thời điểm mở nên có ngoại lệ `<=` cho original title/body. Nội dung API hiện tại và `updated_at` không đủ chứng minh body ban đầu. Human notes, patch, test_patch, test IDs, hints và metadata evaluation không đi vào prompt public. Gold localization chỉ trích file/dòng phía base/symbol từ unified diff; insertion có anchor riêng, added file không bịa pre-patch symbol.

`benchmarks.asof` chuẩn bị receipt lịch sử, còn `asof_assets` replay receipt trước khi xây snapshot `issue_time`. Base nguồn giữ nguyên; selected ancestor phải có ancestry và public availability riêng. Query ban đầu nằm trong overlay có parent/hash, không sửa case nguồn. GitHub opened event và Trac version 0 có adapter riêng. Nhãn người của query nguồn không tự áp dụng cho overlay.

`benchmarks.construction` đăng ký recipe trước call và lưu các stage generation/critique/judge/variants riêng. Hash dependency theo protocol: thay đổi validator lịch sử chặn strict resume; code generation chung chặn cả hai protocol. ZIP source/config của registration giữ hash của lần chạy. Preparation/annotation construction có thể tạo asset/nhãn holdout theo recipe, nhưng không mở evaluation capability, tính metrics hoặc tune model.

`benchmarks.releases` chốt các ca đủ proof và đủ bốn stage, giữ exclusions và archive của recipe. `evaluation.open_test_split` nhận release có hash cùng ủy quyền/purpose mới; capability đọc đúng cohort đã chốt, replay integrity mỗi lần đọc và hết hiệu lực khi rời context. Cổng bootstrap cũ vẫn giữ dữ liệu Live chưa xác minh ở trạng thái bị chặn. Các module này không gọi model hoặc tự chạy metrics.

`benchmarks.cohorts` tạo view cho extension Live có parent/source/selection hashes, tách cases/eligibility/restricted gold. View yêu cầu lựa chọn rõ ràng, không đổi default dev/test loaders hoặc nguồn bootstrap. Historical và annotation builders chỉ nhận namespace được hỗ trợ bởi phiên bản code/recipe tương ứng; không truyền ID ngoài manifest để lách kiểm.

## Controller và verifier

`investigation.discriminating_checks` có output contract cho kiểm tra đề xuất:
procedure/assumptions, outcomes A/B, predictions đầy đủ cho 3–5 hypothesis và
code citations hợp lệ. Selector đếm cặp có prediction A/B khác nhau, bỏ unknown,
rồi dùng model-estimated effort làm tie-break; tất cả giữ unreviewed/unverified.
Không gán xác suất, chạy check hoặc chứng nhận causal correctness. Khai báo
outcome khác nhau về label chưa chứng minh phân biệt về ngữ nghĩa.

`investigation.policy_agent` cung cấp chung bounded search_code/read_code trên
snapshot đã replay cho hai chính sách prompt. Seed/context, schema và budgets
giống nhau; action chỉ đọc tối đa 60 dòng/4.000 ký tự, ba round. Error/repeated
request vẫn tiêu hao round; final call tối đa 16.384 output tokens. Protocol v10
dành một repair slot cho final, tối đa năm call kể cả repair. Các stage dùng effort
medium; cả hai policy cùng cấu hình. Chỉ output lỗi được
sửa bằng một request mới từ context/schema gốc; auth/rate/network/budget và lỗi
integrity ở call store không đi vào repair. Record cũ giữ nguyên, không bịa hoặc
ánh xạ lại citation/path. Ranked paths chỉ thuộc file đã đọc/truy xuất; schema
và local validator cùng kiểm enum, độ dài và đủ prediction. Action/final dùng
instruction riêng, final không còn danh sách tool/file index mời gọi hành động.
`benchmarks.policy_pilot` preregister 25 accepted original Main Dev cùng source/
query/snapshot/code/config hashes và thứ tự arm luân phiên.
JSON payload được canonical hóa trước hash/HTTP, nên key order trong response
đã lưu không làm đổi request ở stage tiếp theo khi replay. Call intent giữ request
hash và receipt ownership trước HTTP, response được giữ trước validation. Resume
replay bằng saved calls, cấm tạo call thay thế khi receipt/output bị thiếu.
Action và final dùng constrained `json_schema`, `strict:true`, schema đầy đủ
đặt trong response_format; không thêm native tools/tool_choice/parallel_tool_calls.
Planner được yêu cầu phân loại catalog entry thay vì sinh command. Schema choice_id
chỉ nhận STOP hoặc R1…/S1…, cùng reason; không có raw query/range field. R ID ánh xạ chính xác
về locator đã tính, không ánh xạ output sai về một file hợp lệ. Raw proposal được
giữ trong action record và replay cùng catalog/hash; local schema/semantic guard
từ chối ID lạ và field thừa. Code vẫn kiểm bounds trước search/read.
Provider từ chối native tool_calls ngay cả khi có JSON content. Catalog cho tối đa
12 locator hợp lệ, chưa dùng, mỗi locator tối đa 500 ký tự, tính từ file trong
evidence hiện tại. S ID chọn trong tối đa 24 query đã loại trùng/đã dùng, sinh từ
identifier/camel-case/underscore trong issue, class/def names trong evidence và
từ khóa dài; heuristic không đọc gold/human rationale hoặc tạo bằng chứng mới.
Model không được đặt query/range tùy ý; search vẫn tìm toàn snapshot và có thể
cung cấp file mới cho round sau. Hai policy có cùng menu/rules; đây là phạm vi
controller có catalog, không có quyền chọn query tự do như harness ban đầu. Lịch sử
trước lựa chọn giữ ID, locator/query, status/failure và evidence IDs thay vì mời
model gọi function. Không cắt yêu cầu đọc sai để làm thành công. ToolRequestError trả mã lỗi
riêng cho range/EOF/character bound/path/empty/repeated request vào past_actions,
để planner có thể sửa ở round tiếp theo; lỗi snapshot/retrieval không bị che thành
lỗi argument. Planner có output không hợp lệ thì chuyển ngay
deterministic finish với `controller_degraded`/failure trace và không execute
proposal sai. Final vẫn phải qua tất cả validation; không synthesize success.
Final dùng cùng strict schema, đóng phase tool; payload gộp thành một user
message. Canonical findings_schema vẫn giới hạn năm ranked_paths duy nhất.
wire_findings_schema thay mảng path bằng file_preferences object đóng, mỗi P ID
khóa vào một file evidence, giá trị rank_1…rank_5/omit. Groq đã từ chối uniqueItems
ở control v8 nên wire không dùng keyword này; local validator vẫn kiểm uniqueness.
project_findings kiểm toàn bộ wire rồi sắp theo mức ưu tiên và thứ tự catalog đã
lưu khi đồng hạng, lấy tối đa năm file và chạy validate_findings đầy đủ. Không
nhận danh sách path lỗi để tự loại trùng/ánh xạ; ID lạ, key thiếu/thừa hoặc giá trị
sai bị từ chối. Result giữ candidate catalog, raw preferences, các nhóm đồng
hạng và rule ở ranking_projection; đây là quy tắc ưu tiên tương đối, không phải
thứ tự model phân biệt hết hay confidence/causal truth. HTTP status, allowlisted
error code, retry-after hữu hạn và classification được lưu; không lưu message,
response body hoặc key vào error metadata. Saved terminal output errors được
phát lại như exception để đi vào đúng repair stage đã ghi, không gửi lại HTTP.
Ca đầu cần hai arm hoàn tất trước full diagnostic. Provider/storage errors dừng;
output failure sau repair ở ca sau giữ denominator và không retry. Separate scorer chỉ
đọc exact DEV patch rows sau sealed inference; localization là file proxy gồm
unavailable files. Không chạy holdout capability, đọc Live, nới strict contract
hoặc sửa artifact/receipt cũ. Human/semantic check scoring giữ unreviewed.
Lỗi integrity hoặc runtime ngoài output rejection cũng dừng cả runner. Stop reason
được giữ trong arm record và kiểm lại khi resume, nên một failed arm không thể
bị bỏ qua để tiếp tục gọi trả phí ở arm khác sau lỗi receipt/request binding.
Mode reliability regression đăng ký bốn ca đã accepted từ parent sealed,
replay proof/source hiện tại và giữ bindings parent. Chỉ kiểm lỗi output, không
đọc gold/scorer; selection theo lỗi cũ không phải thí nghiệm chất lượng độc lập.
Không có bulk rerun tự động. Regression chỉ mở gate sau hai arm đầu hoàn tất
mà không degrade/repair/invalid action. Summary đếm failed call records, repair
records và invalid/repeated tool actions riêng với valid final reports.
clean_regression yêu cầu tám arm, inference sealed và mọi bộ đếm lỗi bằng 0;
hai lượt ổn định phải dùng hai registration mới cùng fingerprint, không retry
stage trả phí. Khác transport/search/read scope/ranking contract/budget được ghi trong spec v10; bundle cũ
cần đúng source archive cho replay, report đã lưu vẫn đọc độc lập.

`investigation.evidence_graph` là view của báo cáo đã lưu, khác với AST graph.
Replay query/hash/citation/context bindings và validate critique trước khi chiếu
ra query, hypotheses, evidence và model reviews. Quan hệ truy xuất, asserted
support/counter, assessment và critic citation giữ riêng; không nâng thành causal
truth/human validation. Giao diện SVG có keyboard/inspector/filter và mở citation
về đúng job. View tối đa 100 nút, 300 cạnh, 8.000 ký tự/nút, đếm phần lược bỏ và
không giữ cạnh trỏ ra ngoài view. Workbench bind thêm job/pack SHA256; không sửa
pack gốc, gọi model, xây code graph mới hoặc mở evaluation.

`investigation.versions` đối chiếu hai snapshot trong cùng repository, replay
descriptor/hash/excerpts của báo cáo nguồn trước khi đọc target. Chỉ original
product case dùng benchmark_base ngoài mọi split; follow-up/strict/benchmark bị
chặn. Workbench chạy job riêng trong cùng queue, có cancel và export hash-bound
về source pack; không sửa pack/citation/eligibility. Đếm mọi tracked text được
snapshot nhận, giữ skipped là unavailable; hiển thị tối đa 200 mục và bốn diff
file evidence (2.000 dòng/40.000 ký tự input và 12.000 ký tự output mỗi diff).
Không chạy test, gọi LLM hoặc xác nhận bản sửa/release.

`investigation.reachability` bổ sung ancestry view của cùng repository product.
Resolve ref một lần về SHA, so source/target hai chiều, tùy chọn kiểm commit tham
chiếu và tối đa 200 tag local chụp cùng OID. Không checkout/hook/lazy-fetch;
replacement/commit-graph bị tắt, graft được bỏ qua và đánh dấu altered. Kết quả
âm tính thiếu lịch sử giữ unknown; không gán tag Git thành published release.
Giới hạn thời gian/output và callback cancel áp dụng giữa lệnh. Workbench pin
reference fetched riêng về SHA trước inspection; hash nested result và source/
target/reference được kiểm lại khi đọc export, không chạy lại Git hoặc model.

Rule workflow là baseline cố định. `JevController` nhận state cùng allowed actions,
chỉ trả enum hợp lệ; hành động khác fallback về rules, với cờ fallback quan sát
được. Code vẫn giữ quyền kiểm ngân sách và thực thi; model không chạy command.
`typesafe_controller.TypesafePlannedController` nối riêng API Typesafe cho product
Planned có Groq. Mỗi decision khóa query/snapshot, annotation và inventory nguồn;
state chỉ chứa issue/hypothesis/evidence excerpt rút gọn có cờ truncation, cùng
counts/gaps quan sát. Request một Choice với đúng action allowlist, không gửi
toàn repo, gold hoặc source quality. Tối đa bốn decision, không ảnh hưởng cận
generation/critic của plan v1. Sidecar `typesafe_controller_v1` khóa plan SHA,
job, request, response và receipt; replay thuần dữ liệu đối chiếu decision vào
seed/round/final state, không mạng/model/ledger và không suy ra causal truth.
Workbench còn kiểm receipt ownership với job, từ chối sidecar thiếu/đổi nguồn.

Verifier kiểm hash/ID trước. Pass LLM riêng kiểm support/counter và trả assessment có citation; đây là critique của cùng model khi dùng cùng provider, không được gọi là kiểm người độc lập. Semantic correctness và causal truth vẫn phân biệt. Khi citation sai, generator chỉ được sửa schema một lần với allowlist; lỗi budget/network không được tự retry.

Product Deep có `refinement_rounds=1`: controller chọn retrieve/verify/stop sau
seed generation; `retrieval.focused` chạy tối đa ba query/sáu excerpt trong chính
snapshot đã kiểm. Hit replay bytes/range/revision và không trùng/overlap. Chỉ có
hit mới mới cho sinh lại, rồi chuyển sang verify/stop với zero rounds remaining.
Không nhập history/gold và không nới as-of proof; prepared evidence đã freeze
không được mở rộng. Mặc định pipeline/CLI vẫn zero rounds cho baseline.

Product Planned là lựa chọn riêng `planned_workflow=True`, Web `depth=planned`,
CLI `--planned`; không kết hợp Deep, strict, benchmark case, prepared evidence
hoặc resume. `investigation.planner` định tuyến bằng dấu hiệu quan sát được từ
seed (file, missing counter, parse/skipped gaps), khóa budget một đến ba vòng.
Pipeline dùng controller typed retrieve/verify/stop; tối đa ba query/sáu excerpt
mỗi vòng và 18 excerpt mới tổng cộng. Hash query được khử trùng cả trong/vượt
vòng, kể cả query issue gốc; span/ID evidence không lặp. Không có hit mới thì
không sinh lại. Tối đa bốn lượt generation (mỗi lượt một schema repair) và một
critic, tức chín provider calls. Rules vẫn là mặc định; không gọi Jev tự động.
`planned_investigation_v1` lưu seed, annotation/evidence chain, query/search,
controller/fallback, routing, giới hạn, counts và stop reason; validator replay
liên kết query/snapshot/run ID và hash trước saved-read/report. Không suy ra
causal truth hoặc độ khó đã hiệu chỉnh từ route. Plan và checkpoint không cấp
quyền thực thi tool/code hoặc benchmark evaluation. Các stage cố định
`planned_input`, `planned_retrieval_1..3`, `planned_generation_1..3`,
`planned_complete` giữ tiến độ khi lỗi; receipt chi phí gắn đúng stage.

Web rules Planned mới dùng `planned_investigation_v3` / `bounded_planner_v3`;
CLI thường `--planned` và Jev giữ v2; CLI `--durable` với rules dùng v3. Library `investigate` giữ v1 mặc định cho caller
cũ và nhận explicit `planner_policy='bounded_planner_v2'` hoặc v3. Mọi phiên bản
đi qua cùng pipeline/validator, version pair phải khớp; không sửa hoặc chuyển
đổi pack v1/v2 đã lưu. V2/v3 dùng
`retrieval.gap_directed` thay focused query chung: mỗi action khóa purpose,
gap/reason, input hypothesis, query/hash và source IDs. Support/counter tìm code
theo statement hoặc falsification; caller chỉ đi ngược cạnh `call` exact tới
symbol trong code đang dẫn; documentation/history lọc đường dẫn của snapshot.
History cũng có thể ghi nhận đã xem context issue/PR có receipt trong inventory
gốc. Không chèn history chưa có receipt, không acquisition/network và không đổi
context hiện tại thành historical availability proof. Các unavailable reason chỉ
mô tả scope hiện có; không phải khẳng định phủ định về toàn repository.

Action order deterministic luân phiên các purpose trong cận ba action/vòng;
query đã attempted kể cả unavailable không chạy lại. Chỉ thêm excerpt cùng
snapshot, exact bytes/span và không overlap. Khi không thêm excerpt, v2 có thể
thử purpose tiếp trong số vòng còn lại mà không generation; v1 giữ stop behavior
gốc. Pure replay đối chiếu query/action/source/provenance, exact caller edge và
lọc loại đường dẫn bên cạnh source/inventory/budget validation chung. UI render
cùng action receipt, giữ candidate/counter status chưa xác nhận.

`investigation.planned_waiting` bổ sung pure replay cho v3 và persisted
`planned_waiting_v1`. Chỉ một lượt typed search thực sự đã attempted query nhưng
không thêm excerpt mới mới có thể dẫn tới `waiting_for_user`: còn cue intake
chưa được xem và còn một generation, một repair dự phòng cùng critic trong
budget. Câu hỏi lấy từ template clarification v1 Việt/Anh, loại những field đã
trả lời ở tổ tiên; “chưa biết” vẫn là câu trả lời chưa xác minh, không phải proof
thông tin đầy đủ. Pack chờ chỉ có structural verification, chưa gọi critic.
`inherited` giữ root source, routing/limits, counts, query hashes đã attempted
và answered fields. Tổng cha + con không vượt cận route gốc: 1–3 retrieval rounds,
tối đa bốn generation (mỗi lượt tối đa một repair), một critic và chín Groq calls.
Một câu trả lời không cấp vòng/call mới và không đặt lại routing.

`planned_continuations.PlannedContinuations` dùng queue/pipeline chung. GET
`/api/jobs/{source}/planned-continuation` replay nguồn/receipt và trả preview
khóa source pack, workflow plan, waiting và policy SHA; so policy hiện tại để
báo availability, không đọc Git hoặc gọi provider. POST cùng route yêu cầu đủ
bốn binding, answers/language và `continue_planned_with_api_cost`. Admission
replay snapshot Git, policy hiện tại và owned call receipts của đúng source job;
worker kiểm nguồn lại trước mỗi call/repair và trước publish. Reserved hoặc
không rõ accounting bị chặn; failed call chỉ nhận khi có đúng repair hoàn tất
đã ghi. SQLite intent khóa một child cho mỗi source pause, kể cả lỗi/hủy/ngắt;
gửi lại cùng request trả child cũ, đổi answers bị từ chối, không tạo paid retry.

Child giữ canonical case identity và exact snapshot của cha; query thêm nguyên
văn answers với provenance unverified và luôn chạy generation mới. Code evidence
của cha được copy theo thứ tự; context records/receipts đã chọn được giữ nguyên,
chỉ rebind envelope/excerpts vào query mới. Không đọc mutable history index hoặc
tìm context nguồn mới. `continued_from` khóa parent pack/plan/waiting/policy,
answer receipt và user cue projection; template text tự sinh không được tính
thành cue người dùng. Các đường generic follow-up, clarification, context và
observation follow-up từ pack đang chờ bị chặn trước enqueue và provider, để
không mở một budget mới thay continuation. Journals và review sidecars vẫn đọc
được theo cổng riêng.

Saved-read replay toàn lineage, intent và bindings bằng bytes/policy đã ghi,
không đọc Git, API config, provider hoặc policy hiện tại. Report có trạng thái
chờ và provenance continuation; dossier giữ cùng issue identity. Handoff tự thêm
tổ tiên, portable verifier kiểm chain/query/answers/counters và frozen sources
trên included bytes bằng Python chuẩn. Nó không xác thực hóa đơn provider,
authorship, historical availability hoặc semantic truth. D05 waiting/continuation
đã nghiệm thu và đóng issue #5 với `2773a8c`, sau khi exact-commit CI thành công.

`investigation.stages` ghi checkpoint private sau từng stage đã hoàn tất, giữ
seed khi stage sau lỗi; không overwrite, không retry hoặc tự resume paid calls.
WorkBench/CLI cung cấp callback lưu, pipeline chịu trách nhiệm nội dung và
thứ tự. Các checkpoint này không phải benchmark receipts hay kết quả evaluation.

`investigation.resume` replay `verification_input` của product Quick/Deep: khóa
query/snapshot, replay bytes/citation và annotation, so policy SHA từ source code,
rồi chỉ gọi verifier. Main Dev/strict/follow-up bị chặn. Workbench GET resume chỉ
preview; POST yêu cầu exact checkpoint SHA và xác nhận chi phí. SQLite intent
`BEGIN IMMEDIATE` khóa một child cho mỗi source; worker kiểm intent durable và
source lại trước provider. Giữ nguyên source job/receipts, child có checkpoint
verification và receipt riêng; export liên kết source bằng hash. Một verifier đã
hoàn tất không được gọi lại theo luồng này. Không tự phục hồi hoặc retry API.

`investigation.stage_recovery.StageTape` bổ sung lõi opt-in trong `investigate()`
với `recoverable=True`, durable `on_stage` và tùy chọn `on_recovery_attempt`.
`execution_input` khóa public case, snapshot descriptor, graph, recipe và SHA
của các implementation phụ thuộc; `initial_retrieval` giữ kết quả truy xuất gốc.
Các stage sau giữ cùng envelope v1, thêm sequence/index, input digest và chuỗi
**canonical-record digests**. Chúng khác SHA của bytes JSON do StageCheckpoints
ghi; caller vẫn phải kiểm raw-byte receipts trước khi đưa journal vào pipeline.

`recovery_records` là một completed prefix có thứ tự, không phải danh sách stage
tùy chọn. Replay kiểm source/recipe/sequence, bỏ qua operation đã xong và không
ghi lại checkpoint cũ. Cached code được đối chiếu actual snapshot bytes;
annotation giữ citation/model/public-only constraints; verifier phải khớp loại
critic được yêu cầu. Query, graph, policy, round hoặc stage inputs đổi đều làm
replay bị từ chối. Git/graph vẫn được đọc để kiểm nguồn; completed retrieval và
model generation không chạy lại. Nếu HEAD đổi, đọc full commit gốc trong header,
không đổi query/request để theo revision mới.

Cơ chế này hỗ trợ original product Quick/Deep và rules Planned v1/v2/v3,
kể cả zero-call offline. Có thể tái dùng seed/refined generation, initial/focused
retrieval, từng planned retrieval/generation, verification và completed plan.
Waiting của v3 vẫn giữ nguyên semantics; replay không biến câu hỏi đang chờ
thành dữ kiện đã được người dùng trả lời. Benchmark/strict/instance/split bị
chặn trước Git. Follow-up, answer continuation và Jev có các authority riêng
mô tả bên dưới; controller tùy ý không được nhận vào recovery recipe.

Recipe ghi cận attempt 0 offline, 4 Quick, 6 Deep hoặc 9 Planned. Khi replay,
caller phải cung cấp `prior_provider_calls` đã biết, tính cả failed/in-flight
attempts; thiếu hoặc thấp hơn journal bị từ chối. Attempt sink chạy trước provider
và có thể giữ durable intent trước HTTP; sink lỗi không gọi model. Policy kiểm
trước/sau call, kể cả sau attempt sink, nên policy đổi không đi tiếp sang repair
hoặc critic. `stage_recovery` giữ inherited/new attempt bounds và danh sách record
đã dùng; đây không phải hóa đơn hay khẳng định mọi attempt đã tới provider.

Workbench bật capture cho original product example/local/GitHub Quick/Deep và
rules Planned dùng model đã cho phép hoặc offline. `stage_jobs.StageRecoveryJobs`
kiểm owned raw-byte receipts, canonical request/case/recipe, context, source
policy, attempt ledger và trạng thái failed/cancelled/interrupted. Lease SQLite
giữ trong suốt worker; process khác không được coi worker đang sở hữu là đã
ngắt để chạy trùng. Attempt sink ghi trước HTTP, cả lượt lỗi vẫn trừ vào cận.
Canonical JSON digests cho phép replay tuple/array sau khi serialize, trong khi
byte SHA vẫn kiểm đúng file gốc; không rewrite receipt để làm khớp.

GET preview replay Git/graph/prefix, dừng trước call cần thiết đầu tiên và không
construct provider. POST yêu cầu đúng binding SHA cùng xác nhận có/không chi
phí. Durable intent khóa một child cho mỗi nguồn; duplicate trả cùng child qua
restart/cancel/failure, không cấp retry mới. Worker kiểm nguồn/policy trước và
sau call, trước publication; provider/output config phải khớp recipe. Zero-API
completion không load key/config/provider. Stage cũ không ghi lại, child chỉ lưu
tail mới; saved read kiểm owned sources/intent/pack mà không cần Git hoặc policy
hiện hành. Verifier/export cũ giữ quyền xử lý late Quick/Deep riêng, tránh mở
hai đường retry tính phí cho cùng bước. Metadata recovery mới vẫn tương thích
với hai checkpoint của finalization cũ.

`recovered_from` và telemetry giữ source binding, inherited/new attempt bounds;
không cộng lại receipt cũ vào usage child. Dossier giữ failed root và recovered
child. Handoff thêm stage bytes của hai phiên cùng `stage-recovery.json` witness
cho request/attempts/usage. Portable verifier kiểm recorded source, sequence,
canonical/raw hashes, budget và output binding trên included bytes; không tái
chứng minh AST, hóa đơn, tác giả hoặc semantic truth. Browser có preview/consent
Việt/Anh và chặn stale selection/ack; chuyển xem phiên khác không để background
completion ghi đè report hoặc chi phí đang xem.

CLI `investigate --durable` dùng canonical original product request và dispatcher
của Workbench, không sao chép pipeline/recovery logic. `resume-stages` mặc định
preview; execution cần binding SHA cùng một trong hai cờ consent có/không API.
Các lệnh này tạo Workbench với `automatic_sync=False`, giữ nguyên connection
states và không khởi động scheduler/reconciliation. Preview/duplicate không đọc
key hoặc cấu hình model. Initial remote acquisition có thể đọc `.env` để lấy
GitHub credential; nguồn local offline không đọc. Khi cần model mới, provider
factory mới đọc key. CLI thường giữ recipe/output cũ, không tự migrate.

Lease SQLite cho original rules jobs và stage-resume jobs bắt đầu ngay khi
enqueue; dispatcher chuyển connection cho đúng một worker. Cancel/dispatch
failure/worker-finally giải phóng lock và queue slot. Observer ở process khác
probe lock thực để giữ queued/running; không suy đoán worker chết chỉ vì local
active set trống. Admission đã giữ lease dùng stopped-source check riêng để
không nhầm lock của chính reader là worker sống sau crash. Không có lock thì
raw queued/running có thể được trình bày interrupted; chỉ journal đủ proof mới
được nhận. Legacy jobs chưa có lease vẫn dùng recovery contract hiện có.

`product_stage_recovery_source_v2` nhận failed/cancelled/interrupted stage-resume
descendants của cùng original product. Binding ghép tối đa tám phiên nguồn,
gắn `owner_job_id` với mỗi raw stage hash và giữ request/attempt/usage/input-binding
của từng đoạn. `_source_state()` đi ancestry có giới hạn, replay durable intent
của mỗi cạnh, nối đúng completed prefix và global attempt sequence; không copy
stage/receipt hoặc đặt lại 4/6/9 call bounds. Unknown ledger, mixed owner, receipt
ID trùng, source/query/policy đổi hoặc hết bound đều từ chối. Original late
Quick/Deep vẫn thuộc verifier/export channels cũ; failed recovered child dùng
stage recovery để tiếp tục critic mà không sinh lại generation đã hoàn tất.
Đọc ancestor intents dùng SQLite read-only, tránh lấy write lock lồng trong
dispatch transaction; worker vẫn đợi own intent commit trước khi thực thi.

Root source v1 giữ nguyên projection/digest, không migrate receipt. Handoff v2
giữ request/attempt/usage/owned stage refs cùng input binding của mọi ancestor;
toàn bộ ancestor bắt buộc đi cùng selection. Standalone verifier tái dựng từng
binding v1/v2, owner/hash/order/call counters và output từ included bytes. Nó
không chứng minh authorship, Git/AST hoặc hóa đơn. Giao diện nhận failed recovered
jobs, giữ root ID/source count và yêu cầu consent mới cho mỗi nguồn lỗi mới;
duplicate cùng nguồn vẫn trả một child. Saved read/duplicate không cần policy
hiện hành, Git, config/key hoặc provider; admission mới vẫn kiểm policy hiện tại.

Planned contexts mới ghi `call_budget_policy: profile_and_seed_route_v1` và số
parse errors của graph đã khóa. Sau seed, `recorded_call_limit()` dùng seed
evidence/annotation, skipped files và count đó qua cùng routing rules để giữ
cận 5/7/9 đã chốt. Mọi failed/in-flight attempt đều dùng cận này, kể cả tại critic
sau nhiều lần phục hồi. Trước seed chưa đủ routing inputs thì giữ profile 9;
khi biết cận thấp hơn mà attempts đã vượt, dừng và giữ checkpoint, không publish
completed pack hoặc gọi thêm. Context digest được replay với actual Git/graph
trước call mới. Telemetry thêm `effective_max_provider_calls` khi thấp hơn profile;
child/portable validators bắt buộc giá trị đúng theo recorded policy. Context cũ
không có marker vẫn được đọc theo policy đã ghi, không sửa bytes/hash để nâng
thành policy mới; current admission policy vẫn có thể từ chối gọi tiếp.

`product_stage_recovery_source_v3` và `product_stage_handoff_v3` mở rộng journal
cho `planned_continuation` mới. Context khóa original job/request, exact answer
link và inherited plan projection; không lưu bản sao receipt của cha. Source
replay xác thực durable answer intent và pack cha, dựng lại case/context/counts
rồi đối chiếu journal. Pipeline dùng lại các input/stage cùng protocol như
phiên trả lời ban đầu; admission kiểm policy/Git, saved reads dùng recorded
bytes. Lease từ queued và global attempt sequence được giữ cả qua nhiều phiên
phục hồi lỗi. `continuation_root_budget_v1` lấy root call limit trừ inherited
calls, không routing lại bằng seed của câu trả lời mới để cấp thêm call.
Preview có `budget_scope: planned_continuation_remaining`, inherited/root counts
và cận local; các attempt lỗi mới vẫn nằm trong phần còn lại đó. Handoff tự lấy
ancestry của cả answer parent lẫn recovery parent; verifier độc lập kiểm request,
answer link, inherited counts và journal bindings. Partial answers sau recovery
tiếp tục mang debit chưa nằm trong logical counts đúng một lần.
Saved intent validation dùng SQLite `mode=ro`; chỉ worker mới dispatch mới chờ
transaction tạo intent bằng write barrier. Điều này tránh khóa lồng khi cha là
một phiên phục hồi và child tạo recovery intent mới. Admission dành một hop cho
child trong cận ancestry 11 của dossier; không tạo pack mà dossier không thể mở.

`fork_origins.prepare/attach` là logic chung cho initial run và recovery của
followup/clarification/check_followup/memory_followup/context. Case, parent pack,
exact user context/answers/observations/selection và intake được tái dựng từ
nguồn đúng chủ sở hữu. Created-at của link là immutable original job time.
`product_stage_recovery_source_v4` khóa projection này ngoài options và giữ
owned stage/attempt chain như v2/v3. Fork dùng Quick/Deep bounds 4/6 của lần
điều tra dữ kiện mới, không kế thừa lại budget qua mỗi recovery. Không cho fork
từ waiting Planned để tránh reset root budget. Admission replay Git/current
selection và policy; saved replay dùng frozen selection/journal prefix, không
đọc current memory store hoặc current repository index. Memory/check intent
validation dùng SQLite read-only; initial worker mới giữ dispatch write barrier.
Initial fork và recovery đều dự phòng child trong cận dossier ancestry 11.

`product_stage_handoff_v4` giữ original fork owner trong chain. Standalone
verifier đối chiếu original request/parent bytes, exact follow-up query/answers/
selection, local attempt bound và raw stage hashes. Selected memory attachment
của child được kiểm bằng original memory-fork identity; mọi unverified truth
flags giữ nguyên. Included-byte consistency vẫn không xác minh causal truth,
Git/AST, tác giả hoặc hóa đơn.

Không tự chuyển checkpoint legacy thành recovery journal.

`typesafe_recovery.RecoveringTypesafeController` dùng chung pipeline cho Planned
Jev v2 mới. Context khóa origin job, model Jev và policy; bốn stage
`planned_decision_1..4` giữ exact public state, allowlist, root input binding và
record với actual execution owner. Sidecar `typesafe_controller_v2` giữ logical
root cùng `decision_owners` riêng. Mọi quyết định đã lưu, kể cả durable row tail
trước checkpoint kế tiếp, được `_record` replay và không gọi lại client/ledger/
callback ghi chi phí. Typesafe v1 giữ nguyên cách đọc.

`controller_accounting.segments` đối chiếu attempt ordinal toàn chuỗi (tối đa
bốn), logical decision index, receipt và original owner. Mất response/decision
không xóa attempt. Receipt terminal thiếu decision được báo trong preview, giữ
nguyên cost/binding và chỉ nhận thử bước thiếu với consent và budget còn lại;
receipt reserved thì từ chối. Trước gọi mới, mọi receipt ở logical state đó còn
phải khớp exact annotation/evidence/request hashes. Hết cận Jev thì chặn cả
generation chắc chắn không thể đi tới quyết định tiếp theo, thay vì trả thêm
chi phí Groq rồi mới báo hết cận.

Source/handoff v5 giữ controller inventory từng owner ngoài Groq usage. Wrapper
đếm lượt thử mới trước client, không cấp lại bốn call khi đổi child; Groq vẫn
giữ route-derived 5/7/9 riêng. Provider được tạo lười theo call thực tế: critic
không load Typesafe; Jev chọn STOP không tạo Groq; export không load cả hai.
Preview không gọi provider/config/ledger, nêu scope provider có thể cần. Saved
read/duplicate/handoff không replay Git hoặc load cấu hình hiện tại.

Standalone verifier v5 đối chiếu included raw journals, mỗi decision state/
allowlist/request/response/receipt, logical plan và mọi owner/attempt, kể cả
terminal orphan receipt. Nó kiểm inventory Groq/Typesafe riêng và chặn receipt
trùng xuyên provider, không xác nhận causal truth, tác giả hay hóa đơn. Trace
giữ quyết định cũ có owner; UI chi phí phiên con chỉ lấy receipt do con tạo.

Planned phục hồi ở waiting state có thể dùng luồng trả lời
riêng của mục 5: `planned_waiting.recovery_call_debit()` chiếu phần attempts
vượt logical completed-stage calls từ recorded recovery binding. Source service
kiểm intent/prefix/tail và hợp nhất receipt ownership của mọi failed ancestor cùng
recovered parent, đối chiếu đúng attempt stages; thiếu/unknown/duplicate thì từ
chối. Không chuyển receipt cũ sang phiên mới hoặc sửa parent plan/waiting SHA.

`continuation_remaining()` trừ debit trong preview/admission; phải còn đủ ba
call cho generation, repair và critic. `_inherit()` chuyển debit vào cumulative
provider count đúng một lần; các hậu duệ kế tiếp dùng inherited counts thường.
Khi attempts vượt cận hai call cho mỗi completed generation, inherited metadata
thêm `recovery_attempt_debit` dương và carry sang các đoạn sau. Parent replay
bind đúng debit từ recovery witness; logical calls sau khi trừ debit vẫn chịu
cận generation/repair cũ và tổng calls vẫn giữ nguyên. Old v3 đã lưu trong cận
cũ giữ exact shape, không sửa plan SHA để thêm field. Xóa/giảm debit rồi reseal
không qua được parent-bound replay hoặc portable verification.
Portable verifier replay recovery witness và tính lại inherited attempt count;
giảm debit rồi rehash child không đủ để qua cổng. UI giữ saved logical counts
riêng với attempt budget toàn chuỗi; đổi ngôn ngữ giữ draft và thu hồi consent.
Nguồn/policy của answer continuation được kiểm cả trước/sau mỗi call và trước
publication; một response đã có receipt không tự trở thành completed checkpoint
khi authority thay đổi. Không có quyền chi tiêu provider mới hoặc evaluation
phát sinh từ cơ chế recovery.

## Ngân sách và bảo mật

`investigation.check_plans` tạo phần bổ sung `product_check_plan_v1` cho báo cáo
product có giả thuyết đã lưu. Nó dùng chung public source projection với câu
hỏi thích nghi, thêm report SHA; không sinh lại giả thuyết hoặc đổi report v1.
Output chỉ gồm 0–3 kiểm tra với procedure/controls/A–B/effort/citations và đúng
một dự đoán A/B/unknown cho mọi giả thuyết. Validator đóng schema, kiểm đầy đủ
refs/bounds/A–B khác nhau; `discriminating_checks.compare_proposed_checks`
chọn nhiều cặp A/B do model nêu nhất, rồi effort/thứ tự lưu. Số cặp này không
phải information gain, chất lượng ngữ nghĩa hoặc xác nhận nguyên nhân.

`check_plan_jobs.CheckPlanJobs` khóa intent SQLite theo source job + pack bytes
SHA. Kiểm authority toàn bộ lineage, report/query/annotation/evidence/snapshot;
replay Git trước provider. Mỗi call (kể cả output repair) kiểm lại request,
intent, nguồn và hủy; kiểm lại trước publish. Một call và tối đa một output
repair, không retry transport/admission hoặc thay lượt lỗi/interrupted/hủy.
Groq 120b dùng receipt/cận từng call/giá có ngày; không Jev. Lưu trong ignored
`artifacts/demo/check_plan_runs`, tách khỏi investigation packs, benchmark và
manual journal. GET/export replay nguồn và recommendation nhưng không cần
Git/config/network/model. UI Việt/Anh render cùng bundle với JSON/Markdown,
kiểm report/plan SHA khi tải. Nhật ký gốc vẫn chỉ ghi các bước của report v1.

`check_observations.CheckObservationStore` lưu journal riêng theo plan job trong
`supplemental_check_observations`, cùng SQLite private. Event khóa digest của
toàn bộ check, gồm procedure/controls/outcomes/predictions. Chỉ append, tối đa
100 event; sửa trỏ tới active event của cùng check, optimistic lock chặn ghi
trên revision cũ. A/B/other/inconclusive/not_run là tự báo, không được model
hoặc hệ thống xác minh. Commit đã thử do người dùng nhập có thể unknown/khác
commit phân tích. JSON/Markdown/UI đọc cùng journal; báo cáo nguồn không đổi.

`check_followups.CheckFollowups` nhận 1–3 active observations không phải not_run,
provider/depth rõ ràng và nguồn plan/journal hash. Intent SQLite theo digest
yêu cầu tạo đúng một child. Trước queue, provider, mọi call và export, replay
authority/lineage/checks/journal bindings; Git chỉ replay trước inference.
Trong khi chạy, journal đổi thì dừng; đọc child hoàn tất replay đúng prefix
revision đã chọn, nên đính chính mới không làm đổi query/receipt/report cũ.
Context ghi rõ model proposals và unverified user reports; lexical intake chỉ
dùng conditions/observed_result/interpretation, không lấy câu chữ model làm cue.
Child chạy pipeline chung cùng snapshot gốc, giữ receipt/trace/provenance và
không có nhãn strict, human validation, causal/fix verdict hoặc quyền GitHub
write/Jev kế thừa. Giới hạn tám thế hệ áp dụng chung các dạng follow-up.

`runtime.typesafe` dùng endpoint chính thức cố định và pin `jev-1.13.0`, không
dùng đường OpenAI-compatible hoặc credential Groq. `configs/typesafe.toml` có
enable và trần request/ngày, USD/ngày, USD/tháng riêng; mặc định tắt/trần 0,
key có sẵn không cấp quyền chi tiêu. Giá ngày 17/10/2026 từ
[Typesafe Models](https://docs.typesafe.ai/models): USD 0.042/triệu input,
output miễn phí, context mặc định 64.000/request. Vì API không có max-output
được tài liệu hóa, ledger riêng chỉ reserve cận USD cho toàn 64.000 input;
không bịa giá output hoặc giới hạn output bằng API. Request local tối đa
16 KiB/một Choice; response tối đa 64 KiB, timeout 20 giây, cấm redirect và retry.
SQLite intent unique theo job/decision được ghi trước HTTP; callback ownership
phải lưu thành công. Lỗi/missing usage giữ cận, usage vượt cận khóa ledger.
Jev receipt và chi phí được giữ riêng khi chạy/lỗi/hủy, không cộng nhầm vào
Groq ledger hoặc coi receipt là hóa đơn. Giá quá 30 ngày/thiếu key/caps thì
không gọi. Confidence/probabilities chỉ là output lựa chọn action của model,
chưa được hiệu chỉnh hoặc đánh giá cho IssueIntel. Ngày 17/10/2026, tác giả cho
phép đúng bốn call tổng hợp với tổng cận USD 0.011 cho smoke test một lần:
4/4 response/schema/receipt hợp lệ, 1.842 input token và 142 output token;
local receipt ghi nhận USD 0.000079, chưa phải hóa đơn provider. Ledger/receipt/
authorization thử giữ trong ignored artifacts; config product vẫn tắt/trần 0.
Kết quả chỉ nghiệm thu transport/schema/receipt, không đo độ chính xác điều phối,
không dùng benchmark và không cấp quyền chi tiêu thường xuyên.

`integrations.github_releases` kiểm một release/tag công khai theo yêu cầu rõ
ràng, với tối đa 16 GET và receipt JSON tổng tối đa 20 MB. Nó pin repository ID,
release ID, ref object và commit qua các lần đọc; peel annotated tag có giới hạn,
rồi so ancestry bằng REST compare giữa hai full SHA. Không dùng target_commitish
làm commit, không theo URL/pagination trả về, không tải Git history hoặc gọi LLM.
Quan sát hiện tại không phải bằng chứng tag tại ngày công bố hoặc xác nhận fix.
`investigation.release_jobs` kiểm nguồn GitHub product và lineage trước queue/
worker, giữ pack bytes/query/snapshot binding, lưu receipt trước request kế tiếp.
Kết quả private tách khỏi pack; đọc lại replay proof mà không Git/network/model.
Lỗi/hủy giữ receipt đã ghi, không retry tự động. Benchmark/follow-up bị chặn.

Product provider có callback receipt tùy chọn: ghi reservation ownership vào job
trước HTTP, cập nhật trạng thái sau reconcile (kể cả lỗi/usage breach). Ghi receipt
không thành công thì không gửi HTTP. `investigation.usage` chỉ cộng receipt gắn
về đúng job, không suy từ chênh lệch tổng ledger; USD dùng integer micro-USD và
Decimal. Phiên cũ thiếu tracking giữ unknown; interrupted có thể giữ trạng thái
receipt cuối cùng trước crash. API/lịch sử/UI đọc lại không gọi model. Số liệu
charged-or-reserved khác hóa đơn provider; failed/missing usage giữ cận, breach
báo riêng. Report chi phí không thay pack/benchmark receipts hoặc quyền chi.

Reservation token/cost trước HTTP được ghi trong SQLite transaction `BEGIN IMMEDIATE`, chống các call đồng thời vượt tổng khi có cap. Tác giả đã cho phép bỏ token/USD cap cho công việc dựng lại cần thiết; explicit flags phải khớp author decision, không suy ra unlimited từ cấu hình thiếu. Usage hợp lệ mới giảm charge từ cận trên; call lỗi/usage thiếu giữ cận. Usage vượt bound hoặc bất nhất khóa ledger để review. Ledger không chứa prompt/secret. Giá có ngày và nguồn, decimal/round-up, giá cũ quá 30 ngày chặn.

Authorization không đi qua redirect; chỉ endpoint Groq cố định. Không retry ngầm, built-in tool hoặc vòng server không kiểm soát. CI chỉ dùng synthetic fixture/mocked transport. PostgreSQL DSN cũ trong .env không được dùng. Artifact/data/secrets đều ignored; demo và review chỉ dùng cục bộ.
