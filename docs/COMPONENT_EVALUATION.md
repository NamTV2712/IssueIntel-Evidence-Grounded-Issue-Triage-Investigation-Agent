# So sánh thành phần trên accepted Main Dev

Phần này chuẩn bị V01 trong backlog. Nó chạy pipeline điều tra chung với các
thành phần được chọn rõ, không dùng diagnostic hai prompt làm bằng chứng cho
graph/history/verifier. Đăng ký hoặc test tổng hợp thành công chưa chứng minh
chất lượng điều tra đã cải thiện. Đây là phép đo development, không phải kết quả
Holdout, Live, production hoặc so sánh với Copilot.

## Thiết kế được khóa

`configs/component_ablation_protocol.json` mặc định `execution_authorized: false`.
Lấy đúng 25 ca đầu theo round-robin repository rồi case ID trong accepted Main
Dev gốc, không chọn theo kết quả chạy. Không thay ca lỗi bằng ca khác. Những ca
này có thể đã được xem trong quá trình phát triển; không gọi đây là mẫu độc lập.

| Nhánh | Truy xuất đầu | Lịch sử | Verifier | Tìm thêm |
|---|---|---|---|---|
| `lexical_symbol` | Tối đa 12 lexical/symbol excerpts | Không | Structural | Không |
| `plus_graph` | Tối đa 8 seed + 4 graph excerpts | Không | Structural | Không |
| `plus_history` | Như `plus_graph` | Prior issue openings đủ proof | Structural | Không |
| `plus_verifier` | Như `plus_history` | Như trên | Một pass model riêng | Không |
| `plus_counter_search` | Như `plus_history` | Như trên | Một pass model riêng | Một vòng theo falsification test |

Graph vẫn được xây trong phần chuẩn bị chung của mọi nhánh. So sánh graph đo
**cách phân bổ retrieval dưới cùng cận 12 excerpts**, vì số seed đổi từ 12 sang
8; không diễn giải thành tác động riêng của cạnh graph với cùng seed. Code
excerpt tối đa 1.600 ký tự. Tìm thêm có tối đa ba query, sáu code excerpts mới
và một lượt generation bổ sung nếu có evidence mới. Đoạn tìm được mới là ứng
viên phản bác, chưa phải counter-evidence được xác nhận.

Mọi nhánh dùng `openai/gpt-oss-120b`, reasoning `medium`, output cap 2.048 token,
cùng schema/prompt và cận tối đa năm call mỗi nhánh kể cả output repair. Đây là
cùng cận sử dụng, không phải cùng chi phí thực tế. Không tạo call thừa để cân
chi phí. Thứ tự năm nhánh xoay theo chỉ số ca, một trial mỗi ca/nhánh; latency
phải báo cùng cache reuse và điều kiện chạy, không coi là benchmark độc lập.

## Lịch sử hợp lệ

Input tùy chọn là một JSON private trong `artifacts/`, ánh xạ selected case ID
sang danh sách receipt. Không nhận ID ngoài selection. Giới hạn mỗi ca: 20
candidate, 512 KiB mỗi receipt và 2 MiB tổng; toàn input tối đa 50 MiB. Raw input
và bản chuẩn hóa được giữ nguyên trong thư mục đăng ký để replay.

`component-history --output artifacts/component-history.json` có thể chuẩn bị
input từ opening receipts **đã có cục bộ của accepted Main Dev gốc**. Lệnh không
tải thêm, đọc gold hoặc đổi cohort. Candidate được kiểm proof, loại issue đích,
sắp theo thời điểm event giảm dần rồi URL/hash, và giữ tối đa 20 mỗi ca. Audit
báo phạm vi cache đã đọc và lý do không nhận; không coi cache là lịch sử đầy đủ.

Chỉ hỗ trợ title/body ban đầu của GitHub issue cùng repository, với receipt và
`IssuesEvent/opened` độc lập khớp identity/nội dung/hash. Cả ngày tạo issue và
ngày phát event đều phải **nhỏ hơn** cutoff của ca đích. Issue đích và mọi URL
query source của nó bị loại. Không suy đoán rename/transfer hoặc lấy body hiện
tại, PR/comment và timestamp chỉnh sửa làm proof ban đầu.

Chọn tối đa bốn nguồn đầu đủ điều kiện theo thứ tự candidate đã khóa, trích tối
đa 1.200 ký tự mỗi nguồn. Các nhánh tắt history cũng kiểm cùng danh sách nhưng
không đưa nội dung vào model. Receipt đầy đủ không gửi vào prompt. Coverage chỉ
thuộc danh sách đã chọn, không chứng minh đã tìm hết lịch sử. Nếu coverage bằng
0 thì contrast history không có thông tin; không kết luận history vô ích.

