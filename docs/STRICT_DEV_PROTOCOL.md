# Main Dev strict — eligibility v1

Yêu cầu cuối ngày 13/10/2026 thay thế yêu cầu mở rộng 500 ca: **chỉ kiểm đúng
400 Main Dev gốc**, giữ 100 Main Test và 300 Live Candidates. Sau khi đủ 400
trạng thái, freeze N ≤ 400 ca thực sự đạt strict thành `main-dev-strict-v1`.
Không hạ chuẩn, không lấy thêm task và không đổi DEV thành final test.
Proposal sản phẩm, bootstrap 800 và release V2 cũ không bị viết lại.

## Thay đổi kiến trúc

Trước: proof → snapshot + graph + gold localization → generation → critique →
judge → variants → release. Số release đủ artifact không phải số strict Main Dev.

Sau: original Main Dev public authority → ưu tiên provenance A/B/C → deterministic
proof replay + opening version → snapshot-only → eligibility receipt → audit đủ
400 → freeze N thật → optional DEV pilot/artifacts. Eligibility không gọi evaluator,
không đọc patch/human rationale và không tạo provider.

`StrictEligibilityResult(accepted, reasons, proof_refs)` là contract độc lập.
`check_proof` kiểm pure receipts; `process_candidate` kiểm source membership và
snapshot; `verify_eligibility_receipt` replay trước freeze hoặc sử dụng.
`verify_main_dev_cohort` kiểm partition chính xác 400 ca và replay mọi ca accepted.
Cờ strict cũ không đủ để được nhận.

## Nguồn và scope

Authority lấy đúng các dòng `split=main_dev` từ cases đã pin. Đối chiếu 400 ID
với nguồn Verified qua public Parquet columns, lọc theo instance_id Main Dev.
Không decode gold/hints/tests/human columns và không đọc Parquet Live. Hash bytes
của archive để kiểm nguồn không đưa restricted columns vào eligibility.

Case phải khớp nguyên bản, kể cả split, problem_statement và base revision.
Relabel Main Test/Live hoặc đưa ID mới vào bị từ chối trước acquisition.
Việc đọc metadata nguồn để kiểm membership không mở quyền inference/evaluation
cho holdout. Không đọc annotation contents của holdout hoặc Live.

Shared repositories, code và query identities có thể báo riêng;
không tự đổi semantics split hay lấy gold/performance để chọn ca. Standard
eligibility là provenance/query/code trước issue của chính task Main Dev.

Authority khóa source, parent, original membership và validator code; archive
code có hash được lưu ngay lúc đăng ký. Thay validator phải đăng ký pool mới;
không sửa proof/hash cũ. `data/development/` và `artifacts/` là private/ignored.
Các pool 500 cũ chưa freeze đã bị thay thế, không được dùng cho phase này.

## Strict proof

Issue identity phải là issue thật; ngày PR không thay ngày issue. Query GitHub
dùng opened payload có nested updated_at bằng created_at và chronology hợp lệ,
hoặc Trac version 0 đã replay raw HTML. Query là overlay riêng; source-human chỉ
áp dụng query upstream, không xác nhận query/hypotheses mới.

Selected commit là base hoặc ancestor đã kiểm raw Git parent chain. Independent
public availability phải **nhỏ hơn** issue opening. Git date chỉ chọn giờ tìm proof.
Snapshot kiểm commit/tree/source/blob hashes và query/source/parent/proof/split
bindings. GH Archive/API/Trac là trusted acquisition boundary; hashes không xác
thực lịch sử giả mạo. Bounded lookup không tìm thấy luôn pending, không chứng minh
absence hoặc maximum possible count. `benchmark_base` không thành strict.

## Resume, trạng thái và freeze

SQLite transaction lock theo case/acquisition/Git repo được OS giải phóng khi
worker chết. Git mutation/inspection khóa riêng; archive I/O độc lập có bounded
concurrency. Metadata/opening/selection/snapshot là stage riêng có content hash.
Receipt hợp lệ được replay/reuse; stage thiếu mới tiếp tục. Receipt bị sửa từ chối,
không thay expected hash để che lỗi. Completed LLM stages của recipe cũ không chạy
lại; phase eligibility không gọi LLM.

Windows progress dùng temp/fsync/replace retry/fallback; receipt critical vẫn fail
closed. Progress không xác nhận stage hoàn tất. Các ca runtime failure được ghi
`pending` cùng diagnostic riêng, không biến thành accepted.

```powershell
.\.venv\Scripts\python.exe -m issueintel build-strict-dev --namespace main-dev-strict-v1 --target 400 --resume --concurrency 6 --acquire
# Thêm --pool <SHA> để tiếp tục selection/code đã đăng ký.
# Bỏ --acquire để chỉ replay; --limit dành cho audit một phần, không freeze.
```

Report JSON phân hoạch accepted/pending/excluded của đúng 400 ca. Chỉ freeze khi
đã kiểm đủ 400, ngay cả khi N < 400. Frozen manifest có ordered IDs, proof refs,
source/snapshot/query bindings và exclusions gồm các ca pending/excluded.
Membership không đổi dưới cùng cohort hash; thêm proof về sau tạo cohort mới.
Freeze không mở evaluation authorization.

## Routing và chi phí

`main_dev_artifact_routing_v1`: deterministic → no LLM; simple semantic → 20b
medium; normal generation/critic → 20b high sau pilot theo stage; escalation →
120b high khi invalid output/low confidence/verifier disagreement/evidence conflict/
retry limit. Chặn artifact calls trước Main Dev cohort đã freeze. Pilot không dùng
holdout; chưa chạy pilot thì không tuyên bố 20b tương đương 120b hoặc chuyển bulk.
Variants hiện tại là deterministic, không cần model để xác định eligibility.

Giá uncached đọc ngày 13/10/2026 từ [Groq 20b](https://console.groq.com/docs/model/openai/gpt-oss-20b)
và [Groq 120b](https://console.groq.com/docs/model/openai/gpt-oss-120b), lưu riêng
configs/strict_dev_prices.json. Authorization mới giữ quyền Groq và quyết định
bỏ caps cũ; thiếu key/price vẫn chặn. Ledger có category/stage/effort/escalation;
reservation lỗi giữ cận. Không dùng provider khác/Jev và không mở evaluation.
