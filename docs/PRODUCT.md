# Sản phẩm theo proposal.md

Luồng sản phẩm gọi `investigation.pipeline.investigate()` dùng chung với benchmark:
issue → snapshot Git chỉ đọc → AST graph → evidence → competing hypotheses →
verifier → investigation pack. `investigation.workbench` quản lý job/lịch sử;
`interfaces.web` chỉ cung cấp HTTP và giao diện localhost.

## Chạy thử

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,data,llm]"
.\.venv\Scripts\python.exe -m issueintel serve
```

Mở `http://127.0.0.1:8787`. **Thử ví dụ tổng hợp** tạo repository Python nhỏ trong
`artifacts/demo/`, đọc Git objects và xuất evidence; không tải benchmark hay gọi
API. Với issue tự nhập, cần repository local hoặc `owner/repo` và revision rõ ràng.
Đầu vào này dùng `benchmark_base`, không nhận nhãn strict lịch sử.

Main Dev chỉ liệt kê accepted thuộc 400 ca gốc từ cohort bất biến và checkpoint
phục hồi hash-bound. Trước khi chạy mỗi ca, loader replay eligibility, query ban
đầu, nguồn, proof và snapshot. Nó không đọc gold, human rationale, Holdout hay
Live. Các ca recovered không được biểu diễn là cohort mới đã freeze.

Groq là tùy chọn do người dùng bật. Nó dùng `openai/gpt-oss-120b`, structured
output, generation và critique riêng, giá có ngày, giới hạn từng call và durable
budget ledger hiện có. Đây là lời gọi API thật; dữ liệu được gửi là query và code
excerpts. Không tự chạy LLM khi mở trang, mở lịch sử hoặc xuất báo cáo. Failure/
restart không tự retry call đã bắt đầu. Hủy được kiểm trước/sau call; call đang
ở provider có thể vẫn tính phí.

## Phạm vi chức năng

Danh sách dưới đây đối chiếu F01–F20 của proposal; không coi roadmap là kết quả
đã hoàn thành.

| Mã | Trạng thái hiện tại |
|---|---|
| F01 | Nhận issue/repo/revision; intake gợi ý bug/feature/question/unknown bằng lexical cues, nguồn dòng/hash và thông tin cần xem lại; không coi cue là phân loại đã xác minh. |
| F02 | Tìm ứng viên local và synced issue cùng repo; preview/consent riêng để đối chiếu symptoms/mechanism/version/differences bằng Groq. Kết quả related/possible_duplicate/insufficient giữ raw judgment, assessment tái tính và exact side-scoped sources; không cần hypotheses có sẵn, chưa xác nhận duplicate hoặc tự ghi GitHub. Phạm vi/kiểm thử mục 7 bên dưới. |
| F03 | Định vị file/symbol/dòng qua lexical, identifier và typed graph retrieval ở đúng snapshot. |
| F04 | Repo đã kết nối tự truy xuất tối đa 6 nguồn; người dùng cũng có thể tìm/chọn chính xác nguồn đã đồng bộ để tái điều tra cùng query/commit. Record, receipt và repository ID được replay; dùng trong generation/critique/export. Current context không phải temporal proof. |
| F05 | Job/trace/checklist có phiên bản; nhật ký quan sát theo từng saved check, điều kiện/mong đợi/quan sát và commit tự báo, đính chính append-only cùng nguồn. Deep thêm một vòng; Planned có typed gap actions. Web rules v3 có persisted waiting và explicit continuation giữ budget toàn chuỗi, đã nghiệm thu ở mục 5. |
| F06 | Adapter Typesafe nối tùy chọn vào Planned: bounded public context, choice allowlist, tối đa bốn quyết định, cấu hình/ledger/receipt riêng, fallback được ghi và replay. Đã kiểm kết nối thật 4/4 call tổng hợp ngày 17/10/2026 theo quyền thử riêng; chưa đánh giá chất lượng controller hoặc luồng GitHub thật. Cấu hình thường vẫn tắt/trần 0. |
| F07 | Generation 3–5 hypotheses, support/counter IDs; đồ thị và bảng đối chiếu từng cặp từ báo cáo lưu sẵn. Product có kế hoạch bổ sung 0–3 kiểm tra phân biệt, điều kiện kiểm soát, kết quả A/B và dự đoán A/B/unknown cho từng giả thuyết. Gợi ý theo số cặp A/B do model nêu và công sức ước tính; chưa đo chất lượng phân biệt hoặc suy ra nguyên nhân. |
| F08 | Kiểm citation/hash, tìm thêm code ở Deep/Planned, critique riêng và abstention. Claim-review sidecar có exact Unicode spans, assumptions/counter và rubric sufficiency tái tính ở mục 3; vẫn giữ riêng nhận xét model, citation validity và causal/semantic verification chưa có. Planned dừng rõ khi query/evidence không đổi; counter có thể `not_found`. |
| F09 | Câu hỏi intake theo mẫu và câu hỏi riêng từ issue/code/giả thuyết đã lưu; Việt/Anh, có lý do hỏi/cách dùng câu trả lời/refs. Nhận trả lời một phần/“chưa biết”, khóa và replay nguồn/Q&A. Planned Web rules v3 có câu hỏi trong trạng thái chờ, continuation cùng commit không reset budget; generic follow-up bị chặn khi đang chờ. Tạo câu hỏi thích nghi qua một paid attempt được duyệt riêng; chất lượng phân biệt và dữ kiện mới vẫn unverified. |
| F10 | Durable job/artifact receipts, plan ledger và repository page checkpoints; product có verifier/export recovery riêng. Original Workbench/durable CLI Quick/Deep/Planned rules và Jev, answer continuations/follow-up forks mới và chuỗi stage-resume có preview/consent, owned prefix/lease, inherited attempt bounds, lineage và portable handoff. Mỗi nguồn giữ một intent; replay quyết định Jev giữ actual owner và cận bốn lượt riêng với Groq, không retry API ngầm. Phạm vi và nghiệm thu ở mục 8 bên dưới. |
| F11 | Cache Git/snapshot/graph, follow-up lineage, saved-run retrieval; kết nối tự backfill/tiếp tục, webhook cập nhật canonical và tracked branch. Repository memory mục 6 giữ nguồn/revision, dependency invalidation và bản cũ bất biến; historical admission cần đầy đủ proof trước cutoff, không coi current context là proof thời gian. |
| F12 | Quick lấy evidence gọn; Deep thêm tối đa một lượt. Planned chọn một đến ba vòng theo dấu hiệu file/gap/counter còn thiếu từ seed; tối đa chín Groq calls kể cả repair. Web/CLI durable rules v3 kế thừa root route/limits và counts qua continuation; CLI thường/Jev giữ v2, library mặc định v1. Đây là heuristic quan sát, chưa phải đánh giá độ khó/chất lượng. Routing benchmark giữ policy/pilot riêng. |
| F13 | Trạng thái/hủy, token và USD theo phiên từ receipt riêng; đang chạy/lỗi/interrupted vẫn xem được cận giữ chỗ. Mục 9 có projection current/lineage riêng từng provider, known/complete/settled tách biệt, JSON/Markdown và bàn giao. Mục 13 thêm readiness local theo chức năng, trạng thái sync/cache và metadata dung lượng/cleanup preview có cận, không có thao tác xóa. |
| F14 | Lịch sử, mở lại pack và so sánh hai phiên cùng issue/repo: query/snapshot/proof bindings, evidence và hypotheses; điều tra tiếp với input mới vẫn tạo job riêng. |
| F15 | Issue URL/collaborator command; HMAC-signed installation/repository events tự onboarding public repos và incremental sync có durable inbox/dedup. App/HTTPS relay thật chưa cấu hình trên máy. |
| F16 | Preview comment có code links; exact-body approval, target binding và durable one-attempt intent trước POST. Không tự đăng. |
| F17 | Tổng quan và JSON/Markdown/nháp GitHub dùng chung report contract; có Markdown bàn giao kèm mọi quan sát/đính chính thủ công và JSON journal riêng. Khóa nguồn và version; phân biệt model critique, citation, user report và kiểm người. Đồ thị có lọc/keyboard/citation; không tự đưa journal vào nháp GitHub hoặc model. |
| F18 | Reservation trước HTTP gắn về job, cập nhật usage/price date sau call; tab chi phí/export riêng, không nhận tổng ledger thành chi phí phiên. Mục 9 bổ sung dedup immutable owner/provider/reservation, attempt inventory và source-bound lineage; legacy/receipt thiếu hoặc conflict giữ unknown, không phải invoice. Phạm vi và kiểm thử bên dưới. |
| F19 | HTTP API, một worker, hàng đợi tối đa 4; product batch mục 11 có frozen preview/manifest, durable child intents, concurrency 1 và partial success/pause/cancel/explicit continue. Benchmark CLI có concurrency bounded riêng. |
| F20 | Đối chiếu commit/tag, diff evidence và ancestry local; mục 12 đối chiếu từng cited code excerpt với scope/witness/limits và giữ nguồn gốc bất biến. Kiểm riêng một GitHub Release công khai theo tag với commit tham chiếu, giữ ID/receipt/hash và phân biệt draft/prerelease. Những quan sát này không chứng minh nội dung tag lúc công bố hoặc lỗi đã sửa. |

Kiểm tra phân biệt giả thuyết đã được nối vào tab **Kế hoạch kiểm tra** cho
điều tra product có giả thuyết lưu sẵn. Người dùng duyệt gửi nguồn tới Groq;
mỗi nguồn có một tác vụ bền vững, tối đa hai call kể cả một lần sửa output.
Kế hoạch là phần bổ sung riêng gắn pack/report/query/snapshot/annotation bằng
hash; JSON/Markdown và UI đọc cùng nội dung, mở lại không gọi API hoặc Git.
Report v1, pack và nhật ký cũ giữ nguyên. Kế hoạch bổ sung có nhật ký riêng,
ghi điều kiện/kết quả/diễn giải/commit tự báo và đính chính append-only.
Chọn tối đa ba quan sát đang hiệu lực để chủ động tạo child investigation,
offline mặc định hoặc bật Groq riêng. Nguồn journal revision/hash, selected
events và toàn bộ check/predictions được khóa; later correction không viết lại
child cũ. Phiên con truy xuất lại code cùng commit phân tích; commit tự báo
được giữ riêng, không dùng để chuyển snapshot. Không tự đánh giá quan sát là
đúng hoặc kết luận giả thuyết được hỗ trợ/phản bác.
Main Dev/strict/nguồn có lineage benchmark không được dùng tính năng này.

Lõi CLI diagnostic dùng chung quy tắc chọn theo asserted separation.
DEV diagnostic 25 ca so hai prompt policy trong cùng harness/model/tool/budget;
không phải benchmark toàn bộ workbench hoặc sản phẩm bên ngoài. Các artifact
thử nghiệm không được nhập thành kết quả product. Có file Recall@5
proxy/cost/latency và CSV chờ review ngữ nghĩa; kết quả thực tế chỉ ở artifacts
private, không coi test tổng hợp hoặc số cặp dự đoán là chất lượng điều tra.

Đồ thị mặc định tập trung vào một giả thuyết và các cạnh trực tiếp; có tổng quan,
thẻ nguồn chữ thật, màu/nét theo loại quan hệ và cửa sổ phóng rộng với inspector.
Escape trả về báo cáo, chọn nút/đổi chế độ không gọi lại API hoặc LLM. Trên màn
hình hẹp chỉ vùng bản đồ cuộn ngang; nội dung và controls vẫn ở trong viewport.

## API và dữ liệu local

Tab **Tổng quan / Overview** là màn đầu khi mở báo cáo, gồm trích issue được ghi
rõ là lời người báo, giả thuyết cạnh tranh, bước kiểm tra chưa chạy và vị trí code
ứng viên. Mở rộng để xem nhận xét model, giới hạn và hashes nguồn. Citation dẫn
về evidence của đúng phiên; link GitHub pin đúng commit/dòng. Không tự sinh phần
tóm tắt mới hoặc điểm tin cậy. Các nút **Markdown**, **Report JSON** và **Pack JSON**
phân biệt bản trình bày với nguồn máy đọc gốc. UI đổi Việt/Anh; nội dung nguồn và
export giữ nguyên ngôn ngữ. Pack cũ đọc qua projection mới, không bị viết lại.

Giao diện hỗ trợ **Tiếng Việt / English** và **Light / Dark / System** từ thanh
đầu trang. Theo hệ thống phản ứng với đổi theme của OS; lựa chọn rõ ràng giữ
nguyên. Preferences được lưu bằng `issueintel.preferences.v1` trong browser,
chỉ gồm language/theme, không chứa issue, token hoặc kết quả. Storage bị chặn
vẫn dùng được. Đổi ngôn ngữ render lại dữ liệu đã có, không gửi lại job/call.
Issue/code/model output và JSON/Markdown export giữ nguyên nguồn và hash;
không tự dịch evidence hay thay prompt. Ngôn ngữ mặc định theo browser, fallback
English. Chuyển ngôn ngữ khi đang xem draft GitHub bỏ phần duyệt cũ; cần xem
và duyệt lại nội dung trước khi đăng.

Màu dùng semantic tokens chung giữa hai theme, có focus bàn phím và hỗ trợ
reduced motion. Đã đo 22 cặp màu text/control: text thấp nhất 5.43:1, control/
focus thấp nhất 3.83:1; helper light trước sửa `#778780` trên trắng chưa đạt
4.5:1. Đây là kiểm palette và các trạng thái được thử, chưa phải audit WCAG đầy
đủ. Ngưỡng đối chiếu [text contrast](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html)
và [non-text contrast](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html).