Chuẩn bị cục bộ ngày 24/10/2026 tìm được nguồn đủ proof cho 14/25 ca được chọn,
không chạm giới hạn quét, không gọi API và không đọc gold. Input/audit giữ
private trong `artifacts/experiments/`. Đây là coverage của cached receipts được
kiểm, chưa phải kết quả so sánh pipeline hoặc chứng minh không có lịch sử ở 11
ca còn lại.

## Đăng ký, chạy và tiếp tục

```powershell
python -m issueintel component-register
# Hoặc thêm --history artifacts/selected-history.json trước khi đăng ký.
python -m issueintel component-preflight --experiment <registration_sha256>
python -m issueintel component-inspect --experiment <registration_sha256>
```

Các lệnh này không đọc `.env`, tạo provider/ledger hoặc đọc gold. Đăng ký khóa
protocol, source/config fingerprints, model/giá có ngày, case/query receipt,
snapshot/graph và lịch sử. Preflight replay nguồn accepted gốc. Inspect vẫn
cho thấy đăng ký cũ đã stale; không sửa hash để cho phép chạy tiếp.
Inspect cũng đọc số nhánh/call đã lưu, trạng thái receipt và stage bị ngắt mà
không mở ledger hoặc credentials; nó không tự tiếp tục phiên lỗi.

`component-run` cần bản khai ủy quyền chính xác riêng, lưu private trong
`artifacts/`. Schema `component_ablation_run_authorization_v1` khóa registration,
code fingerprint, protocol, đủ ordered case IDs, provider/model/effort/output,
phase và cận call. Nó không kế thừa quyền từ diagnostic hai prompt. Bản khai có
request/ngày được ghi rõ; kiểm schema không xác thực danh tính người khai.

```powershell
python -m issueintel component-run --experiment <registration_sha256> --authorization artifacts/component-pilot-authorization.json --limit 1
# Chỉ sau khi pilot đủ năm nhánh hoàn tất và có quyền matrix tương ứng:
python -m issueintel component-run --experiment <registration_sha256> --authorization artifacts/component-matrix-authorization.json --limit 25
```

Admission kiểm ủy quyền trước khi đọc credentials, rồi provider vẫn kiểm key,
giá, model và budget thông thường. Không có quyền Holdout/Live, freeze mới,
bulk annotation restart hoặc chi tiêu Jev trong phép đo này.

Stage cache lưu toàn bộ output có nguồn và hash. Các nhánh có generation input
giống hệt dùng lại cùng annotation bytes, kể cả timestamp, và giữ receipt owner
ban đầu. Nhánh verifier bật/tắt vì vậy không sinh lại hai câu trả lời khác nhau.
Intent được ghi trước stage/call; call không rõ đã tính phí hoặc response chưa
chốt thành stage không tự chạy lại. Tiếp tục không đổi timestamp để tạo kết quả
khác, cộng lại receipt cũ hoặc bỏ ca lỗi khỏi mẫu số. Lỗi transport không được
biến thành quyền output repair.

## Chấm sau inference

Chỉ matrix đủ 25 × 5 trạng thái terminal mới tạo inference seal. Scorer là lệnh
riêng, cần đúng quyền `gold_scoring_authorized: true`, replay seal trước khi mở
gold Main Dev. Nó không mở Holdout hay Live:

```powershell
python -m issueintel component-score --experiment <registration_sha256> --authorization artifacts/component-matrix-authorization.json
```

Localization dùng thứ tự file xuất hiện đầu tiên trong code evidence của pack,
không phải xếp hạng nguyên nhân do model xác nhận. Báo Recall@5, Precision@5,
MRR và all-files@5 trên **mọi file gold patch**, kể cả file ngoài snapshot.
Nhánh thất bại giữ prediction rỗng và còn trong mẫu số; subset hoàn tất nếu có
phải trình bày riêng. Chỉ thay verifier không làm thay đổi retrieval localization.
Recall@18 là chỉ số phụ trên toàn cận 12 excerpts ban đầu + 6 tìm thêm, cùng số
file mới và số file gold xuất hiện trong phần thêm. Counter-search nối evidence
phía sau, không rerank, nên Recall@5 có thể giữ nguyên dù đã tìm được file mới.
Những chỉ số phụ này đo candidate coverage, không xác nhận phản bác đúng.

Citation integrity, model-cited counter coverage và nhận xét verifier phải được
phân biệt với semantic correctness, causal proof và kiểm người. Chi phí giữ
riêng phần charged/unknown/reserved, receipt owner và cache references. Thời gian
chuẩn bị, pipeline và provider không được gộp thành bằng chứng chất lượng.
Chênh lệch giữa hai nhánh là mô tả increment có điều kiện trong thiết kế này.

Raw inputs, outputs, receipts và báo cáo nằm trong ignored
`artifacts/experiments/component_ablation/`; không commit chúng. CI dùng fixture
tổng hợp để kiểm contract, admission, lỗi và reuse, không dùng dataset private
hoặc gọi model thật.
