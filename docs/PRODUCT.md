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
| F02 | Tìm ứng viên liên quan trong lịch sử local; CLI repository index thêm FTS trên issue/PR/comment đã đồng bộ. Chưa xác nhận duplicate, chưa tự đưa kết quả vào model. |
| F03 | Định vị file/symbol/dòng qua lexical, identifier và typed graph retrieval ở đúng snapshot. |
| F04 | Code evidence và revision; CLI đồng bộ issue/PR/comment, metadata PR–commit–file và đọc nguồn/hash. Điều tra tự động xuyên artifact trong workbench còn roadmap. |
| F05 | Job/trace/checklist có phiên bản; Deep thêm một vòng truy xuất theo giả thuyết rồi cập nhật đầu ra. Planner tổng quát nhiều tool/vòng còn roadmap. |
| F06 | `JevController` typed allowed actions và rules fallback đã có; remote transport chưa cấu hình/đánh giá. |
| F07 | Generation 3–5 hypotheses, support/counter IDs và graph descriptor qua shared pipeline. |
| F08 | Kiểm citation/hash, tìm thêm code theo giả thuyết ở Deep, critique riêng và abstention. Kết quả tìm vẫn là candidate, counter có thể `not_found`. |
| F09 | Intake chỉ ra mục cần bổ sung; tạo follow-up ở đúng commit với parent pack hash và chuỗi nguồn được replay. Context mới unverified, không kế thừa strict query. |
| F10 | Durable job/artifact receipts và plan ledger; mở lại kết quả/checklist; lỗi/interrupt không retry API ngầm. |
| F11 | Cache Git/snapshot/graph, follow-up lineage, saved-run retrieval và repository index có checkpoint/refresh thủ công. Auto-sync và memory có as-of proof còn roadmap. |
| F12 | Quick lấy evidence gọn; Deep mở rộng và có tối đa một lượt truy xuất/sinh bổ sung khi bật Groq. Routing benchmark giữ policy/pilot riêng. |
| F13 | Trạng thái theo stage và yêu cầu hủy; request/usage/cost qua ledger. |
| F14 | Lịch sử, mở lại pack và so sánh hai phiên cùng issue/repo: query/snapshot/proof bindings, evidence và hypotheses; điều tra tiếp với input mới vẫn tạo job riêng. |
| F15 | Nhận issue URL và webhook `issue_comment` HMAC-signed, collaborator command, delivery dedup; GitHub App chưa được cài/triển khai. |
| F16 | Preview comment có code links; exact-body approval, target binding và durable one-attempt intent trước POST. Không tự đăng. |
| F17 | Xem evidence/citations/hypotheses/verifier/trace và export JSON/Markdown. |
| F18 | Provider bounds, reservation/usage USD, lỗi và trace hiện có; output model vẫn unverified. |
| F19 | HTTP API, một worker, hàng đợi tối đa 4; benchmark CLI có concurrency bounded. |
| F20 | Revision/provenance rõ ràng; đối chiếu bản phát hành và xác nhận bản sửa còn roadmap. |

## API và dữ liệu local

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
- `POST /api/investigations`: tạo job với `mode`, `provider`, `depth` và input.
- `POST /api/comparisons`: `before_id`, `after_id` của hai job đã hoàn tất cùng issue/repository; kiểm hash cả hai export, không gọi model.
- `POST /api/related`: `job_id` đã hoàn tất; tìm ứng viên từ các job local, không nhận scope/split/query override.
- `GET /api/jobs`, `GET /api/jobs/<id>`: lịch sử/trạng thái.
- `GET /api/jobs/<id>/intake`: cue trong query có dòng/hash, áp dụng cả pack cũ mà không sửa export gốc.
- `GET/POST /api/jobs/<id>/plan`: checklist theo pack; ghi cần expected SHA256, step ID, status và note.
- `POST /api/jobs/<id>/cancel`: yêu cầu hủy với JSON `{}`.
- `GET /api/jobs/<id>/pack.json`, `.../report.md`: export kiểm hash.
- `GET /api/jobs/<id>/stages/<stage>`: tải nguyên bytes checkpoint đã ghi, kể cả job thất bại; kiểm path/hash và session.
- `POST /api/jobs/<id>/github-preview`: bản nháp comment từ job GitHub đã hoàn tất.
- `POST /api/jobs/<id>/github-publish`: body SHA256 và xác nhận cụ thể từ người dùng.
- `POST /api/github/webhook`: GitHub signature/event/delivery guards riêng; không tự gọi model hay đăng comment.

Mọi job/pack và repository ví dụ ở ignored `artifacts/demo/`. Không commit data,
token, review bundle hoặc kết quả thật. CI dùng fixture tổng hợp. Evaluation,
metrics và thí nghiệm Holdout vẫn cần capability/ủy quyền riêng theo proposal.

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
in refs đã lưu khi lỗi. Đây là audit/review, chưa có tự động resume hoặc tự tái
sử dụng paid output. Mở/tải không gọi model và không đổi bytes/hash checkpoint.

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