API cần session token của trang trong `X-IssueIntel-Token`. Origin/Host phải đúng
localhost; server không cung cấp CORS hay duyệt filesystem. Source excerpts được
render bằng `textContent`, không thực thi HTML/Markdown từ repository hoặc issue.

- `GET /api/readiness`: local runtime/configuration/prices/sync/cache và 17 nhóm chức năng; key chỉ có cờ hiện diện, không đọc `.env` hoặc gọi provider/benchmark/ledger. Không xác minh live auth, ngân sách call tiếp theo, App/HTTPS hoặc source/job đang chọn.
- `POST /api/storage/preview`: body đúng `{}`; metadata-only bounded scan dưới workspace cố định, không nhận root/path/limit/delete override. Partial measurement và protected/candidate scope được báo rõ; không có endpoint xóa.
- `GET /api/catalog`: metadata accepted Main Dev; không chứa gold.
- `GET /api/jobs/{id}/check-journal.json` hoặc `.md`: journal source-bound hoặc toàn report kèm journal; hai header `X-IssueIntel-Report-SHA256` và `X-IssueIntel-Journal-SHA256` bind đúng body. Chỉ product ngoài benchmark và lineage đã replay; không network/model.
- `POST /api/jobs/{id}/check-journal`: `{expected_sha256, observation}` gồm `check_id`, `outcome`, `conditions`, `expected_result`, `observed_result`, `interpretation`, `tested_commit_sha` nullable và `supersedes` nullable. `not_run` cần lý do, cấm observed result/tested SHA; các outcome khác cần điều kiện, mong đợi và quan sát. Đính chính cùng check còn active, tối đa 100 event; xung đột trả input error, tải lại để giữ bản nháp và xem phiên bản mới.
- `POST /api/investigations`: tạo job với `mode`, `provider`, `depth` và input.
- `POST /api/comparisons`: `before_id`, `after_id` của hai job đã hoàn tất cùng issue/repository; kiểm hash cả hai export, không gọi model.
- `POST /api/versions`: `job_id`, `revision`, tùy chọn `reference_revision` và `source_pack_sha256`; UI gửi exact pack SHA đã xem, caller cũ thiếu SHA được server khóa nguồn hiện tại trước queue. Job đối chiếu code/excerpt và ancestry dùng chung queue/worker, không model hoặc thực thi code/test.
- `GET /api/jobs/<source>/versions`: tối đa 20 lượt đối chiếu của báo cáo nguồn.
- `GET /api/jobs/<comparison>/versions.json`: export riêng có hash và binding về pack nguồn.
- `GET /api/jobs/<comparison>/evidence-impact.json` hoặc `.md`: đối chiếu excerpt đã lưu, hai header `X-IssueIntel-Source-Pack-SHA256` và `X-IssueIntel-Evidence-Impact-SHA256`; legacy không có excerpt comparison trả 404, không tự đọc Git/tạo kết quả mới.
- `POST /api/releases`: `job_id`, `tag` và `reference_commit` full SHA; kiểm đúng một release của nguồn GitHub product, không gọi model/tải Git history.
- `GET /api/jobs/<source>/releases`: tối đa 20 lượt kiểm release đã lưu, kể cả lỗi/hủy.
- `GET /api/jobs/<check>/releases.json`: export quan sát có receipt và binding về source pack/query/snapshot; replay không network/model.

- `POST /api/related`: `job_id` đã hoàn tất; tìm ứng viên từ các job local, không nhận scope/split/query override.
- `POST /api/issue-relation-candidates`: `source_job_id`, `source_pack_sha256`, tùy chọn `query` tối đa 2.000 ký tự; tìm saved product runs và synced issue cùng repository, không gọi model. Điểm tìm kiếm không phải xác suất quan hệ.
- `POST /api/issue-relations/preview`: `source_job_id`, `source_pack_sha256`, `candidate` với selector exact của kết quả tìm kiếm; trả hai nguồn đã khóa, `comparison_source_sha256`, `policy_sha256`, cận hai call và confirmation, không provider call.
- `POST /api/issue-relations`: đúng source/candidate, `comparison_source_sha256`, `policy_sha256` và `confirmation: compare_issue_pair_with_api_cost`. Một intent cho exact cặp/nguồn/policy qua restart; không chấp nhận scope/repo/model override.
- `GET /api/jobs/<source>/issue-relations`: các tác vụ đối chiếu của đúng source pack, kể cả chưa hoàn tất.
- `GET /api/jobs/<comparison>/issue-relation.json` hoặc `.md`: export cùng sidecar đã replay, hai header `X-IssueIntel-Source-Pack-SHA256` và `X-IssueIntel-Issue-Relation-SHA256`; không Git/index mới/config/provider.
- `POST /api/context-search`: `job_id`, `query`; tìm tối đa 20 nguồn trong index của đúng repo GitHub của báo cáo, không network/model.
- `POST /api/investigations` với `mode: context`: `parent_job_id`, `parent_pack_sha256`, `selected_sources` (1–6 bộ key/record_sha256/receipt_sha256), `provider`, `depth`; giữ query/snapshot nguồn, không nhận repo/revision/query override.
- `GET /api/jobs`, `GET /api/jobs/<id>`: lịch sử/trạng thái.
- `GET /api/jobs/<id>/intake`: cue trong query có dòng/hash, áp dụng cả pack cũ mà không sửa export gốc.
- `GET /api/jobs/<id>/evidence-graph`: đồ thị từ citation đã replay, hash-bound về pack; không gọi model hoặc đọc nguồn mới.
- `GET /api/jobs/<id>/usage`: metadata receipt theo phiên, bao gồm job lỗi/interrupted; token/USD charged-or-reserved, không phải hóa đơn provider.
- `GET /api/jobs/<id>/investigation-usage`: `investigation_usage_v1` từ một snapshot accounting local chỉ đọc, gồm owners/current/lineage và gaps; không thay `/usage` hoặc `job_usage_v1`.
- `GET /api/jobs/<id>/investigation-usage.json`, `.../investigation-usage.md`: export cùng projection; header `X-IssueIntel-Accounting-SHA256` và `X-IssueIntel-Source-Pack-SHA256` khi có pack binding. Nếu accounting nguồn đổi trong lúc tải thì phải làm mới, không ghép tổng từ hai capture.
- `GET/POST /api/jobs/<id>/plan`: checklist theo pack; ghi cần expected SHA256, step ID, status và note.
- `POST /api/jobs/<id>/cancel`: yêu cầu hủy với JSON `{}`.
- `GET /api/jobs/<id>/pack.json`, `.../report.md`: export kiểm hash.
- `GET /api/jobs/<id>/investigation-report.json`, `.../investigation-report.md`: report v1 dựng từ pack đã replay, có raw-pack/query/snapshot SHA256; không model/Git/network.
- `GET /api/investigation-report-schema`: JSON Schema 2020-12 của report v1; cùng session/origin guard như API đọc khác.
- `GET /api/jobs/<source-id>/check-plans`: khả năng sử dụng và tác vụ kế hoạch bổ sung của đúng pack nguồn; không gọi model.
- `POST /api/check-plans`: `source_job_id`, `source_pack_sha256`, `confirmation: generate_check_plan_with_api_cost`; một intent cho mỗi nguồn, gọi Groq với receipt và giới hạn từng call.
- `GET /api/jobs/<plan-id>/check-plan.json`, `.../check-plan.md`: cùng kế hoạch đã validate; header `X-IssueIntel-Report-SHA256` và `X-IssueIntel-Check-Plan-SHA256` khóa export về nguồn đang xem. Đây là phần bổ sung riêng, không phải pack/report mới.
- `GET /api/jobs/<plan-id>/check-observations.json`, `.../check-observations.md`: nhật ký riêng của plan; Markdown giữ toàn bộ kế hoạch và lịch sử đính chính, header thêm `X-IssueIntel-Check-Observations-SHA256`.
- `POST /api/jobs/<plan-id>/check-observations`: `expected_sha256` và `observation` để append với optimistic lock; không gọi model.
- `POST /api/check-followups`: exact `plan_job_id`, `plan_sha256`, `journal_sha256`, `observation_ids` (1–3), `provider` (`none`/`groq`), `depth` (`quick`/`deep`). Một durable intent cho mỗi yêu cầu; chỉ nhận quan sát đang hiệu lực và không phải `not_run`.
- `GET /api/jobs/<id>/stages/<stage>`: tải nguyên bytes checkpoint đã ghi, kể cả job thất bại; kiểm path/hash và session.
- `GET /api/jobs/<id>/finalization`: preview khả năng kết xuất từ generation/verifier đã hoàn tất; không gọi model, đọc Git hoặc cấu hình API.
- `POST /api/jobs/<id>/finalization`: `verification_input_sha256`, `verification_sha256`, `confirmation: finalize_saved_checkpoints_without_api`; một intent bền vững cho nguồn, tạo job `provider: none` và `depth: export`.
- `GET /api/jobs/<id>/stage-recovery`: explicit preview của stopped original product journal, answer continuation/follow-up fork mới hoặc stage-resume descendant; replay source/prefix với zero provider calls, trả completed/next stage, inherited attempt bound, binding SHA, `requires_api`, root job ID và source job IDs. Answer recovery có thêm inherited/root call counts và scope phần ngân sách còn lại.
- `POST /api/jobs/<id>/stage-recovery`: exact `{binding_sha256, confirmation}` với `resume_product_stages_with_api_cost` hoặc `resume_product_stages_without_api` theo preview. Một durable intent/source, trả cùng job `stage_resume` nếu gửi lặp; giữ queue/session/Origin guards, không cấp retry mới.
- `POST /api/jobs/<id>/github-preview`: bản nháp comment từ job GitHub đã hoàn tất.
- `POST /api/jobs/<id>/github-publish`: body SHA256 và xác nhận cụ thể từ người dùng.
- `POST /api/github/webhook`: GitHub signature/event/delivery guards riêng; không tự gọi model hay đăng comment.

Đối chiếu mới lưu thêm `git_reachability`: pin source/target/reference về SHA
commit, kiểm ancestor hai chiều và kiểm reference có trong target. `contained`
chỉ nói về lịch sử Git; chưa chứng minh đúng bản sửa, patch tương đương, ngày
phát hành hoặc bản phát hành công khai. Tag được chụp từ refs local, tối đa 200
tag nhận kiểm containment, có số ngoài phạm vi/không kiểm được. Lịch sử shallow,
altered, thiếu object hoặc hết thời gian giữ unknown khi không đủ bằng chứng;
đường ancestor tìm thấy vẫn có thể chứng minh containment trong cache shallow.

Với input remote gốc, chỉ target và reference người dùng nhập được fetch theo
cơ chế acquire giới hạn hiện có; không tải toàn bộ tags hoặc deepen lịch sử.
Phần kiểm ancestry chỉ đọc local, không lazy-fetch. Git có cận 5 giây/lệnh,
60 giây tổng và 2 MiB output/lệnh. Export giữ inventory/ref hash lúc kiểm;
mở lại không chạy Git hoặc làm tag hiện tại thay đổi kết quả đã lưu. Báo cáo cũ
thiếu mục này vẫn mở được và không được suy thêm quan hệ ancestry.

Mục 12 thêm `product_evidence_impact_v1` cho code excerpts, gắn source job/pack,
query và hai commit/snapshot. Sáu trạng thái `unchanged/moved/changed/missing/
unavailable/ambiguous` có quy tắc tái tính từ captured observations. Khớp nguyên
văn được giữ cùng locator/hash; `moved` không phải Git rename hoặc ánh xạ ngữ
nghĩa. Không thấy chỉ áp dụng admitted scope; file skip/tìm vượt cận giữ unknown,
nhiều vị trí không chọn vị trí đầu. Export không chứa full target text inventory:
completeness/absence là khai báo quan sát local, không phải proof replay độc lập.
Validator kiểm kiểu JSON, source text/IDs/hypothesis refs, span/hash, counts,
cận tìm và tính nhất quán path/file/inventory; rehash không hợp thức hóa metadata
tự mâu thuẫn. UI có filter, nguồn/đích riêng, giới hạn và JSON/Markdown; report
và citation gốc giữ nguyên. Nguồn product qua shared ancestry authority cho cả
follow-up/recovery; không nới quyền context/release hoặc benchmark/strict.

Mọi job/pack và repository ví dụ ở ignored `artifacts/demo/`. Không commit data,
token, review bundle hoặc kết quả thật. CI dùng fixture tổng hợp. Evaluation,
metrics và thí nghiệm Holdout vẫn cần capability/ủy quyền riêng theo proposal.

## Chọn nguồn cho điều tra

Mục **Chọn nguồn cho điều tra** chỉ áp dụng báo cáo GitHub product
ngoài benchmark/strict và follow-up nhập văn bản. Tìm local không gọi model;
preview dùng đúng excerpt mà inference sẽ nhận, tối đa 1.200 ký tự/nguồn. Danh
sách lựa chọn không tự thêm nguồn khác. Cả record/receipt/global repo ID phải
khớp trước queue và trước provider; repo gốc được replay ở commit đã lưu, không
fetch lại HEAD hoặc mô tả issue hiện tại. Groq phải bật riêng và consent được xóa
khi đổi nguồn/phiên/ngôn ngữ. Không có retry sau lỗi nhận job.

`context_selection` khóa parent pack/context SHA, thứ tự nguồn và độ sâu tối đa
tám thế hệ, dùng chung giới hạn với follow-up. Toàn bộ proof nguồn được lưu
private, nên mở lại không phụ thuộc index đang thay đổi. Phiên mới không kế thừa
quyền đăng comment hoặc verifier resume của phiên cha; context vẫn unverified.
JSON, Markdown và Trace có provenance; giữ nguyên mọi byte/receipt của phiên cha.

