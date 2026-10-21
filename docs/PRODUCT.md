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
| F02 | Tìm ứng viên liên quan trong lịch sử local; repository memory ở UI/CLI thêm FTS trên issue/PR/comment đã đồng bộ. Chưa xác nhận duplicate, chưa tự đưa kết quả vào model. |
| F03 | Định vị file/symbol/dòng qua lexical, identifier và typed graph retrieval ở đúng snapshot. |
| F04 | Repo đã kết nối tự truy xuất tối đa 6 nguồn; người dùng cũng có thể tìm/chọn chính xác nguồn đã đồng bộ để tái điều tra cùng query/commit. Record, receipt và repository ID được replay; dùng trong generation/critique/export. Current context không phải temporal proof. |
| F05 | Job/trace/checklist có phiên bản; nhật ký quan sát theo từng saved check, điều kiện/mong đợi/quan sát và commit tự báo, đính chính append-only cùng nguồn. Deep thêm một vòng; Planned có typed gap actions. Web rules v3 đã có persisted waiting và explicit continuation giữ budget toàn chuỗi, đang chờ nghiệm thu D05; tool điều tra mở rộng còn roadmap. |
| F06 | Adapter Typesafe nối tùy chọn vào Planned: bounded public context, choice allowlist, tối đa bốn quyết định, cấu hình/ledger/receipt riêng, fallback được ghi và replay. Đã kiểm kết nối thật 4/4 call tổng hợp ngày 17/10/2026 theo quyền thử riêng; chưa đánh giá chất lượng controller hoặc luồng GitHub thật. Cấu hình thường vẫn tắt/trần 0. |
| F07 | Generation 3–5 hypotheses, support/counter IDs; đồ thị và bảng đối chiếu từng cặp từ báo cáo lưu sẵn. Product có kế hoạch bổ sung 0–3 kiểm tra phân biệt, điều kiện kiểm soát, kết quả A/B và dự đoán A/B/unknown cho từng giả thuyết. Gợi ý theo số cặp A/B do model nêu và công sức ước tính; chưa đo chất lượng phân biệt hoặc suy ra nguyên nhân. |
| F08 | Kiểm citation/hash, tìm thêm code ở Deep/Planned, critique riêng và abstention. Bảng đối chiếu lộ các mục thiếu từ metadata và nhận xét model cần xem lại; chưa coi đây là đánh giá sufficiency/nguyên nhân. Planned dừng rõ khi query/evidence không đổi; counter có thể `not_found`. |
| F09 | Câu hỏi intake theo mẫu và câu hỏi riêng từ issue/code/giả thuyết đã lưu; Việt/Anh, có lý do hỏi/cách dùng câu trả lời/refs. Nhận trả lời một phần/“chưa biết”, khóa và replay nguồn/Q&A. Planned Web rules v3 có câu hỏi trong trạng thái chờ, continuation cùng commit không reset budget; generic follow-up bị chặn khi đang chờ. Tạo câu hỏi thích nghi qua một paid attempt được duyệt riêng; chất lượng phân biệt và dữ kiện mới vẫn unverified. |
| F10 | Durable job/artifact receipts, plan ledger và repository page checkpoints; product có verifier/export recovery riêng. Original Workbench/durable CLI Quick/Deep/rules Planned và chuỗi stage-resume có preview/consent, owned prefix/lease, inherited attempt bounds, lineage và portable handoff. Mỗi nguồn giữ một intent; mục 8 còn thiếu phục hồi lỗi của Jev/follow-up/answer continuations và nghiệm thu toàn phạm vi, không retry API ngầm. |
| F11 | Cache Git/snapshot/graph, follow-up lineage, saved-run retrieval; kết nối tự backfill/tiếp tục, webhook cập nhật canonical và tracked branch. Memory có as-of proof còn roadmap. |
| F12 | Quick lấy evidence gọn; Deep thêm tối đa một lượt. Planned chọn một đến ba vòng theo dấu hiệu file/gap/counter còn thiếu từ seed; tối đa chín Groq calls kể cả repair. Web rules v3 kế thừa root route/limits và counts qua continuation; CLI/Jev giữ v2, library mặc định v1. Đây là heuristic quan sát, chưa phải đánh giá độ khó/chất lượng. Routing benchmark giữ policy/pilot riêng. |
| F13 | Trạng thái/hủy, token và USD theo phiên từ receipt riêng; đang chạy/lỗi/interrupted vẫn xem được cận giữ chỗ. |
| F14 | Lịch sử, mở lại pack và so sánh hai phiên cùng issue/repo: query/snapshot/proof bindings, evidence và hypotheses; điều tra tiếp với input mới vẫn tạo job riêng. |
| F15 | Issue URL/collaborator command; HMAC-signed installation/repository events tự onboarding public repos và incremental sync có durable inbox/dedup. App/HTTPS relay thật chưa cấu hình trên máy. |
| F16 | Preview comment có code links; exact-body approval, target binding và durable one-attempt intent trước POST. Không tự đăng. |
| F17 | Tổng quan và JSON/Markdown/nháp GitHub dùng chung report contract; có Markdown bàn giao kèm mọi quan sát/đính chính thủ công và JSON journal riêng. Khóa nguồn và version; phân biệt model critique, citation, user report và kiểm người. Đồ thị có lọc/keyboard/citation; không tự đưa journal vào nháp GitHub hoặc model. |
| F18 | Reservation trước HTTP gắn về job, cập nhật usage/price date sau call; tab chi phí/export riêng, không nhận tổng ledger thành chi phí phiên. Phiên cũ thiếu receipt giữ unknown. |
| F19 | HTTP API, một worker, hàng đợi tối đa 4; benchmark CLI có concurrency bounded. |
| F20 | Đối chiếu commit/tag, diff evidence và ancestry local; kiểm riêng một GitHub Release công khai theo tag với commit tham chiếu, giữ ID/receipt/hash và phân biệt draft/prerelease. Quan hệ hiện tại không chứng minh nội dung tag lúc công bố hoặc lỗi đã sửa. |

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

