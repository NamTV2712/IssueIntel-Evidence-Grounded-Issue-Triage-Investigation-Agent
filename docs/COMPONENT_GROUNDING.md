# Common grounding assessment — V01

Đây là phép đo **nhận xét tự động của model về evidence đã thấy**, bổ sung cho
localization, citation integrity và critic trong pipeline. Không gọi các nhãn
này là semantic accuracy, causal truth hoặc kiểm người độc lập.

## Đầu vào và ranh giới

Chỉ nhận continuation có full native effective inference seal cho đúng 25
accepted Main Dev gốc × năm nhánh. Parent plan, registration, ordered case IDs,
mọi arm SHA, source/config fingerprint và inference inventory phải replay hợp lệ.
Không nhập thêm task, mở Holdout/Live, đọc patch/test/human rationale hoặc đổi
split để dùng một service product. Pack và receipt inference giữ nguyên.

Scorer localization hiện có chỉ đo vị trí, citation integrity, counter citations
và các nhãn critic đã lưu. Những critic này xuất hiện trong một số treatment;
không lấy chúng làm phép chấm chung cho cả năm nhánh.

## Cùng một cách chấm

Tạo fresh common judge bằng frozen `verify_hypotheses()` cùng public input
projection, critic schema và native critique validator. Judge chỉ thấy
hypotheses/evidence đã khóa. Tên nhánh, verification cũ, receipts, cost, organizer
metadata và restricted targets không vào input. Wording của hypothesis/evidence
vẫn có thể gợi ý treatment; không tuyên bố blinding hoàn hảo.

Để ổn định giữa Python hash seeds, chỉ canonicalize key order/whitespace của
JSON object trong user message. Mọi string value, thứ tự list, system prompt và
schema giữ nguyên. Plan khóa exact rendered request cùng source hashes.

Hai arm có **messages + schema + judge config giống hệt** dùng chung một unit
và một receipt. Mapping vẫn giữ từng case/arm; mỗi case được alias phải kiểm
proof riêng. `plus_history` và `plus_verifier` thường dùng cùng hypotheses và
evidence, vì vậy common judge không thể chứng minh verifier cải thiện hypotheses
trong contrast này. Các case dùng chung unit cũng có thể tương quan.

Mọi unit dùng Groq `openai/gpt-oss-120b`, effort `medium`, output cap 2.048.
Tối đa một call/unit, không schema repair hoặc tự retry transport. Pilot gồm
mọi unit của case đầu, tối đa năm unit; chỉ khi tất cả được review hợp lệ mới
mở matrix. Số unit thực tế ≤125 được khóa trước gọi. Call/chi phí judge là phase
riêng; không thay cận inference năm call/arm hoặc 625 call toàn matrix.

## Thực thi và lưu trữ

CLI chạy adapter trong Python child isolated; toàn bộ native imports lấy từ
checkout inference cũ, adapter mới nạp bằng exact file. Plan khóa cả file đang
thực thi và frozen dependencies. Đăng ký/admission và final scoring replay
native parent đầy đủ. Trước/sau mỗi unit kiểm source/config, parent seal/
inventory, ủy quyền và proof của mọi current case mà unit đó đại diện.

Plan, phase authorization và pilot gate được kiểm trước credentials/provider.
Intent tồn tại trước construction/HTTP; callback giữ đúng owner của một receipt,
đối chiếu ledger, cấm dùng lại receipt inference hoặc unit khác. `reviewed` cần
đúng một receipt `settled` hoặc `missing_usage_bound`; `failed_bound` giữ failed.
Response được giữ private và kiểm cả wire schema lẫn native critique contract.
Raw HTTP response chưa chốt native review không được tự tái dựng/retry.

Thư mục study chỉ nhận plan, unit journals, execution intents, pilot gate, seal
và deterministic score đúng schema/tên/hash. File lạ hoặc journal chuyển sang
backup/scores làm admission thất bại. Không cất authorization hoặc ghi chú thủ
công vào thư mục study; giữ chúng ở đường dẫn private riêng trong `artifacts/`.
Mọi source/unit outcome, kể cả lỗi hoặc chưa rõ, phải được giữ để audit.

```powershell
python -m issueintel --root <frozen_checkout> component-grounding-preview --parent-plan <continuation_plan_sha256>
python -m issueintel --root <frozen_checkout> component-grounding-inspect --study <grounding_plan_sha256>
python -m issueintel --root <frozen_checkout> component-grounding-run --study <grounding_plan_sha256> --authorization artifacts/grounding-pilot.json --limit 1
# Cần phase authorization khớp và pilot đủ trước khi mở matrix:
python -m issueintel --root <frozen_checkout> component-grounding-run --study <grounding_plan_sha256> --authorization artifacts/grounding-matrix.json --limit 25
python -m issueintel --root <frozen_checkout> component-grounding-score --study <grounding_plan_sha256>
```

Run authorization `component_grounding_run_authorization_v1` khóa plan, parent
seal/registration, toàn selection/unit IDs, model/effort/output, phase và cận.
Nó ghi request/ngày nhưng không tự xác thực người khai hoặc tạo quyền chi tiêu.
Preview/inspect/score không đọc `.env`, tạo provider hay đọc gold; score ghi một
artifact kết quả mới sau full study seal, không sửa dữ liệu nguồn.

## Diễn giải báo cáo

Mẫu số selected giữ **25 case mỗi arm**, gồm source unavailable, judge failure,
unknown hoặc chưa được chấm. Phân bố `supported/unsupported/contradicted/uncertain`
và macro fractions dùng subset có review; báo denominator rõ. Paired differences
dùng subset cùng được review, không biến thành accuracy hoặc tác động nhân quả.
Tối đa 25 case clusters; 125 arm rows hoặc số hypothesis không phải số mẫu độc
lập. Không xác nhận riêng counter spans hoặc lợi ích thực tế cho lập trình viên.

Chi phí và thời gian tổng chỉ tính mỗi owned unit/receipt một lần. Per-arm timing
là thời gian của shared unit được tham chiếu, không cộng ngang các arm. Active
duration gồm wrapper/source guards/journal I/O, không phải network latency thuần.
Bound của unknown usage không phải hóa đơn đã xác minh. Nhãn model giữ bias
cùng họ model với generator và hoàn toàn tách khỏi human validation.

Đây là tooling và protocol V01; chỉ khi có run/report thật mới báo số đo. Fixture
tổng hợp kiểm source/admission, aliases, cap, crash/no-retry và metric integrity;
không dùng chúng thay kết quả chất lượng trên Main Dev.

Kiểm tra của mốc này: 63 test grounding và CLI liên quan đạt trên Windows,
gồm inventory/intent tampering, receipt của outcome lỗi, strict wire schema,
shared-unit accounting và isolated child imports. Review code độc lập không
phát hiện blocker mới; chưa có real common-judge run hoặc quality acceptance.