## So sánh hai phiên

Phần dưới lịch sử cho chọn phiên trước/sau. So sánh là mô tả các đầu ra đã lưu,
không phải evaluation, điểm chất lượng hoặc xác nhận bản sửa. Query, commit,
snapshot, protocol, cutoff, availability và query provenance được báo riêng.
Khi một binding thay đổi, excerpt giống nhau vẫn cần gắn lại citation; hypotheses
không được coi giữ nguyên qua context mới. Không copy evidence sang job khác.

Evidence đối chiếu theo loại và locator, kèm hash/revision của từng phía. Đổi
range dòng được báo thêm/bỏ; chưa có semantic line alignment. Hypotheses giữ
nguyên cần cùng nội dung, bước kiểm, support/counter nguồn và context; đổi ID
cục bộ hoặc thứ tự không tự tạo khác biệt. Đầu vào local có ID phụ thuộc request,
nên chỉ ghép khi repository và query giống hệt. GitHub/Main Dev dùng identity
issue cố định và báo riêng khi query đổi. File hỏng, citation thiếu hoặc nguồn
khác snapshot bị từ chối. Kết quả giữ private trên máy, không đọc Holdout/gold.

## Intake và dữ kiện tiếp nối

Tab Intake/Thông tin đầu vào chỉ trích lexical cues trong query: hành vi thực tế,
mong đợi, cách tái hiện, môi trường/phiên bản. Các tín hiệu intent mâu thuẫn hoặc
không có giữ `unknown`. `mentioned` không xác nhận đủ thông tin; `not_found`
không chứng minh thông tin vắng mặt. Nguồn dài có hash cho dòng đầy đủ và excerpt
riêng, kèm dấu truncated. Không gọi model, tra gold hoặc gán nhãn người.

Mục **Trả lời câu hỏi gợi ý** dùng template v1 Việt/Anh cho những cue chưa tìm
thấy, tối đa bốn câu. Có thể trả lời một phần hoặc “chưa biết”. `mode=clarification`
nhận `parent_job_id`, `parent_pack_sha256`, `questions_sha256`, `language` và
`answers` (ID câu hỏi + nguyên văn câu trả lời, tối đa 4.000 ký tự/câu). Server
kiểm nguồn trước queue và worker; replay snapshot/excerpts trước provider. Chỉ
product được dùng, kể cả kiểm nguồn gốc xuyên các phiên tiếp nối; không benchmark.
Groq bật riêng cho lần gửi, không được kế thừa khi đổi nội dung/ngôn ngữ/nguồn.
Nguyên văn Q/A đã lưu xem ở tab Intake và có trong JSON/Markdown. Query mới lấy
code lại ở cùng SHA, không tự mang nguồn repository đã chọn của query trước.
Để câu chữ trong câu hỏi không tạo cue giả, intake của follow-up dùng projection
nội dung người dùng (query gốc + câu trả lời, bỏ câu hỏi/wrapper), với text/hash và
nguồn dòng riêng. Query SHA vẫn khóa toàn bộ đầu vào. Luồng theo mẫu này không
đặt câu hỏi phân biệt giả thuyết; phần model bên dưới đề xuất loại câu hỏi đó.

**Câu hỏi theo giả thuyết và code** nhận báo cáo product đã có 2–5 hypotheses;
mỗi lượt tạo 1–4 câu hỏi riêng qua `openai/gpt-oss-120b`, dùng issue, public code
excerpts và hypotheses của đúng báo cáo. Mỗi câu có Việt/Anh, mục đích, cách dùng
các câu trả lời khác nhau, ít nhất hai hypothesis refs và một code citation.
Phải duyệt việc gửi dữ liệu/chi phí; provider đi qua ledger/per-call bounds hiện
có, một call không tự sửa schema/thử lại. Gửi lặp cùng source nhận lại job cũ,
kể cả job lỗi; muốn attempt mới cần tạo báo cáo điều tra nguồn mới. Mở lịch sử,
đổi ngôn ngữ, export hoặc đọc bundle không gọi model. Câu hỏi/IDs đúng schema
không chứng minh hữu ích hoặc đúng về ngữ nghĩa.

API: `POST /api/adaptive-questions` nhận source job ID, pack SHA và confirmation
`generate_questions_with_api_cost`. `GET /api/jobs/<source>/adaptive-questions`
liệt kê local attempts; `GET /api/jobs/<question-job>/questions.json` replay/xuất
bundle. Trả lời dùng `mode=clarification` cùng `question_job_id`, parent hash và
bundle hash. Giữ nguyên hỏi đáp, mục đích, refs và language; câu hỏi không đi vào
lexical cue projection. Context mới chưa xác minh, không mang quyền write/resume,
không đọc benchmark/Holdout hoặc thay các receipts gốc.

Form bổ sung tạo `mode=followup` với parent job ID và context tối đa 20.000 ký tự.
Repository/commit lấy từ parent đã kiểm hash; client không được ghi đè. Pack cha
giữ nguyên, pack mới có parent pack SHA256, context SHA256, thời điểm và depth.
Mỗi lần mở/so sánh/tiếp nối replay mọi parent, giới hạn tám thế hệ và query tổng
100.000 ký tự. Parent bị hỏng hoặc lineage có vòng bị từ chối trước provider.

Mọi follow-up dùng `benchmark_base`, có case ID `followup:` riêng và context chưa
xác minh. Bổ sung vào Main Dev không thay cohort/query/proof gốc hoặc trở thành
một ca strict mới. Không tự gọi Groq; người dùng bật riêng ở form follow-up nếu
cần. Không kế thừa quyền đăng GitHub của job cha: follow-up không tạo/publish
comment. So sánh với parent nhận identity từ chuỗi nguồn đã replay và báo query/
context đổi, không tái sử dụng citation qua context mới.

## Issue liên quan trong lịch sử

Tab Related issues/Issue liên quan tìm thủ công trong tối đa 50 job local gần
nhất, chỉ cùng repository đã khai báo. Parent gốc của follow-up có thể cũ hơn cửa
sổ 50 job: lineage được replay và mở về query nguồn. Không đọc dataset/holdout,
gọi GitHub, embeddings hoặc LLM; không đưa kết quả vào inference hoặc pack strict.

Mỗi issue nguồn chỉ xuất hiện một lần; loại chính issue đang xem và follow-up của
nó. Với input local, query gốc + repository xác định một issue thay vì ID phụ
thuộc provider/depth. Nguồn hỏng bị bỏ và báo số lượng. Patch/test/rationale/review bundle/gold
không được dùng để tính tương đồng. Query trong pack local có hash kiểm lại.

Ranking BM25 dùng token Unicode, loại stopwords và cần ít nhất hai token chung.
Score không phải xác suất. Mỗi kết quả có matched terms, đoạn nguồn với hash,
query/pack SHA256 và nút mở báo cáo. Quan hệ luôn `candidate_unverified`, không
coi giống triệu chứng là duplicate. Chưa xác minh nguồn này có trước cutoff của
issue đích. Search cũ vẫn là tìm kiếm; model comparison bên dưới là thao tác
riêng, không được suy từ điểm BM25 hoặc tự chạy khi mở danh sách.

### Mục 7 — P1: giải thích quan hệ giữa hai issue

Trong tab Issue liên quan, chọn một saved product run hoặc synced **issue** từ
đúng repository, xem preview rồi duyệt chi phí Groq. Product run offline chưa có
hypotheses vẫn dùng được; PR/comment không được làm issue ứng viên của luồng này.
Server replay cả hai authority/pack hoặc record/receipt, kiểm repository identity
thay vì tin tên path alias. Self/same issue, local query trùng nhưng không có
identity phân biệt, benchmark/strict và nguồn khác repo bị từ chối. Hai issue
GitHub có URL khác nhau vẫn được đối chiếu khi title/body giống hệt nhau.

`product_issue_relation_v1` giữ triệu chứng, cơ chế, phiên bản và khác biệt, kèm
support/counter citations đúng phía, source ID, revision và span Unicode. Nhận xét
model giữ riêng với sufficiency assessment tái tính. Cùng error text không tự
thành shared mechanism. Counter chưa xử lý, giả định cần thiết chưa được hỗ trợ
hoặc material differences chặn nâng lên `possible_duplicate`. Affected version
chưa rõ được giữ thành unknown/gap để hỏi thêm, không tự phủ nhận một ứng viên
có cơ chế được dẫn nguồn cả hai phía. Commit snapshot không thành phiên bản
ảnh hưởng, release hoặc bằng chứng fix. Mọi kết quả vẫn unverified; không có
confirmed duplicate, causal verification, nhãn người hoặc auto close/comment.

Mỗi cặp/nguồn/policy có một durable intent, tối đa hai call gồm một output repair,
và tối đa 20 intent cho mỗi source pack. Worker kiểm snapshot trước provider và
nguồn/policy/cancel trước/sau mỗi call; receipt/attempt giữ owner. Lỗi/hủy/restart
không cấp lượt trả phí thay thế. Input đã khóa giữ exact pack bytes hoặc synced
record/receipt; đồng bộ index sau đó không đổi bản cũ. Đọc/xuất JSON/Markdown
không cần Git, index hiện tại, API config hay model; local pack/authority bị sửa
vẫn không được chấp nhận. Model input không mang machine path/job owner/pack
digest không cần thiết.

Dossier giữ comparison như sidecar của source run. ZIP bổ sung frozen candidate
inputs dù issue đó nằm ngoài các phiên được chọn; preview phải tính cả dữ liệu
này. Standalone verifier kiểm included bytes, hai projection, quotes, cận attempts
khai báo và assessment gates; không replay toàn bộ candidate Git/ancestry hoặc chứng nhận
tác giả/provider/semantic correctness. Pack/report nguồn không bị sửa.