- `GET /api/catalog`: metadata accepted Main Dev; không chứa gold.
- `GET /api/jobs/{id}/check-journal.json` hoặc `.md`: journal source-bound hoặc toàn report kèm journal; hai header `X-IssueIntel-Report-SHA256` và `X-IssueIntel-Journal-SHA256` bind đúng body. Chỉ product ngoài benchmark và lineage đã replay; không network/model.
- `POST /api/jobs/{id}/check-journal`: `{expected_sha256, observation}` gồm `check_id`, `outcome`, `conditions`, `expected_result`, `observed_result`, `interpretation`, `tested_commit_sha` nullable và `supersedes` nullable. `not_run` cần lý do, cấm observed result/tested SHA; các outcome khác cần điều kiện, mong đợi và quan sát. Đính chính cùng check còn active, tối đa 100 event; xung đột trả input error, tải lại để giữ bản nháp và xem phiên bản mới.
- `POST /api/investigations`: tạo job với `mode`, `provider`, `depth` và input.
- `POST /api/comparisons`: `before_id`, `after_id` của hai job đã hoàn tất cùng issue/repository; kiểm hash cả hai export, không gọi model.
- `POST /api/versions`: `job_id`, `revision`, tùy chọn `reference_revision`; job đối chiếu code và ancestry dùng chung queue/worker, không model hoặc thực thi code/test.
- `GET /api/jobs/<source>/versions`: tối đa 20 lượt đối chiếu của báo cáo nguồn.
- `GET /api/jobs/<comparison>/versions.json`: export riêng có hash và binding về pack nguồn.
- `POST /api/releases`: `job_id`, `tag` và `reference_commit` full SHA; kiểm đúng một release của nguồn GitHub product, không gọi model/tải Git history.
- `GET /api/jobs/<source>/releases`: tối đa 20 lượt kiểm release đã lưu, kể cả lỗi/hủy.
- `GET /api/jobs/<check>/releases.json`: export quan sát có receipt và binding về source pack/query/snapshot; replay không network/model.

- `POST /api/related`: `job_id` đã hoàn tất; tìm ứng viên từ các job local, không nhận scope/split/query override.
- `POST /api/context-search`: `job_id`, `query`; tìm tối đa 20 nguồn trong index của đúng repo GitHub của báo cáo, không network/model.
- `POST /api/investigations` với `mode: context`: `parent_job_id`, `parent_pack_sha256`, `selected_sources` (1–6 bộ key/record_sha256/receipt_sha256), `provider`, `depth`; giữ query/snapshot nguồn, không nhận repo/revision/query override.
- `GET /api/jobs`, `GET /api/jobs/<id>`: lịch sử/trạng thái.
- `GET /api/jobs/<id>/intake`: cue trong query có dòng/hash, áp dụng cả pack cũ mà không sửa export gốc.
- `GET /api/jobs/<id>/evidence-graph`: đồ thị từ citation đã replay, hash-bound về pack; không gọi model hoặc đọc nguồn mới.
- `GET /api/jobs/<id>/usage`: metadata receipt theo phiên, bao gồm job lỗi/interrupted; token/USD charged-or-reserved, không phải hóa đơn provider.
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
- `GET /api/jobs/<id>/stage-recovery`: explicit preview của stopped original product journal hoặc stage-resume descendant; replay source/prefix với zero provider calls, trả completed/next stage, inherited attempt bound, binding SHA, `requires_api`, root job ID và source job IDs.
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
issue đích; chưa có semantic duplicate judgment hoặc đồng bộ toàn GitHub history.

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

