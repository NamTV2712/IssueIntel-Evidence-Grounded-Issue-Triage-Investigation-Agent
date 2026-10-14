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
| F01 | Nhận mô tả issue và repo/revision qua CLI/workbench; public input boundary. Phân loại intent nâng cao còn roadmap. |
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
| F14 | Lịch sử và mở lại pack local; so sánh đa phiên có kiểm rebinding còn roadmap. |
| F15 | GitHub bot thuộc giai đoạn sau. |
| F16 | Workbench chỉ đọc; GitHub write cần preview và approval riêng. |
| F17 | Xem evidence/citations/hypotheses/verifier/trace và export JSON/Markdown. |
| F18 | Provider bounds, reservation/usage USD, lỗi và trace hiện có; output model vẫn unverified. |
| F19 | HTTP API, một worker, hàng đợi tối đa 4; benchmark CLI có concurrency bounded. |
| F20 | Revision/provenance rõ ràng; đối chiếu bản phát hành và xác nhận bản sửa còn roadmap. |

## API và dữ liệu local

API cần session token của trang trong `X-IssueIntel-Token`. Origin/Host phải đúng
localhost; server không cung cấp CORS hay duyệt filesystem. Source excerpts được
render bằng `textContent`, không thực thi HTML/Markdown từ repository hoặc issue.

- `GET /api/catalog`: metadata accepted Main Dev; không chứa gold.
- `POST /api/investigations`: tạo job với `mode`, `provider`, `depth` và input.
- `GET /api/jobs`, `GET /api/jobs/<id>`: lịch sử/trạng thái.
- `POST /api/jobs/<id>/cancel`: yêu cầu hủy với JSON `{}`.
- `GET /api/jobs/<id>/pack.json`, `.../report.md`: export kiểm hash.

Mọi job/pack và repository ví dụ ở ignored `artifacts/demo/`. Không commit data,
token, review bundle hoặc kết quả thật. CI dùng fixture tổng hợp. Evaluation,
metrics và thí nghiệm Holdout vẫn cần capability/ủy quyền riêng theo proposal.