Kiểm thử hiện có: **66 test core**, **33 service**, **18 handoff**, **76 neighbor**
và **145 legacy** đạt; toàn bộ **293 test frontend** đạt. Bao phủ hai phía/
Unicode, counter/assumption/version unknown, rehashed classification/citation/
owner tampering, repository alias, frozen reopen, unique intent, HTTP guards,
provider bounds và portable archive độc lập. Browser QA tổng hợp đã kiểm empty,
preview/consent, Space/Tab/Return, queued/running/completed, bản lỗi đã lưu,
Việt/Anh, sáng/tối, desktop 1440px và mobile 390px không tràn ngang. JSON/Markdown
tải từ bản đã lưu; ZIP tải qua UI có một source run và hai comparison, gồm nguồn
ứng viên. SHA khớp preview và verifier Python độc lập chạy với `-I` đạt. Timeline
giữ cả tác vụ so sánh hoàn tất/lỗi và chi tiết cuối hồ sơ. Đã push commit
`396fcc9`; [CI toàn repo thành công](https://github.com/NamTV2712/IssueIntel-Evidence-Grounded-Issue-Triage-Investigation-Agent/actions/runs/39775729234)
và issue #7 đã đóng. Chưa chạy đánh giá chất lượng nhãn, API model thật,
Holdout hoặc Live ở mốc này.

### Mục 9 — P1: chi phí toàn chuỗi điều tra

Projection `investigation_usage_v1` giữ riêng **phiên hiện tại** và **toàn chuỗi**,
với bảng Groq/Typesafe và danh sách owner. Toàn chuỗi theo exact typed ancestry
của selection, cộng các sidecar câu hỏi/check plan/claim review/issue relation
gắn đúng source pack và durable intent. Không dùng việc cùng issue/dossier để
cộng các original run độc lập hoặc sibling investigation; không nhập chi phí
điều tra của issue ứng viên, lần tạo memory đã chọn hoặc tổng global ledger.
Finalization vẫn thể hiện chi phí của cha ở lineage dù bản thân không gọi model.

Collector chụp metadata/receipt local có giới hạn và kiểm bytes/listing/registry
đã quan sát không đổi; không đọc config/giá mới/Git/network/API key/provider
ledger. Snapshot xuất không chứa request, prompt hoặc issue/code input. Bundle
khóa source job/pack khi có và chính snapshot SHA, không cộng hai capture khác
thời điểm. Request/job/proof hashes là binding khai báo của nguồn đã capture,
không chứng minh không tồn tại file hoặc call bên ngoài inventory đó.

Mỗi provider hiển thị **known subtotal**, **complete total hoặc unknown**,
**inventory complete** và **all usage settled** riêng. Integer micro-USD tránh
làm tròn bằng float. Dedup theo provider/reservation với owner bất biến: cùng
owner và bytes chỉ tính một lần; khác owner hoặc bytes tạo conflict, không chọn
bản rẻ hơn hoặc settled. Reserved/failed/missing-usage giữ cận; đủ inventory chưa
đồng nghĩa đã settled. Thiếu attempt tracking/receipt, legacy chưa có journal,
native receipt invalid hoặc usage breach vẫn giữ known subtotal nếu có, còn
complete total là null. Unknown không được diễn giải thành 0 USD.
Typesafe còn kiểm receipt thuộc đúng execution owner và decision slot đã ghi,
không trùng slot. Slot sai hoặc lặp giữ known subtotal từ receipt hợp lệ nhưng
complete total vẫn unknown, kể cả số receipt bằng số attempt.

Thiếu parent/source-pack proof, sidecar discovery hoặc chạm cận scope làm lineage
chưa đủ. Các gap phạm vi này không xóa complete current cost nếu attempts và
receipts của chính job đã đầy đủ. Ngược lại, mất tracking/receipt/owner của job
vẫn làm current unknown. Cả gap và public receipts đã capture còn trong export.
Typesafe reserved receipt hợp lệ để báo accounting; điều này không nới admission
phục hồi đang yêu cầu đối soát reserved trước call tiếp theo.

Câu hỏi/check-plan/claim-review mới ghi durable attempt/receipt-owner
journal trước provider, với cận 1/2/2 call hiện có. Receipt thuộc slot/owner tạo
ra nó; generation/verifier/refinement hoặc Jev decision được reuse không tạo bill
mới. Attempt không có receipt giữ uncertainty, lỗi/hủy/restart không cấp retry.
Legacy thiếu journal được đọc với known subtotal/unknown inventory, không sửa
artifact hoặc suy số call chỉ từ số receipt. Runner vào lại không được reset
journal đã khởi tạo, kể cả journal còn rỗng, để cấp lại allowance.

Gói bàn giao thêm `accounting/<requested-job>/{snapshot.json,usage.json,usage.md}`
cho từng phiên được người dùng chọn. Hai bảng có thể chung chi phí tổ tiên, nên
không được cộng thành tổng cả ZIP. Trusted standalone verifier replay native
receipt rules/core từ included capture và đối chiếu mọi owner chung với included
runs về metadata/request/pack bindings, cùng parent của captured edge. Legacy
handoff không có accounting vẫn đọc được. Đây là consistency của bytes/bindings
được gửi; không phải invoice, xác minh provider thực sự chạy hoặc chứng minh
inventory đầy đủ bên ngoài những gì đã ghi.

Kiểm trên source cuối đạt **174 test**: 102 core, 43 collector và 29 handoff;
toàn bộ **310 test frontend** cũng đạt. Trong bộ collector, sáu ca tập trung
kiểm native Typesafe receipt/slot ownership, cả lỗi inventory và chuỗi recovery.
Handoff kiểm frozen receipt/core rules, binding với included runs, export lặp
xác định và verifier bằng Python isolated.

Browser QA tổng hợp đã kiểm finalization hiện tại 0 call với chi phí cha ở toàn
chuỗi, phiên lỗi còn cận chưa settled, legacy unknown giữ known subtotal, mở lại
sau đổi phiên, JSON/Markdown khi chưa có pack, focus bàn phím và màn hình 390px
không tràn ngang. ZIP tải từ browser đã được verifier độc lập kiểm: hai phiên,
một accounting projection, current 0 và lineage giữ chi phí cha. Các khoản này
đến từ transport tổng hợp, không phải số đo giá provider thật. Lượt hồi quy rộng
đạt **416 test**, có giao với các bộ trên nên không cộng thành số test duy nhất.
Mục 9 đã nghiệm thu trên commit `300164aaac29cd0900d8b391313213f8f37a05d6`:
[CI toàn repo thành công](https://github.com/NamTV2712/IssueIntel-Evidence-Grounded-Issue-Triage-Investigation-Agent/actions/runs/39880133317)
và [issue #9 đã đóng](https://github.com/NamTV2712/IssueIntel-Evidence-Grounded-Issue-Triage-Investigation-Agent/issues/9).
Không gọi provider thật hoặc mở Holdout/Live/evaluation ở mốc này.

### Mục 10 — P1: kiểm thử trình duyệt trọn luồng

Bộ `tests/browser` mở Chromium thật qua Playwright Python 1.64.1, đi qua static
UI → HTTP server loopback → Workbench/pipeline và native provider receipts bằng
transport tổng hợp. Fixture có repository/config/giá/ledger riêng trong thư mục
tạm; không cần `.env`, khóa thật, dataset hoặc đọc Main Dev/Holdout/Live. Browser
và Python chặn network ngoài loopback; không có lời gọi provider thật, gửi comment
hoặc acquisition. Chín kịch bản bao phủ:

- Tạo phiên từ form, follow-up cùng commit, preview/tải ZIP bàn giao và chạy
  verifier trong Python isolated; luồng desktop Anh/sáng và 390px Việt/tối.
- Recovery giữ generation đã xong, chỉ thêm critic sau consent bàn phím;
  finalization giữ chi phí cha mà không thêm call.
- Trì hoãn response pack thật rồi đổi phiên; mở A khi B còn chạy và giữ đúng
  report/chi phí/thông báo A khi B tiếp tục hoặc kết thúc.
- Checklist, follow-up, clarification và observation draft qua đổi tab/ngôn ngữ/
  nguồn; A → B → A khôi phục đúng A, không mang nháp hoặc consent sang B.

Draft chỉ ở bộ nhớ trang, không vào localStorage; reload/đóng trang mất phần
chưa lưu. Key luôn gồm source job/pack, cùng questions/query hoặc full plan
binding khi áp dụng. Checklist có nháp chưa lưu mở lại để thấy nội dung; nguồn
sạch vẫn thu gọn. Follow-up giữ lựa chọn Groq riêng của đúng nguồn; clarification
và observation cần consent mới sau đổi nguồn/ngôn ngữ. Request đang gửi giữ
single-flight qua đổi nguồn; chỉ acknowledgement nối đúng journal và observation
mới được xóa draft quan sát. Không coi bản nháp hoặc quan sát tự báo là kiểm người.

CI thêm job browser riêng. Extra development `browser` và Chromium chỉ phục vụ
kiểm thử; runtime sản phẩm vẫn dùng Python chuẩn, không cần Node/Playwright.
Chạy rõ `ISSUEINTEL_BROWSER_TESTS=1` rồi `python -m pytest tests/browser -q`;
lệnh cài Chromium headless shell ở README. Khi test lỗi mới lưu screenshot/trace
trong ignored `artifacts/browser-tests/` và CI failure artifact. Source cuối đạt
**9 test browser** và **326 test frontend**; chế độ mặc định không bật browser
skip đúng chín ca, trả exit code thành công. Khi đã bật mà thiếu Playwright,
test báo lỗi cấu hình thay vì bỏ qua. Mục 10 đã nghiệm thu tại `6c761e7`;
[cả hai job test/browser của CI thành công](https://github.com/NamTV2712/IssueIntel-Evidence-Grounded-Issue-Triage-Investigation-Agent/actions/runs/39882645559)
và [issue #10 đã đóng](https://github.com/NamTV2712/IssueIntel-Evidence-Grounded-Issue-Triage-Investigation-Agent/issues/10).

### Mục 11 — P2: batch điều tra product — chờ CI phát hành

Panel **Điều tra nhiều issue** nhận JSON hoặc file cục bộ theo
`product_batch_manifest_v1`: run_key ổn định và 1–20 mục có key riêng. Phạm vi
original local/GitHub/example, Quick/Deep/Planned rules, none/Groq; không Main Dev,
Holdout/Live, fork hoặc Jev. Mẫu tổng hợp mặc định offline. Preview có thể đọc
Git/GitHub để chụp issue và full commit, nhưng không gọi model; hiển thị từng
nguồn/lỗi và cận call. Sửa input làm mất preview/consent. Với Groq cần duyệt exact
manifest/preview và chi phí, không coi aggregate USD unknown là 0.

Batch lưu intent/source/preview và fixed child IDs private. Dispatcher gọi
pipeline Workbench chung, concurrency 1 và dùng shared queue/capacity; nhiều hơn
bốn mục không được bỏ vì queue đang đầy. Có status/count từng mục, partial success,
pause/continue, hủy từng mục/cả lô và mở kết quả/checkpoint riêng. Requested cancel
đợi child dừng trước khi thành terminal; call đang gửi có thể còn chi phí.
Completed item vẫn mở được khi mục khác lỗi. Job ID giữ từ trước dispatch;
`job_available` quyết định có thể mở job thật, tránh nút trỏ tới file chưa tồn tại.

Gửi trùng cùng key/manifest trả lại cùng batch/child IDs; key đổi nội dung bị
conflict. Mất ACK khóa yêu cầu đã gửi để đối soát read-only, không tự đổi key hoặc
retry all. Saved read và restart không tự khởi chạy; tiếp tục mục chưa chạy cần
chủ động chọn đúng batch/binding, có consent Groq mới khi cần. Started failed/
interrupted child không được batch tự retry; recovery hiện có giữ cổng riêng.
Batch polling không dùng `runJob()` hoặc tự đổi report; stale preview/create/poll/
cancel responses không được gắn vào batch mới. Tiếng Việt/Anh, semantic light/dark,
bàn phím và item details ở màn hình nhỏ dùng cùng nguồn.

API product gồm preview/start/history/detail, pause/continue/cancel và item cancel.
Raw `manifest_json` được parser server kiểm duplicate fields trước chuẩn hóa;
JSON/source text chỉ render inert. Contract thuần dữ liệu đạt 107 test; source cuối
đạt 26 test backend/HTTP integration, 22 HTTP route checks, 345 frontend và toàn
bộ 14 kịch bản Chromium (chín luồng cũ và năm luồng batch). Chế độ optional mặc
định skip đúng 14 ca, exit thành công. Hồi quy Workbench/recovery đạt 114 test;
các sửa isolation/race sau lượt đó đã được kiểm lại trong bộ 26 test cuối. Các
phạm vi có overlap, không cộng thành tổng test độc lập.

Test tái hiện snapshot DB chậm hơn JSON và hoàn tất giữa lần đọc row/lease; nguồn
hỏng vẫn giữ occupied khi worker còn quyền thực thi. Batch hỏng không có worker
được báo riêng trong lịch sử, giữ các batch hợp lệ hoạt động. Chromium kiểm nguồn
đã duyệt, sáu mục vượt capacity, paid consent/partial success/native receipts,
hủy pending, delayed HTTP A→B, mở child, bàn phím và màn hình 390px. Visual QA giữ
nguồn đã duyệt trong details gọn, đồng bộ trạng thái/counts giữa detail và history.
Chỉ dùng dữ liệu/transport tổng hợp; chưa gọi model thật hoặc mở evaluation.
CI đúng commit và quyết định đóng mục được theo dõi tại
[issue #11](https://github.com/NamTV2712/IssueIntel-Evidence-Grounded-Issue-Triage-Investigation-Agent/issues/11).

## Kế hoạch kiểm tra có phiên bản

Tab Check plan/Kế hoạch kiểm tra lấy bước đề xuất trong pack; description không
cho ghi đè. Trạng thái gồm todo/in-progress/blocked/reported-done. Bước blocked
hoặc reported-done cần ghi chú; mọi quan sát là tự báo cáo, chưa xác minh. Không
thực thi code/test, thay đổi confidence hoặc đánh giá test split khi lưu checklist.

SQLite local tại `artifacts/demo/plans.sqlite` lưu event có hash/source pack/
parent hash, timestamp, version và change. Mỗi lần đọc replay từ plan gốc; dữ
liệu không khớp source hoặc chain bị từ chối. `BEGIN IMMEDIATE` cùng expected
SHA256 chặn ghi đè phiên bản cũ giữa thread/process. Giới hạn 200 version/job,
50 bước và 2.000 ký tự/ghi chú. Ghi không thay pack, hypotheses hoặc receipt gốc.
Đây là lịch sử local, không phải chứng thực chống mọi sửa đổi bởi chủ filesystem.

Có tải lại và export plan JSON riêng; mở lại sau restart không gọi model. Bản
nháp ghi chú giữ qua đổi ngôn ngữ. Metadata plan lỗi không ngăn xuất báo cáo
nguồn còn nguyên vẹn. Không đưa ghi chú checklist vào prompt tự động.

## Deep: truy xuất theo giả thuyết

Quick giữ một lượt sinh và critique riêng. Deep khi bật Groq dùng evidence ban
đầu để sinh hypotheses, rồi rules/typed controller có thể chọn retrieve/verify/
stop. Retrieve chỉ chạy tối đa một vòng, lấy statement/falsification của ba
hypotheses đầu làm query text, tối đa 1.201 ký tự/query, ba hit/query và tổng sáu
excerpt mới. Không thực thi nội dung query hay kiểm tra được model đề nghị.

Mọi hit được replay đúng file/range/text/SHA/revision/protocol/available_at trong
cùng snapshot. Loại đoạn trùng hoặc overlap; không đọc local history, GitHub,
gold hoặc snapshot khác. Nếu có đoạn mới thì sinh lại một lần rồi critique trên
evidence cuối. Không có đoạn mới thì bỏ call sinh lại. Query/proof gốc giữ nguyên.
Hit không tự trở thành counter-evidence, không chứng minh causal truth hoặc cải
thiện chất lượng; hypotheses vẫn automatic/unverified. Seed annotation và query
trace được lưu để phân biệt đầu ra trước/sau. Kết thúc sau vòng giới hạn hoặc
controller stop; không có vòng agent vô hạn.

Giới hạn trên là logical passes, không phải số HTTP request: generation có thể
repair/fallback schema theo policy sẵn có. Mỗi call thực vẫn qua provider bounds,
giá có ngày và durable budget ledger. Lỗi budget/network dừng, không retry ngầm.
Khi tắt Groq, Deep chỉ thay độ rộng evidence và không sinh/search theo hypotheses.

CLI mặc định giữ baseline; bật rõ `investigate --refinement-rounds 1`. Pipeline
không cho mở rộng `prepared_evidence` đã chốt. Không đổi recipes, registrations,
cohort hoặc receipts cũ để hợp thức hóa code mới; benchmark bulk vẫn tạm dừng.

Prepared input, seed generation, focused retrieval, refined generation và
verification có checkpoint riêng trong ignored artifacts. Workbench lưu dưới
`artifacts/demo/runs/<job>/stages/`; CLI dưới thư mục output/stages/<random-id>/.
Failed/interrupted job chỉ có những stage đã hoàn tất; không được coi là pack
hoàn thành. UI có tải checkpoint ở lịch sử job lỗi và báo job ID đang xem. CLI
in refs đã lưu khi lỗi. Mở/tải không gọi model và không đổi bytes/hash checkpoint.

Product Quick/Deep lưu thêm `verification_input` ngay trước verifier. Với phiên
GitHub/local/synthetic bị failed/interrupted/cancelled và chưa có verification
hoàn tất, người dùng có thể kiểm tra checkpoint rồi duyệt một lần gọi verifier
có chi phí. Generation và evidence cuối được dùng lại, kể cả refinement đã xong.
Không tự retry. Phiên mới giữ liên kết/hash về phiên nguồn; receipt mới chỉ thuộc
call mới, chi phí cũ có thể vẫn đã tính. Intent SQLite giữ một lần tiếp tục cho
mỗi nguồn qua gửi đồng thời/restart; gửi lại trả cùng job, kể cả job đó đã lỗi.

Trước preview và trước provider, replay snapshot Git local, query, evidence,
citation, annotation và dấu vân tay policy/code. Thiếu repo/commit, hash lệch,
policy đổi hoặc model/max-output không tương thích thì từ chối, không sửa hash.
Checkpoint cũ thiếu payload này không được suy diễn thành đầu vào hợp lệ. Không
áp dụng Main Dev/strict, follow-up, generation lỗi hoặc verifier đã hoàn tất;
không kế thừa quyền đăng comment của phiên GitHub. Đây là phần tiếp tục verifier
của F10, chưa phải resume tùy ý tại mọi stage hoặc phục hồi bulk benchmark.

Nếu đã có cả `verification_input` và `verification` nhưng export bị lỗi/ngắt,
luồng **Kết xuất từ checkpoint đã hoàn tất** kiểm hai bytes/hash nguồn, request
product gốc, snapshot descriptor, annotation/citations và critique theo schema.
Admission và worker yêu cầu policy hiện tại khớp checkpoint. Không đọc API key,
runtime budget, repo Git hoặc gọi lại model. Nó tạo pack/report trong thư mục
phiên mới, giữ annotation/verifier nguyên vẹn, ghi `finalized_from` và trace tái
sử dụng với `model_calls: 0`; chi phí và checkpoint cũ không chuyển quyền sở hữu.
Đây là replay dữ liệu đã lưu, `full_git_replay_performed: false`, không phải
tái xác nhận nội dung Git hoặc nguyên nhân lỗi. Bản đã hoàn tất vẫn đọc bằng
policy được ghi lúc tạo, kể cả khi policy hiện tại đổi. Mọi lần đọc xác minh
source request, hai checkpoint, durable intent và toàn bộ child pack; không sửa
hash để bỏ qua lỗi. Chỉ nhận nguồn product Quick/Deep gốc failed/interrupted/
cancelled; không Main Dev, Planned hoặc follow-up, không kế thừa GitHub write.
UI không tự preview hoặc xuất khi mở lịch sử. Người dùng chủ động kiểm rồi xác
nhận; gửi lặp/restart trả cùng child ID và không retry một attempt đã lỗi.

## Tích hợp GitHub

Chọn **GitHub issue URL**, nhập URL và revision. `HEAD` lấy snapshot hiện tại;
không biến mô tả REST hiện tại thành opening-query strict. Tab **GitHub** tạo bản
nháp có code links theo commit đã đọc. Sau khi xem đúng issue đích và nội dung,
người dùng đánh dấu duyệt và bấm đăng. Token local cần quyền comment trên issue
đích. Thay nội dung/hash/target bị từ chối; một intent đã bắt đầu không retry ngầm
khi lỗi hoặc restart. Không có comment thật nào được đăng trong kiểm thử phát triển.

Adapter webhook hỗ trợ `/issueintel investigate [revision]` từ OWNER, MEMBER hoặc
COLLABORATOR trên issue của repo public. Nó tạo một job evidence-only; không tự
chi Groq hoặc gửi comment. Delivery lặp lại dùng cùng job. Cần đặt riêng
`GITHUB_WEBHOOK_SECRET` trong `.env` và cấu hình GitHub App webhook `issue_comment`.
Secrets không đặt trong repo/config mẫu hoặc chat. Endpoint local là
`/api/github/webhook`; server mặc định chỉ bind localhost. GitHub không thể gọi
localhost trực tiếp: triển khai endpoint/gateway HTTPS và cài App vẫn là bước
cấu hình tiếp theo, chưa được làm tự động hoặc coi đã hoàn thành.

Relay stdlib chạy riêng bằng `python run.py --webhook-relay` (loopback 8788,
đích Workbench 8787; có `--port`/`--workbench-port`). Chỉ exact POST webhook
được nhận; không UI hoặc API phiên. Relay kiểm HMAC trên raw bytes, cận body/
headers/4 kết nối/8 giây, bỏ credential/session/Origin của caller và dùng Host
loopback cố định. Response chỉ có trạng thái chung, không lộ job/token/body
receiver; không retry khi kết quả chưa rõ. Gateway HTTPS cần nối vào cổng relay,
không công khai cổng Workbench. Command App yêu cầu connection đang hoạt động,
global repo ID/installation/configured App khớp, kể cả delivery cũ sau gỡ quyền.
GUID được chuẩn hóa trước durable intent; receipt tên cũ được giữ nguyên và
case aliases mâu thuẫn bị từ chối (inventory có cận 10.000 entries). Legacy manual signed webhook vẫn dùng
được khi không có App binding. Đây là chuẩn bị ingress, chưa nghiệm thu App thật;
adapter đăng comment hiện dùng token local, chưa phải installation-token auth.

Write adapter pin API version `2026-03-10`, kiểm HTTP 201, exact body và comment
URL/ID từ response. Nguồn đã đối chiếu ngày 14/10/2026:
[Create issue comment](https://docs.github.com/en/rest/issues/comments#create-an-issue-comment)
và [Webhook validation](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries).

Ngày 24/10/2026, theo lựa chọn thử đọc repo public của tài khoản khác, đã chạy
product smoke trên `pypa/sampleproject`, issue #225, nguồn `21fd8d4`. Scan tiếp
tục checkpoint/snapshot cũ qua tám đợt: 97 issue, 141 PR, 563 issue comment,
126 review comment, 370 PR-file record và 520 PR-commit record; không còn task/
update pending hoặc unavailable record. Hai số PR-file/commit là record theo PR,
không phải số file/commit duy nhất hoặc toàn bộ lịch sử Git. Snapshot commit
`621e4974ca25ce531773def586ba3ed8e736b3fc` có 12 file admitted, không skipped file.
Điều tra Quick offline hoàn tất với bốn code excerpts từ `pyproject.toml` và
bốn repository-context excerpts có receipt. Pack/report mở lại qua read adapter,
hash/citation consistency và snapshot-index binding đạt khi deny network/config/
store constructors; không cần gọi model. Connection thử được pause sau khi xong.
Không ghi lên repo đích, không mở benchmark/Holdout/Live và không đánh giá chất
lượng chẩn đoán. Đã sửa lỗi Link issues có `after` cursor với red regression;
74 index checks và 178 GitHub/context checks đạt, phạm vi có overlap.
Ingress `0a40087` có cả hai CI job test/browser thành công tại run `40035125021`.
Pagination `21fd8d4` cũng thành công cả hai job tại run `40035496626`.
Smoke chỉ đọc này không đóng mục 4: installation/HTTPS/events/approved comment
thật, restart và mất quyền trên dịch vụ thật vẫn cần nghiệm thu riêng.

## Hồ sơ xuyên suốt issue (D01 — F05/F07/F14)

`GET /api/jobs/<id>/dossier` và `.../dossier.json` dựng hồ sơ từ những product
runs cùng GitHub issue hoặc cùng local repository/query gốc; không giới hạn ở
50 job gần nhất của danh sách lịch sử. Source job được chọn phải hoàn tất và
qua product authority replay. Phiên lỗi/chưa xong chỉ có metadata; nguồn khác
không đọc lại được xuất hiện trong warnings, không được diễn giải thành kết quả.
Source recovery failed có thể chưa từng có pack; child finalization giữ nguyên
case/stage gốc và vẫn nằm trong timeline. Main Dev/benchmark bị từ chối.

Timeline gom run, saved plan, report/check-plan observations và corrections.
Transition giữ hai report bindings, evidence diff và chính receipt quan sát
đã dùng cho child. Bảng chỉ so reported A/B với model prediction A/B/unknown:
matches/conflicts/undetermined không phải semantic correctness hay causal proof.
Commit tự báo được phân biệt unknown/same/different; correction mới không sửa
receipt hoặc ma trận lịch sử của child. Không tự ghép hypothesis theo ID.

`POST /api/jobs/<id>/dossier-links` nhận `source_pack_sha256`, `expected_sha256`
của hypothesis-link journal và `relation`: parent/child job, nullable hypothesis
IDs, kind retained/refined/new/not_carried, reason, 1–12 exact evidence refs,
nullable supersedes. Chỉ nối parent-child đã replay trong cùng hồ sơ; state
SQLite tối đa 200 events với optimistic transaction, source binding và append-only
correction. Diễn giải giữ user_report_unverified; không là independent human
validation hoặc kết luận lỗi đã sửa. UI giữ draft khi đổi view/ngôn ngữ hoặc lỗi
gửi, chặn late response và không gọi model khi đọc/lưu/export.

## Planned chờ dữ kiện và tiếp tục trong cùng ngân sách (D05)

Web Planned với rules dùng `planned_investigation_v3` / `bounded_planner_v3`;
CLI thường `investigate --planned` và Jev giữ v2; CLI `--durable` rules dùng v3,
API Python giữ v1 mặc định. V1/v2 đã
lưu vẫn đọc nguyên bản. Cả v2/v3 phân biệt support/counter/caller/docs/history,
khóa gap, hypothesis, query và nguồn cho từng action. Caller là cạnh call tĩnh
exact; docs/history chỉ dùng snapshot hoặc context có receipt đã được nhận,
không tự tải lịch sử mới và không nhận current history thành strict proof.

V3 có thể lưu pack **Chờ câu trả lời / Waiting for answers** sau một lượt typed
search đã thực sự thử query nhưng không thêm evidence, còn câu hỏi intake chưa
trả lời và còn budget cho generation mới cùng critic. Đây là câu hỏi xem lại cue
theo template Việt/Anh, chưa chứng minh issue thiếu thông tin. Pack chờ giữ
annotation và structural citation check; critic chưa chạy. Tab **Trace** cho
chủ động kiểm khả năng tiếp tục, xem các câu hỏi và budget còn lại. Trả lời một
phần hoặc “chưa biết” rồi xác nhận chi phí mới tạo child; mở báo cáo, đổi ngôn
ngữ hoặc xem câu hỏi không tự gọi model.

GET `/api/jobs/{source}/planned-continuation` trả binding source pack, workflow
plan, waiting và policy SHA cùng remaining counts; không đọc Git hoặc gọi API.
Nó so policy hiện tại để báo khả năng tiếp tục. POST cùng route yêu cầu đúng bốn
SHA, answers/language và confirmation `continue_planned_with_api_cost`. Trước
enqueue, service replay snapshot Git, current policy, nguồn request và owned
call receipts; kiểm nguồn lại trước từng call/repair và publish. Policy đổi hoặc
accounting không đủ proof thì chặn gọi tiếp, giữ nguyên bytes/receipts cũ.
Một durable intent cho mỗi source pause: cùng request trả child cũ, đổi answers
bị từ chối; child lỗi/hủy/ngắt không được tự thay bằng một paid attempt mới.

Child dùng cùng commit, copy code evidence của cha và giữ đúng context
records/receipts đã khóa; chỉ rebind context envelope/excerpts sang query có
answers. Query mới phải sinh hypotheses mới, không gắn annotation cũ sang query
mới. Answers, cue text và nguồn cha đi trong `continued_from`, giữ unverified;
câu hỏi/template tự sinh không được tính thành cue người dùng. Root route/limits,
counts và attempted query hashes được kế thừa: tổng toàn chuỗi tối đa ba vòng
truy xuất, bốn generation, một critic và chín Groq calls kể cả repair. Route nhỏ
hơn giữ cận nhỏ hơn; continuation không mở thêm vòng hoặc reset budget. Các
form follow-up, clarification, context và observation follow-up riêng bị chặn
khi đang chờ; dùng continuation để giữ đúng cận.

Saved reports/dossier/handoff replay nguồn và lineage đã ghi, không cần Git,
API key, model hoặc policy hiện tại. Handoff chọn child sẽ thêm các tổ tiên bắt
buộc; portable verifier kiểm bindings/counters/answers/frozen sources trên bytes
được đưa vào gói. Kiểm này không chứng nhận semantic truth, hóa đơn provider,
người gán nhãn hoặc proof lịch sử. Waiting/continuation đã qua 85 test lõi
Planned, 60 test service/tương thích, 53 test handoff, 212 test frontend và
kiểm trình duyệt tổng hợp Việt/Anh, sáng/tối. Chuỗi ba phiên giữ cùng commit,
dùng ba vòng truy xuất và bốn call giả lập tổng cộng, với một critic cuối;
partial/unknown answers, budget kế thừa và timeline cha–con đã được kiểm.
D05 đã nghiệm thu với commit `2773a8c` và [CI toàn bộ thành công](https://github.com/NamTV2712/IssueIntel-Evidence-Grounded-Issue-Triage-Investigation-Agent/actions/runs/39385189121).
D08 resume stage lỗi và V01/V02 quality evaluation vẫn giữ phạm vi/cổng riêng.
Không gọi provider thật hoặc mở evaluation.

## Backlog được tác giả yêu cầu ngày 19/10/2026

Danh sách sản phẩm gồm **mục 1–13**; P0/P1/P2 là mức ưu tiên. Ký hiệu D01–D13
trong bảng và GitHub issue bên dưới chỉ ánh xạ về đúng số mục này, không phải
một danh sách khác.

Mốc Workbench memory của mục 6 đã qua 156 test liên quan (memory store,
product API, Planned continuations, dossier và report); bộ API cuối bổ sung
exact file-node/CRLF source hash đạt 24 test. Toàn bộ frontend đạt 228 test.
Browser tổng hợp kiểm lưu architecture/lesson, đổi Việt/Anh, sáng/tối, bàn phím,
giới hạn capture sai, giữ draft, viewport 390 px không tràn trang, source mất
vẫn đọc cache, assessment lỗi không giữ status cũ, và revision mới đánh dấu
parser changed trong khi capture cũ giữ commit gốc. Đây là kiểm contract/UI,
không đo chất lượng ngữ nghĩa hoặc mở benchmark evaluation. Nguồn/model đều
tổng hợp; preview restart đọc capture cũ với zero model calls. CI của mốc này
đã thành công trên commit `57fa5a5`.

Mốc lựa chọn memory bổ sung đã qua 160 test memory/CLI/Planned liên quan; bộ
preview/API cuối đạt 45 test, toàn bộ frontend đạt 233 test. Browser tổng hợp
đã chọn hai mục xuyên architecture/lesson, chặn parser fact không còn khớp,
giữ static fact còn nguyên và prior hypothesis `source_changed` chưa xác minh,
đổi ngôn ngữ giữ selection và xóa preview khi checkbox đổi. Context có source,
viewed revision cùng selection/context SHA; chưa tạo job hoặc gọi model. UI
chưa thực thi inference với selection hoặc xuất nguồn memory trong portable handoff
tại mốc preview `aff83c3`; CI của commit này đã thành công.

Mốc tiếp theo nối preview vào phiên điều tra product Quick/Deep. Người dùng xem
exact context rồi xác nhận offline hoặc Groq với consent chi phí riêng. Code
được truy xuất lại ở cùng commit; prior hypotheses không trở thành code citations
hoặc nhãn đúng sai, memory không được tính là intake cue do người báo cung cấp.
Một durable intent cho mỗi preview chặn gửi lặp, đổi provider/depth để chạy thêm
và tự retry sau lỗi/hủy/restart. Nguồn/policy đổi trước/sau call hoặc trước khi
công bố làm dừng phiên và giữ receipt. Saved report/dossier đọc lại không cần
Git/current AST/live memory store; UI giữ riêng memory đã dùng. Handoff có exact
selection/context attachment và source ancestors; standalone verifier kiểm
recorded projection/query/item/receipt/truth bindings, không tái chứng minh AST
hoặc tính xác thực capture gốc. Đã qua 77 test memory/selection/handoff và 239
test frontend tổng hợp; bộ hồi quy rộng đạt 514 test, kiểm tra backend cuối đạt
36 test sau bổ sung malformed provider/depth inputs. [CI toàn bộ của commit
`8bada45` đã thành công](https://github.com/NamTV2712/IssueIntel-Evidence-Grounded-Issue-Triage-Investigation-Agent/actions/runs/39434454066).
Browser tổng hợp đã chạy offline từ preview, mở lại memory đã dùng, kiểm consent
Groq, English/dark và viewport 390 px không tràn. Lỗi provider giả lập giữ receipt,
không có report thành công giả và kiểm lại cùng intent không tạo phiên/call mới.
Transport Groq là giả lập;
không có provider thật, Holdout/Live hoặc evaluation.

Mục 6 đã nghiệm thu trên commit `8bada45`: core/store/UI và confirmed selection
đã hoàn chỉnh trong phạm vi product. `test_repository_memory.py` kiểm source span,
dependency/relationship/producer invalidation và full source/target availability
**strictly trước cutoff**; Git dates/hash riêng lẻ bị từ chối. Store/lesson tests
kiểm immutable source pack/query/revision, saved read không có checkout và prior
hypotheses luôn chưa xác minh. Product/API/UI/inference/dossier/handoff tests ở
trên kiểm source ownership, explicit intent, guards và mở lại offline. Đây là
nghiệm thu chức năng, không phải chứng minh memory cải thiện chẩn đoán hoặc đã
thu thập lịch sử thực cho một cohort mới. Lessons không được cấp historical
eligibility; portable hash verification không thay cho proof lịch sử/AST.

Mục 8 có lõi journal/replay opt-in trong pipeline chung. Fault injection đã
kiểm các ranh giới từ execution input/initial retrieval qua seed, focused search,
refined generation, từng retrieval/generation của cả ba vòng Planned, verifier
và completed plan. Reuse không ghi lại stage cũ hoặc chạy lại operation đã xong;
root source bytes/receipts được giữ nguyên. Test Groq giả lập có schema repair
giữ receipt cũ riêng với receipt mới; failed/in-flight attempts phải được caller
đưa vào cận kế thừa đã biết, không tự đoán bằng số stage thành công. Snapshot/
graph/query/policy, thứ tự journal, cached code và loại critic đều được kiểm.
HEAD mới không đổi commit gốc. Offline giữ cận 0; rules Planned v3 vẫn có thể
dừng chờ, không tự trả lời câu hỏi hoặc mở rộng budget. Bộ hồi quy rộng đạt 443
test trước hardening cuối; bộ test lõi và tương thích cuối đạt 146 test, gồm 58
test recovery. Chưa có provider thật hoặc evaluation. [CI toàn bộ của core
`2773fb4` đã thành công](https://github.com/NamTV2712/IssueIntel-Evidence-Grounded-Issue-Triage-Investigation-Agent/actions/runs/39438853954).

Mốc tích hợp tiếp bật capture/replay cho original Workbench example/local/GitHub
Quick/Deep/rules Planned. Admission kiểm owned source/stage bytes, recipe/query/
snapshot/policy và durable attempts gồm failed/in-flight. SQLite execution lease
chặn phục hồi nguồn còn có worker sở hữu. Preview dừng trước model; consent khóa
source SHA và có/không API. Một durable intent/source qua restart, cancel/failure
không tự retry. Child chỉ lưu stage/receipt mới, giữ nguyên source prefix và cận
call gốc; source/policy guard chạy trước/sau provider và publication. Không API
thì không load provider/key; saved read không cần Git/config/current policy.
Tuple→JSON-array roundtrip được kiểm bằng canonical digest, byte receipt vẫn
khóa đúng file gốc. Verifier/export recovery cũ giữ tương thích và quyền xử lý
late Quick/Deep riêng.

Hồ sơ issue giữ failed root/recovered child; handoff thêm source/tail stage bytes
và witness `stage-recovery.json`. Standalone verifier kiểm included bytes, source/
attempt/sequence/output binding; không kiểm hóa đơn, AST hoặc chẩn đoán đúng.
Giao diện Việt/Anh có preview/consent, trạng thái lost acknowledgement, giữ cùng
intent và bảo vệ report/usage khi chuyển phiên lúc POST hoặc polling đang chạy.
Hồi quy tích hợp đạt **423 test backend**, bộ handoff cuối đạt **77 test** và
toàn bộ frontend đạt **250 test**. Browser tổng hợp đã phục hồi lỗi seed với
transport giả, giữ attempt lỗi 1/4, mở lại sau restart, giữ lineage và preview
ZIP thêm failed parent. Đã kiểm bàn phím, Việt/Anh, sáng/tối và viewport 390 px.
Không có provider thật, Holdout/Live hoặc evaluation.

[CI toàn bộ của mốc Workbench `4d043d4` đã thành công](https://github.com/NamTV2712/IssueIntel-Evidence-Grounded-Issue-Triage-Investigation-Agent/actions/runs/39521323747).

Mốc tiếp theo cho phép **trả lời sau một Planned recovery đã dừng chờ**.
Plan/waiting receipt gốc giữ nguyên logical completed-stage calls. Preview và
admission trừ thêm failed/in-flight attempts qua recorded recovery binding;
source service kiểm cả root/child intent, journal và receipt ownership. Thiếu
receipt, unknown tracking, trùng ID hoặc khác attempt stage thì từ chối gọi.
Phải còn đủ ba call cho generation, một repair và critic. Continuation kế thừa
attempt debit đúng một lần; trả lời từng phần qua nhiều phiên không cộng lặp
debit hoặc reset limits. Portable handoff có mọi tổ tiên và replay cumulative
attempt counts từ recovery witness. UI Việt/Anh hiển thị logical stage counts
riêng với whole-chain attempt budget, giữ draft khi đổi ngôn ngữ và vẫn yêu cầu
consent. Authority đổi trong call thì giữ receipt, dừng trước checkpoint/critic
tiếp theo; không công bố pack thành công.
Hồi quy rộng đạt **432 test backend**, gồm 9 test mới cho recovered waiting,
receipt/accounting denial, partial-answer descendants và in-flight source drift;
toàn bộ **253 test frontend** đạt. Standalone handoff kiểm riêng việc giảm
inherited attempt debit rồi reseal mọi wrapper vẫn bị từ chối. Browser tổng hợp
đã giữ nháp khi đổi Việt/Anh, yêu cầu consent và chạy từ 2/7 attempts (còn 5)
đến continuation hoàn tất với 4/7 toàn chuỗi, không có provider thật.
[CI toàn bộ của mốc `98b2c9f` đã thành công](https://github.com/NamTV2712/IssueIntel-Evidence-Grounded-Issue-Triage-Investigation-Agent/actions/runs/39522837044).

Mốc CLI thêm `investigate --durable` để original product dùng cùng owned job
store/journal với Web, có thể chọn Quick/Deep/rules Planned v3. `resume-stages
--job ID` mặc định preview; binding SHA cùng `--with-api`/`--without-api` mới tạo
child. Một intent được dùng chung qua CLI/Web, kể cả duplicate sau restart;
CLI không tự retry. Không hỗ trợ benchmark, custom output hoặc bỏ critic trong
durable mode; legacy CLI giữ contract cũ. Preview/duplicate không đọc `.env`,
model config/provider và không chạy scheduler hoặc reconciliation GitHub.
Lease được giữ ngay khi queued, chuyển cho worker và thả khi cancel/dispatch
failure/complete; observer khác process giữ đúng queued/running khi lock còn
được sở hữu. Crash có lock được OS thả không bị nhầm với reader của admission.
Bộ hồi quy rộng đạt **461 test backend**; bộ CLI cuối đạt **16 test**, gồm
Quick/Deep/Planned offline, exact consent, duplicate sau restart, live/queued
ownership, cancel/dispatch cleanup và initial GitHub credential loading. Test
auto-sync xác nhận observer CLI không đổi connection hoặc gọi GitHub ngầm.
Smoke bằng tiến trình CLI thật trên repo tổng hợp phục hồi một lỗi export với
preview 0 model calls, 0 reservation mới và gửi lặp trả cùng child. Không có
provider thật, Holdout/Live hoặc evaluation.
CI toàn bộ của commit CLI `93743d0` đã
[thành công](https://github.com/NamTV2712/IssueIntel-Evidence-Grounded-Issue-Triage-Investigation-Agent/actions/runs/39524327760).

Mốc tiếp theo nhận **phiên stage-resume bị lỗi tiếp**. Source binding v2 ghép
prefix và global attempt sequence từ tối đa tám phiên nguồn; từng stage giữ
owner job/raw SHA riêng. Mỗi cạnh phải có durable intent đúng request và input
binding; missing/unknown attempts, trùng receipt owner, changed source/policy
hoặc exhausted bound thì không có call mới. Mỗi nguồn lỗi cần xác nhận riêng;
gửi lặp cùng nguồn vẫn trả cùng child. Generation/rounds đã hoàn tất không chạy
lại, kể cả khi chỉ còn critic; old receipts và source files giữ nguyên chủ sở hữu.
Root v1 không bị migrate; saved read/duplicate/handoff vẫn dùng recorded policy.
Planned mới giữ cận riêng 5/7/9 sau routing, ngoài profile chung tối đa 9;
mọi attempt cũ vẫn trừ vào cận này. Context ghi policy marker và pinned parse-error
count; seed/evidence/skipped files tái dựng routing trước call. Telemetry có
`effective_max_provider_calls` khi cần, không được xóa/tăng rồi reseal để qua
validator hoặc offline verification. Trước seed chưa biết routing dùng profile
9; sau khi biết cận thấp hơn thì không gọi tiếp hoặc công bố pack vượt cận.

Handoff v2 bao gồm cả các tổ tiên lỗi, raw stages tại owner path và witness cho
mọi request/attempt/usage/input binding. Verifier độc lập tính lại prefix, các
binding và cumulative debit, giữ giới hạn included-byte consistency. Planned
waiting sau nhiều lần lỗi mang mọi debit vào answer continuation đúng một lần;
`recovery_attempt_debit` chỉ tách failed attempts có nguồn khỏi completed-stage
generation/repair counts, không tăng call limit. Legacy v3 trong cận cũ giữ shape
và plan SHA. UI Việt/Anh hiển thị root, số nguồn và tổng lượt thử, không tự retry.
Khi hết cận, preview trả `original_call_budget_exhausted` cùng used/max; UI
giải thích giới hạn và không cung cấp nút xác nhận gọi tiếp. Bộ frontend đạt
**259 test** và được đưa vào CI, gồm các cận Planned và chuỗi nguồn. QA trình
duyệt tổng hợp đã kiểm explicit consent, reuse generation khi critic lỗi,
dossier ba phiên, preview handoff gồm hai tổ tiên lỗi, Việt/Anh, light mode,
bàn phím và màn hình 390px không tràn ngang. Ba bộ fixture đã lưu từ các mốc
trước được mở/xuất lại khi cấm config/Git/provider; source bytes giữ nguyên.
Bộ hồi quy cuối đạt **491 test backend**: fault injection ở generation/critic,
từng vòng Planned, zero-API export, concurrent intent, giới hạn ancestry,
source/receipt tampering, route cap và debit sau nhiều lần lỗi. Mọi provider
trong bộ kiểm là giả lập; không có gọi API thật hoặc mở benchmark/evaluation.

Mốc tiếp theo nhận **answer continuation Planned mới bị lỗi**. Binding v3 khóa
original continuation request và answer intent, pack cha, câu trả lời, inherited
plan và owned stage prefix. StageTape giữ cận local bằng root call limit trừ
inherited calls, kể cả mọi failed attempt từ các phiên phục hồi trước. UI hiển
thị riêng số lượt đã dùng ở cha và cận còn lại; cần consent mới để gọi tiếp.
Nếu chỉ export còn thiếu thì không khởi tạo provider/config hoặc gọi API.
Handoff v3 chứa cả answer ancestry và recovery ancestry, verifier kiểm request /
answer / inherited budget bằng included bytes. Phiên trả lời sau recovered
partial answers vẫn mang failed debit đúng một lần. Không sửa bản nhãn, plan,
receipt hay source hash cũ; legacy continuation thiếu journal không tự migrate.
Frontend đạt **262 test**. QA browser tổng hợp xác nhận: preview tách phần cha
đã dùng khỏi cận đoạn phục hồi, consent mặc định tắt, keyboard consent và chạy
đúng một critic mới khi seed đã hoàn tất. Dossier ba phiên và preview handoff
tự thêm cả cha completed lẫn continuation lỗi. Việt/Anh, light/dark và viewport
390px giữ context, không tràn ngang. Ba bộ saved fixtures cũ vẫn đọc/duplicate/
handoff khi chặn config/Git/provider, với source bytes giữ nguyên.
CI toàn bộ của mốc chuỗi phục hồi `58c777f` đã
[thành công](https://github.com/NamTV2712/IssueIntel-Evidence-Grounded-Issue-Triage-Investigation-Agent/actions/runs/39530839520).
Bộ hồi quy cuối của mốc answer recovery đạt **570 test backend**; trong đó
**23 test mới** kiểm answer/critic/round failures, partial-answer debit, source
mutation, intent/hash tampering, combined ancestry, HTTP/CLI và zero-API export.
Kiểm độc lập bằng tiến trình Python isolated xác nhận handoff v3 mở được ngoài
project. Toàn bộ provider là transport tổng hợp; không có API thật hoặc evaluation.

Mốc tiếp theo nhận **các fork product mới bị lỗi**: follow-up thường, trả lời
làm rõ, quan sát kiểm tra, memory và context repository đã chọn. Logic chuẩn bị
và gắn provenance dùng chung giữa initial run/recovery. Source/handoff v4 khóa
original fork request/owner, parent bytes, exact case và provenance; raw stage
và receipt giữ đúng owner. Quick/Deep giữ cận 4/6 của fork ban đầu, tính cả các
lượt lỗi; export không cần API nếu không còn model stage. Fork từ waiting
Planned vẫn bị chặn. Saved replay đọc frozen memory/context/journal prefix,
không phụ thuộc current index/store; admission gọi tiếp phải kiểm nguồn mới.
Logical depth không reset sau recovery và cả initial fork lẫn recovery đều
giữ cận physical ancestry của dossier. Standalone verifier mở được handoff v4
với memory attachment giữ original fork identity và unverified truth flags.
Hồi quy cuối đạt **805 test backend** và **263 test frontend**. Bộ kiểm tập
trung trước đó đạt **100 test** cho các nhánh; có **15 test mới** cho
failure/recovery/provenance, bao gồm kiểm handoff bằng Python isolated.
QA browser tổng hợp xác nhận memory fork bị ngắt sau seed chỉ chạy thêm một
critic khi phục hồi; ba phiên giữ tổng năm attempt, không tạo lại seed hay cộng
lại receipt cha. Dossier/handoff preview tự thêm cả phiên gốc completed và fork
lỗi, giữ memory selection và các cờ chưa xác minh. Việt/Anh, light/dark, keyboard
consent và viewport 390px đã kiểm; không tràn ngang. Ba bộ fixture đã lưu trước
các phiên bản recovery mới vẫn đọc, duplicate intent và xuất/verify handoff
khi chặn config/Git/provider, không sửa source bytes.
CI toàn bộ của mốc answer recovery `af319eb` đã
[thành công](https://github.com/NamTV2712/IssueIntel-Evidence-Grounded-Issue-Triage-Investigation-Agent/actions/runs/39558610277).

Mốc Jev dưới đây bổ sung phần phục hồi controller còn lại của mục 8.

Mốc Jev mới thêm `planned_decision_1..4` vào journal cho original Planned v2.
Context khóa root/model/policy; sidecar v2 giữ logical decision order và actual
execution owner riêng. Quyết định đã ghi trong row nhưng chưa tới checkpoint
kế tiếp cũng được replay đúng state/allowlist, không gọi lại client hoặc ghi
lại receipt của nguồn. Attempt được ghi trước client, giữ cận bốn lượt Jev toàn
chuỗi và cận Groq 5/7/9 riêng, tính cả lượt lỗi/lost response. Hết Jev thì không
gọi generation chắc chắn không thể hoàn tất bước điều phối tiếp theo.

Receipt terminal chưa có decision được preview báo rõ; original owner/cost và
exact annotation/evidence/request binding vẫn giữ. Chỉ thử bước thiếu khi có
consent và budget còn lại. Receipt reserved chặn phục hồi để đối soát, không
tự sửa ledger. Provider được tạo khi thực sự có call: Jev-only STOP không tạo
Groq, critic-only không tạo Typesafe, export không load cấu hình cả hai.
Source/handoff v5 giữ inventory/attempts/receipts từng owner; standalone verifier
replay included decision states, typed responses và logical plan mà không gọi API.

Workbench, HTTP và CLI `investigate --durable --planned --controller jev
--provider groq` dùng cùng service. Preview CLI không đọc env; initial Jev có
kiểm cấu hình riêng, recovery chỉ đọc khóa khi thực sự cần provider tương ứng.
Một admission dùng đúng source lease/segments đã sở hữu: persisted queued/running
sau crash được nhận là interrupted, không bị accounting re-read từ chối nhầm.
Test đã tái hiện lỗi này trước sửa rồi kiểm lại rules/Jev mà không rewrite source.

Toàn bộ **272 test frontend** pass. Browser QA tổng hợp đã chạy nguồn lỗi sau
retrieval đầu tiên tới completed; Trace giữ một quyết định Jev ở cha và ba quyết
định mới ở con. UI chi phí chỉ hiển thị ba receipt Jev mới, không cộng receipt
cha. Việt/Anh, dark/light, keyboard consent, dossier hai phiên và preview ZIP
tự thêm nguồn lỗi đã kiểm. Ba fixture cũ và actual Jev UI fixture vẫn đọc/
duplicate/export/verify handoff khi chặn config/Git/provider, không sửa source
bytes. Lượt hồi quy sau sửa đạt **207 test backend**; hai kiểm tra bàn giao cuối
cũng đạt sau khi siết kiểu JSON của cả sidecar Jev original và recovered. Count
float hoặc số `0` không thay thế được integer/boolean dù manifest được rehash.

Lượt rộng trước đó đạt 970 test và có một lỗi chờ: phiên check-followup thứ tám
hoàn tất sau khoảng 16 giây, vượt deadline 15 giây của test. Profiling cho thấy
replay cùng ancestry lặp lại giữa các helper. Một lần chuẩn bị fork nay dùng chung
memo theo thread trong đúng lượt đồng bộ đó, luôn xóa khi kết thúc hoặc lỗi.
Mọi guard trước/sau provider vẫn đọc nguồn mới, policy/hash/cycle checks giữ
nguyên. Test tám phiên đã đạt với deadline cũ; mutation giữa hai lượt chuẩn bị,
failure cleanup và cycle đều có hồi quy. Mục 8 đã nghiệm thu tại `ecd1233`:
[CI toàn bộ trên đúng commit thành công](https://github.com/NamTV2712/IssueIntel-Evidence-Grounded-Issue-Triage-Investigation-Agent/actions/runs/39613072345)
và [issue #8 đã đóng](https://github.com/NamTV2712/IssueIntel-Evidence-Grounded-Issue-Triage-Investigation-Agent/issues/8).

CI toàn bộ mốc fork `088aa61` đã
[thành công](https://github.com/NamTV2712/IssueIntel-Evidence-Grounded-Issue-Triage-Investigation-Agent/actions/runs/39562986985).
Các cổng chi tiêu/evaluation giữ nguyên; chỉ dùng transport tổng hợp ở mốc này.

Phạm vi này giữ nguyên proposal và các cổng dữ liệu/chi tiêu. Mỗi mã là một
công việc riêng; trạng thái chỉ chuyển hoàn tất sau code, synthetic checks và
nghiệm thu tương ứng. Sau khi tác giả cấp quyền Issues, đã tạo đủ 17 GitHub issue
ngày 19/10/2026: D01–D13 tương ứng #1–#13, V01–V04 tương ứng #14–#17 tại
[backlog GitHub](https://github.com/NamTV2712/IssueIntel-Evidence-Grounded-Issue-Triage-Investigation-Agent/issues).
D01 đã đóng với commit `c3f764e`; D02 đã đóng với commit `30fb2f0`; D03 đã đóng
với commit `7e9c5b0`. D05 đã đóng với commit `2773a8c`; CI của cả bốn commit
đều thành công. Mục chưa nghiệm thu vẫn mở.

Danh sách sản phẩm dưới đây dùng số mục 1–13 và mức ưu tiên P0/P1/P2,
đúng với backlog tác giả đã duyệt. Các mã D01–D13 trong ghi chép lịch sử và
GitHub issue tương ứng lần lượt với mục 1–13; chúng không phải nhóm ưu tiên khác.

| Mục | Ưu tiên | Công việc và nghiệm thu | Trạng thái |
|---|---|---|---|
| 1 | P0 | Hồ sơ issue, timeline, reported-versus-predicted delta, source-bound hypothesis links và correction. Backend/HTTP/browser phải giữ hashes, provenance và dữ kiện gốc. | Đã triển khai; `test_dossier.py`, `frontend_dossier.test.cjs`, GUI tổng hợp Việt/Anh/sáng/tối; source/request/hash/lineage, correction và không gọi API/Git đã kiểm |
| 2 | P0 | Preview nội dung/dung lượng, export bundle có manifest/hash, đọc/kiểm offline trên máy khác, không token/Git/model/full repo. | Đã triển khai: selection + required ancestry, deterministic ZIP, offline HTML/verifier và CLI; synthetic HTTP/isolated process/hash/tamper/unsafe ZIP/stale UI checks; không model/Git |
| 3 | P0 | Claim-level source spans, assumptions/counter-evidence, sufficiency rubric và gaps; semantic quality tách schema checks. | Đã triển khai claim-review sidecar product, exact Unicode spans, `claim_sufficiency_v1`, unreviewed fragments, API/UI Việt/Anh, owned receipts/intents và dossier/handoff; synthetic checks, chưa đánh giá semantic accuracy |
| 4 | P0 | Nghiệm thu App/HTTPS thật trên repo thử: install/sync/command/approved comment/dedup/restart/revoke/uninstall, giữ receipts. | Chưa nghiệm thu thật. Có relay chỉ nhận webhook đã ký, command admission theo connection/App/installation và GUID canonical; 174 kiểm local gồm native HTTP/dedup/restart/revoke đạt. Repo public bên ngoài chỉ dùng thử đọc; không thay nghiệm thu App/HTTPS hoặc comment thật |
| 5 | P1 | Planner phân biệt support/counter/caller/docs/history, gap-bound actions, waiting-for-user và explicit continuation trong cùng bounds. | Đã triển khai: typed retrieval `56de029`, Web rules v3 waiting/continuation `2773a8c`; synthetic integration/UI/handoff và review nguồn/budget/idempotency đã đạt, CI toàn bộ thành công; issue #5 đã đóng |
| 6 | P1 | Architecture/history facts có nguồn/revision, dependency invalidation và historical availability gate. | Đã triển khai/nghiệm thu tại `8bada45`, CI toàn bộ thành công: immutable store/CLI, owned UI/API, explicit bounded inference với intent/guards và saved provenance/dossier/handoff. Source thay đổi không sửa bản cũ; historical admission cần full proof trước cutoff, prior hypotheses chưa xác minh. Không có quality evaluation mới |
| 7 | P1 | Related/possible_duplicate/insufficient với mechanism/version và nguồn hỗ trợ/phản bác; không auto close/comment. | Đã triển khai/nghiệm thu tại `396fcc9`: sidecar bốn chiều, exact side-scoped refs, sufficiency tái tính, frozen pair/intent/receipts, API/UI và dossier/standalone handoff. Core 66, service 33, handoff 18, neighbor 76, legacy 145 và frontend 293 test đạt; browser QA/ZIP độc lập và CI toàn repo đạt; issue #7 đã đóng |
| 8 | P1 | Resume generation/retrieval/Planned rounds, fault injection bảo đảm reuse đúng stage/receipts và không implicit retry. | Đã nghiệm thu tại `ecd1233`: original Workbench/durable CLI Quick/Deep/Planned rules và Jev, answer continuations, năm loại fork và chuỗi stage-resume; journal/lease, source/policy guards, explicit consent, lineage/handoff và tương thích verifier/export cũ. Attempt/receipt giữ owner và budget xuyên phiên; 207 test hồi quy backend, 272 frontend và kiểm bàn giao/JSON type cuối đã đạt. CI toàn repo thành công; issue #8 đã đóng |
| 9 | P1 | Cost toàn lineage, phân biệt current/whole-chain, dedup receipt IDs, unknown và retained bounds riêng từng provider. | Đã nghiệm thu tại `300164a`: bounded read-only snapshot, native receipt replay, typed ancestor/source-bound artifact scope, immutable receipt ownership, API/UI và accounting bàn giao từng requested run. Source cuối đạt 174 test (102 core/43 collector/29 handoff), hồi quy rộng 416 và frontend 310; browser QA/ZIP độc lập và CI toàn repo đạt; issue #9 đã đóng |
| 10 | P1 | Browser E2E tổng hợp: create/follow-up/handoff/recovery, stale/navigation/draft/keyboard/mobile, không API thật. | Đã nghiệm thu tại `6c761e7`: optional Playwright/Chromium, real UI/HTTP/pipeline với native synthetic receipts, outbound guards và CI job riêng; exact-bound draft isolation/pending state, stale view status và keyboard/mobile. 9 browser/326 frontend test, default skip và independent review đạt; cả hai CI job thành công; issue #10 đã đóng |
| 11 | P2 | Product batch manifest/preview/status/cancel/partial success/restart idempotency qua pipeline chung, bounded concurrency. | Đã nghiệm thu tại `34552c1`: original product manifest/frozen server preview, fixed child IDs, shared pipeline/queue concurrency 1, partial success, durable pause/cancel/explicit continue và UI Việt/Anh. Contract 107, backend/integration cuối 26, HTTP 22, frontend 345 và toàn bộ 14 Chromium test đạt; hồi quy shared runner 114 đạt, fixes cuối có deterministic tests. Cả hai CI job tại run `39922088313` thành công; issue #11 đã đóng |
| 12 | P2 | Đối chiếu cited spans hai commits: unchanged/changed/missing/unresolved; không đồng nhất containment/release/fix proof. | Đã nghiệm thu tại `0c7b44c`: source-bound excerpts, sáu trạng thái, bounded exact-line search, captured witnesses/scope/limits, validator, API/export và UI Việt/Anh sáng/tối. Original/follow-up/recovery sources giữ authority; legacy vẫn đọc được. Đạt 73 core/integration, 74 shared regression, 350 frontend và 17 Chromium (các phạm vi có overlap), 3 ca trực tiếp kiểm lại sau sửa validator. CI `39956853347` thành công cả hai job; issue #12 đã đóng |
| 13 | P2 | Readiness trong app, disk usage và cleanup preview chỉ cho dữ liệu rebuildable; bảo vệ source/receipts và junction boundaries. | Đã nghiệm thu tại `36de11f`: shared runtime check với launcher, guarded provider/price inspection, read-only bounded sync/WAL và 17 capability conditions. Metadata scan có cận/partial/protected defaults/narrow cache preview, bỏ qua reparse/junction/device boundary, không xóa. API/UI Việt/Anh sáng/tối; 80 backend/quick-start, 108 shared regression, 28 HTTP/Workbench sau sửa backlog, 364 frontend và 20 Chromium đạt (3 ca trực tiếp kiểm lại sau sửa nhãn); hai symlink ca skip trên Windows, junction thật đã kiểm. Cả hai CI job run `39960756363` thành công; issue #13 đã đóng |
| V01 | Validation | Diagnostic accepted Main Dev: pinned comparative retrieval/graph/history/verifier/counter-search, localization/grounding/cost/latency. Giữ pilot và gold boundaries. | Chưa nghiệm thu chất lượng. Có [controlled component harness](COMPONENT_EVALUATION.md) dùng pipeline chung, strict prior openings, exact registration/authorization, full-stage reuse và scorer tách sau inference seal. Continuation riêng cho pilot lỗi transport khóa adapter ngoài checkout inference gốc, giữ failed audit/accounting và cổng đủ năm nhánh trước matrix. Synthetic checks không thay phép đo thực; diagnostic hai prompt cũ giữ nguyên phạm vi |
| V02 | Validation | Đo usefulness của câu hỏi/checks theo rubric; phân biệt model/user reports/human review và giới hạn nhãn. | Đã đo [pilot model-assessed trên 5 ca product](VALIDATION_V02_PILOT.md): 10 cặp, câu hỏi mới được ưu tiên 5/5; checks mới 3, baseline 1, một ca lỗi sinh không preference. Giữ 31 owned attempt/receipt và ca lỗi trong mẫu số, không retry. Một repo mẫu nhỏ/cùng model generator–judge; chưa nghiệm thu lợi ích developer, semantic accuracy hoặc kiểm người độc lập |
| V03 | Validation | So Jev/rules cùng công cụ/model/budget, decision/output quality, fallback/latency/USD. | Đang chuẩn bị [Planned v2 stage boundary và joint allowance](CONTROLLER_STUDY.md); chưa có registered runner/sealed comparison. Cần cấu hình và quyền chi tiêu Typesafe riêng; 4 smoke calls không cấp quyền tiếp |
| V04 | Validation | Pin phiên bản/protocol; explicit recorded authorization + open_test_split trước Holdout; Live đủ temporal proof mới được nhận. | Chưa mở; không tuning test/live hoặc chờ đủ 400 để phát triển product |

Thứ tự ưu tiên: 1 → 2 → 3 → 5 → 6 → 8. Mục 4 chạy khi có cấu hình
thật; các mục còn lại và diagnostic được triển khai/nghiệm thu theo dependencies.

V01 có thêm [common grounding assessment](COMPONENT_GROUNDING.md) sau full
125-cell effective inference seal: fresh judge chung, canonical public inputs,
exact source/phase authority, một call mỗi input độc nhất và mapping đủ năm arm.
Journal/receipt ownership, closed-return compatibility và inventory allowlist
ngăn che attempt hoặc tính lỗi thành review thành công. Report giữ selected 25,
paired reviewed subsets, case aliases và unique cost/timing; nhãn vẫn tự động,
semantic accuracy/causal/human unverified. Tooling/test không thay phép đo thật;
V01 quality gate vẫn mở.

## Review usefulness ngoại tuyến — chuẩn bị V02

`usefulness-build --selection ... --output ...` nhận JSON có đúng các trường
`schema: usefulness_selection_v1`, `purpose`, `protocol_id`, `language: en|vi`,
`slots`. Mỗi slot là một bundle câu hỏi hoặc check plan đã lưu, có đúng
`unit_id`, `condition_id`, `kind: question|check`,
`status: completed|empty|failed|unavailable`, `reason`, `source_job_id`,
`pack: {path, sha256}`, `artifact: {path, sha256}|null`. Cặp ba
unit/condition/kind phải duy nhất. Hash là SHA256 của **raw file bytes**, tách
khỏi seal JSON bên trong. Paths là workspace-relative JSON trong `artifacts/`;
không nhận absolute/parent traversal/link/reparse. Question export thường ở
`artifacts/demo/question_runs/<id>/questions.json`, check plan ở
`artifacts/demo/check_plan_runs/<id>/check-plan.json`, pack ở
`artifacts/demo/runs/<source_job_id>/pack.json`.

Completed/empty được replay qua native question/check source validators. Empty
chỉ nhận check plan có danh sách checks rỗng hợp lệ; question bundle rỗng không
hợp lệ. Failed/unavailable có pack product nhưng không artifact, bắt buộc reason;
đây là khai báo của organizer, **không** phải failure rate đo từ job/provider
receipt hay proof không tồn tại nguồn. Pack benchmark/strict/instance/split bị
từ chối, kể cả slot lỗi hoặc `split: null`. Gói giữ cả unit có annotation thiếu
khi không có proposal; không dùng restricted patch/test/human rationale.

Output phải là folder mới trong ignored `artifacts/`. `organizer/` giữ selection,
mapping, nguồn raw theo SHA và receipt; `reviewer/` giữ thứ tự/ID ngẫu nhiên đã
khóa, JSON, HTML offline và CSV. Mỗi card là **cả bundle**, không biến 1–4 câu
hỏi hoặc 0–3 checks thành nhiều case độc lập. Card giữ nguyên issue, hypotheses,
evidence và proposals cần chấm; ẩn condition/model/provider/job/local metadata,
recommendation và effort/ranking. Nội dung có thể vẫn làm lộ phương pháp, nên
không tuyên bố blind hoàn hảo. HTML escape nội dung, CSP chặn script/network;
chỉ đưa thư mục reviewer cho người chấm. Gói vẫn private, không tự gửi ai.

CSV có đúng cột `blind_id`, `display_sha256`, `specificity`, `grounding`,
`discrimination`, `feasibility`, `useful_next_action`, `rationale` và đủ từng ID
một lần. Điền `yes|no|uncertain` hoặc giữ trống; nhãn không trống cần rationale.
Không chấm card empty/failed/unavailable. `ratings.template.csv` là bản trống
bất biến; `ratings.csv` là bản có thể sửa. Import giữ exact UTF-8 bytes, không
đổi template hoặc suy nhãn cho ô trống.

Reviewer JSON có đúng `schema: usefulness_reviewer_v1`, `reviewer_id`,
`kind: human|agent|model`, `assistance: none|ai|unknown`,
`relationship: author|declared_independent|unknown`, `model_id: string|null`,
`reviewed_at` theo UTC và `declaration`. Agent/model hoặc human có AI assistance
cần model ID; human tự khai không đồng nghĩa identity/independence đã xác minh.
Import append-only và idempotent. Sửa nhãn cần `--supersedes <receipt SHA>` cùng
`--correction-reason`, giữ nguyên receipt cũ. Writer lock chặn import chồng;
kiểm cận trước publication để không tự tạo gói vượt giới hạn đọc.

Score kiểm lại source copies/frozen display/template và receipt chain, không
cần export gốc, Git, cấu hình hay provider. Mỗi reviewer được báo riêng theo
condition/kind, không gộp nhiều reviewer thành nhiều case. Có coverage theo
status, yes/no/uncertain/missing, known rate `yes/(yes+no)` hoặc null. Observed
bundle-yield bounds là `yes/all selected` đến
`(yes+uncertain+completed-but-blank)/all selected`; no-proposal slot góp 0 vào
yield nhưng không bị gán nhãn chất lượng âm. Missing và uncertain không bị đổi
thành no. Không tự tính agreement, paired contrast theo q1/check_1, causal
efficacy, benchmark performance hoặc independent human validation. Selection
post hoc không phải preregistration và không chứng minh coverage của quần thể.

Cận hiện tại: 100 slots, 8 MB/file, 64 MB nguồn/gói đã freeze và 64 MB receipt,
1.000 receipt đã công bố. Partial writes chưa công bố được giữ để kiểm tra;
không tự xóa. Hash và replay chỉ kiểm included-byte consistency, không xác thực
tác giả, full Git availability hay semantic correctness. Tooling đã kiểm bằng
fixture tổng hợp; chưa có nhãn người mới hoặc phép đo usefulness được nghiệm thu.
Source cuối đạt 290 kiểm tra local cho core/files/CLI và native question/check/
quick-start/relay regression. Bao gồm cận receipt, correction chain, dữ liệu
restricted/benchmark, actual Windows junction và subprocess Python `-I -S`
không site packages; không gọi API hoặc mở dataset thật.

## Kiểm nhận định (D03 — F08)

POST `/api/claim-reviews` yêu cầu đúng source job/pack SHA và confirmation
`generate_claim_review_with_api_cost`; không tự chạy khi mở Verification.
GET `/api/jobs/{source}/claim-reviews` chỉ đọc danh sách/availability. Sau khi tác
vụ hoàn tất, GET `/api/jobs/{review}/claim-review.json` hoặc `.md` replay cả intent,
authority và source/report/quote/rubric, trả header `X-IssueIntel-Report-SHA256` và
`X-IssueIntel-Claim-Review-SHA256` từ đúng export. Một intent mỗi pack; queued/
running/failed/interrupted/cancelled không có replacement paid attempt tự động.
Groq vẫn qua key, dated price, per-call bounds và ledger hiện hành; không Jev,
benchmark, Holdout/Live hoặc evaluation. Model/prompt/rubric/schema có digest,
old pack/report/check journals không bị sửa. UI cần explicit cost consent, chặn
double submit và late responses; đọc/xuất/đổi ngôn ngữ không gọi model.

Nhận định có type source_fact/mechanism/behavior_prediction/causal, exact statement
span, source support/counter và assumptions với exact citations khi model nói có
nguồn hỗ trợ. Nhãn model giữ riêng assessment sau cổng deterministic; lý do/gaps
và fragments chưa kiểm phải thấy được. Model-supported/counter judgments không
thành human/semantic/causal verification. Thiếu counter trong supplied scope chỉ
là giới hạn; không suy ra giả thuyết đúng. Test tổng hợp kiểm citation chính xác
nhưng cơ chế/giả định chưa đủ, causal claim, issue-only support, counter khác ngữ
cảnh, Unicode offsets, malformed output, receipt/failure/concurrency/restart,
stale navigation và quote tampering trong portable archive. Đây là chứng cứ cơ
chế/contract, chưa phải số đo cải thiện độ chính xác; V01/V02 vẫn cần nghiệm thu.