Write adapter pin API version `2026-03-10`, kiểm HTTP 201, exact body và comment
URL/ID từ response. Nguồn đã đối chiếu ngày 14/10/2026:
[Create issue comment](https://docs.github.com/en/rest/issues/comments#create-an-issue-comment)
và [Webhook validation](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries).

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
CLI `investigate --planned` và Jev giữ v2, API Python giữ v1 mặc định. V1/v2 đã
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

Mục 8 đang có lõi journal/replay opt-in trong pipeline chung. Fault injection đã
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

Mục 8 vẫn chưa nghiệm thu toàn bộ: custom/Jev,
follow-up và phục hồi lỗi của chính answer continuation còn thiếu.
Luồng trả lời đã có thể nhận recovered waiting parent; điều này không
cấp quyền resume tùy ý những phiên con đó hoặc bỏ cổng chi tiêu/evaluation.

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
| 4 | P0 | Nghiệm thu App/HTTPS thật trên repo thử: install/sync/command/approved comment/dedup/restart/revoke/uninstall, giữ receipts. | Chờ cấu hình App/HTTPS và nghiệm thu thật |
| 5 | P1 | Planner phân biệt support/counter/caller/docs/history, gap-bound actions, waiting-for-user và explicit continuation trong cùng bounds. | Đã triển khai: typed retrieval `56de029`, Web rules v3 waiting/continuation `2773a8c`; synthetic integration/UI/handoff và review nguồn/budget/idempotency đã đạt, CI toàn bộ thành công; issue #5 đã đóng |
| 6 | P1 | Architecture/history facts có nguồn/revision, dependency invalidation và historical availability gate. | Đã triển khai/nghiệm thu tại `8bada45`, CI toàn bộ thành công: immutable store/CLI, owned UI/API, explicit bounded inference với intent/guards và saved provenance/dossier/handoff. Source thay đổi không sửa bản cũ; historical admission cần full proof trước cutoff, prior hypotheses chưa xác minh. Không có quality evaluation mới |
| 7 | P1 | Related/possible_duplicate/insufficient với mechanism/version và nguồn hỗ trợ/phản bác; không auto close/comment. | Chưa triển khai |
| 8 | P1 | Resume generation/retrieval/Planned rounds, fault injection bảo đảm reuse đúng stage/receipts và không implicit retry. | Đang triển khai: core, original Workbench/durable CLI và chuỗi stage-resume có journal/attempts/lease, explicit consent, source/policy guards và lineage/handoff; tương thích verifier/export cũ. Trả lời sau recovered waiting giữ failed attempt debit qua nhiều phiên. Còn phục hồi lỗi của Jev/follow-up/answer continuations và nghiệm thu toàn mục |
| 9 | P1 | Cost toàn lineage, phân biệt current/whole-chain, dedup receipt IDs, unknown và retained bounds riêng từng provider. | Chưa triển khai |
| 10 | P1 | Browser E2E tổng hợp: create/follow-up/handoff/recovery, stale/navigation/draft/keyboard/mobile, không API thật. | Chưa triển khai |
| 11 | P2 | Product batch manifest/preview/status/cancel/partial success/restart idempotency qua pipeline chung, bounded concurrency. | Chưa triển khai |
| 12 | P2 | Đối chiếu cited spans hai commits: unchanged/changed/missing/unresolved; không đồng nhất containment/release/fix proof. | Chưa triển khai |
| 13 | P2 | Readiness trong app, disk usage và cleanup preview chỉ cho dữ liệu rebuildable; bảo vệ source/receipts và junction boundaries. | Chưa triển khai |
| V01 | Validation | Diagnostic accepted Main Dev: pinned comparative retrieval/graph/history/verifier/counter-search, localization/grounding/cost/latency. Giữ pilot và gold boundaries. | Chưa nghiệm thu; tái dùng diagnostic hợp lệ khi phù hợp |
| V02 | Validation | Đo usefulness của câu hỏi/checks theo rubric; phân biệt model/user reports/human review và giới hạn nhãn. | Chưa nghiệm thu |
| V03 | Validation | So Jev/rules cùng công cụ/model/budget, decision/output quality, fallback/latency/USD. | Cần cấu hình và quyền chi tiêu Typesafe riêng; 4 smoke calls không cấp quyền tiếp |
| V04 | Validation | Pin phiên bản/protocol; explicit recorded authorization + open_test_split trước Holdout; Live đủ temporal proof mới được nhận. | Chưa mở; không tuning test/live hoặc chờ đủ 400 để phát triển product |

Thứ tự ưu tiên: 1 → 2 → 3 → 5 → 6 → 8. Mục 4 chạy khi có cấu hình
thật; các mục còn lại và diagnostic được triển khai/nghiệm thu theo dependencies.

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
