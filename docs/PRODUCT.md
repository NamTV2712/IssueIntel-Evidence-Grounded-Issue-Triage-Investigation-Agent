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
| F01 | Nhận mô tả issue, GitHub issue URL và repo/revision qua CLI/workbench; public input boundary. Phân loại intent nâng cao còn roadmap. |
| F02 | Tìm kiếm và duplicate có bằng chứng còn roadmap. |
| F03 | Định vị file/symbol/dòng qua lexical, identifier và typed graph retrieval ở đúng snapshot. |
| F04 | Code evidence, query/proof nguồn và revision đã có; điều tra động issue/PR/tài liệu chưa có trong workbench. |
| F05 | Trạng thái job và trace; kế hoạch kiểm tra lấy từ hypotheses. Planner nhiều vòng còn roadmap. |
| F06 | `JevController` typed allowed actions và rules fallback đã có; remote transport chưa cấu hình/đánh giá. |
| F07 | Generation 3–5 hypotheses, support/counter IDs và graph descriptor qua shared pipeline. |
| F08 | Kiểm citation/hash, critique riêng và abstention. Counter evidence có thể `not_found`; không bịa quan sát. |
| F09 | Báo bước kiểm tra/thông tin còn thiếu; hội thoại follow-up có provenance còn roadmap. |
| F10 | Durable job/artifact receipts; mở lại kết quả; lỗi/interrupt không retry API ngầm. |
| F11 | Cache Git/snapshot/graph đã có; kiến trúc memory nhiều phiên còn roadmap. |
| F12 | Quick/Deep điều chỉnh số code evidence; routing model của benchmark giữ policy/pilot riêng. |
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
- `GET /api/jobs`, `GET /api/jobs/<id>`: lịch sử/trạng thái.
- `POST /api/jobs/<id>/cancel`: yêu cầu hủy với JSON `{}`.
- `GET /api/jobs/<id>/pack.json`, `.../report.md`: export kiểm hash.
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
