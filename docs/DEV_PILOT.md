# Pilot Main Dev 20B/120B

Pilot chỉ nhận ca trong cohort `main-dev-strict-v1` đã replay, không mở evaluation.
Registration chọn 30–50 ca có generation input cũ và hypotheses cũ có hash hợp lệ,
chia vòng theo repository/ID. Từng excerpt phải khớp dòng code trong snapshot mới;
query phải khớp opening proof. Không đọc pack/localization/judged/human/holdout files.

```powershell
.\.venv\Scripts\python.exe -m issueintel register-dev-pilot --cohort <SHA> --count 30
.\.venv\Scripts\python.exe -m issueintel run-dev-pilot --pilot <SHA> --limit 1
.\.venv\Scripts\python.exe -m issueintel run-dev-pilot --pilot <SHA>
```

Hai arm dùng Groq 20B high và 120B high. Generation dùng cùng public input;
critic dùng cùng bộ hypotheses cũ để assessment IDs so được trực tiếp. Vì effort
và mục đích khác recipe 120B low cũ, output cũ không bị gán lại thành arm 120B high.
Input/code excerpts được tái sử dụng; các paid stage mới có receipt riêng và resume
bỏ qua stage đã lưu. Output chưa có receipt được giữ, không tự gọi model lần nữa.

Source/config/model/input/namespace binding đăng ký trước call; archive code có hash.
Mỗi call có durable reservation, usage và latency. Progress là convenience, không
phải bằng chứng hoàn tất. Provider access/rate/budget errors dừng worker; không retry
HTTP ngầm. JSON/schema repair dùng đúng một slot đã có của generator.

Ngưỡng đăng ký trước: validity ≥95%, citation integrity đủ, abstention agreement
≥85% cho generation, same-hypothesis critic agreement ≥80%. Đây là operational
gate cho tiếp tục phát triển trên DEV, **không chứng minh semantic equivalence**.
Citation set Jaccard không phải độ đúng nguyên nhân; critic là model assessment,
không phải human IAA. Báo riêng retry, invalid output, token, cost và latency từng stage/model.

Diagnostic outputs nằm trong ignored `data/development/pilots/`; không tự nhập vào
cohort hoặc annotation chính. Chuyển bulk cần policy riêng có kết quả pilot binding;
không tune theo holdout hoặc Live. Giữ nhãn automatic/unverified và không chế tạo
counter-evidence khi không tìm được.
