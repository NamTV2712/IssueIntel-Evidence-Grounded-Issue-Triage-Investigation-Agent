# Artifacts trên Main Dev strict

Milestone A kiểm đúng 400 Main Dev gốc, không gọi LLM và freeze
`main-dev-strict-v1`: 293 accepted, 107 pending, 0 excluded. Cohort này không thay
membership khi một annotation thất bại. 100 Main Test và 300 Live Candidates vẫn
giữ split ban đầu; phase này không đọc annotation của chúng hoặc mở evaluation.

`benchmarks.dev_artifacts` xây graph/retrieval bằng primitive dùng chung. Gold
localization là nhánh riêng: đọc Parquet được lọc theo instance ID của **accepted
Main Dev**, giữ tọa độ chưa đối chiếu ở trạng thái không khả dụng. Generation và
critique chỉ replay public query/code; không đọc localization, patch, tests hoặc
source-human rationale. Nhãn người gốc của Verified không xác nhận hypotheses mới.

```powershell
.\.venv\Scripts\python.exe -m issueintel prepare-dev-artifacts --cohort <SHA_cohort>
.\.venv\Scripts\python.exe -m issueintel register-dev-artifacts --cohort <SHA_cohort>
.\.venv\Scripts\python.exe -m issueintel run-dev-artifacts --recipe <SHA_recipe> --limit 1
.\.venv\Scripts\python.exe -m issueintel run-dev-artifacts --recipe <SHA_recipe>
```

Registration replay receipt của paired pilot 30 ca và hash source/config archive.
Không dùng boolean `pilot_approved` do người gọi tự đặt làm bằng chứng cho batch.
Trong transport hiện tại, generation 20B đạt 24/30 đầu ra hợp lệ, critique 19/30;
120B đạt 30/30 ở từng stage. Vì 20B không đạt ngưỡng validity 95% đã đăng ký,
`configs/dev_artifact_policy.json` giữ 20B high là attempt đầu tiên. Chỉ fallback
120B high khi **ca đang chạy** có receipt lỗi structured output, với reason
`invalid_structured_output`. Lỗi của ca pilot khác không đủ để bỏ attempt 20B.
Strategy fallback dựa trên paired pilot, không tuyên bố 20B độc lập đạt gate.
Đây không phải xác nhận
120B đúng nguyên nhân hơn; model agreement trên các cặp hợp lệ có selection bias.

Routing tổng quát vẫn chọn 20B high cho normal investigation, 20B medium cho
simple semantic task. Simple semantic và reference judge chưa có pilot riêng nên
bulk bị chặn. Thay transport/effort/prompt/model cần recipe mới; không sửa receipt
cũ hoặc gọi lại diagnostic output thành nhãn production.

Generation và critique có receipt riêng, input/snapshot/eligibility/model/code
bindings và danh sách reservation ID. Resume kiểm hash, dùng lại stage hợp lệ,
không tính lại phí cho cache hit. Failure được giữ nguyên; started intent hoặc
output chưa có receipt không kích hoạt paid retry ngầm. Giới hạn hiện tại: một
attempt mỗi model/stage, tối đa 20B rồi 120B; generation có tối đa một schema
repair trong mỗi attempt. Mỗi attempt có receipt riêng; crash sau receipt 20B
không làm gọi lại 20B khi resume fallback. Network/access/rate/budget failure
không được đổi thành lý do structured fallback.
401/403/429 hoặc BudgetDenied dừng frontier của worker, không queue cả cohort.

`investigation.pipeline.investigate()` replay excerpt cache đối với bytes snapshot
thật và receipt query riêng của Main Dev. `finish_pack()` lắp hypotheses/verifier
đã có receipt thành investigation pack cùng schema với product. Export JSON/MD
ở nhánh private, không tạo benchmark metrics. Shared pipeline không nhận gold.

Controlled variant hiện tại xóa hợp các code-support ID do generator trích dẫn.
Criticality chưa được người kiểm; expected abstention là mục tiêu tổng hợp,
`validation_status=unreviewed`. Không chế tạo counter-observation thật hoặc gán
nhãn human cho model output. Counter evidence không tìm được vẫn `not_found`.

```text
data/development/artifacts/<cohort>/<case hash>/
  generation_inputs.json        public model boundary
  graph.json.gz                 text lấy lại từ snapshot khi replay
  localization.restricted.json  chỉ dataset construction
  receipt.json
data/development/artifact_recipes/
  registrations/<recipe>.json
  sources/<source archive>.zip
  outputs/<recipe>/<case hash>/
    generation/                 started intent, call journal, output, receipt
    critique/                   started intent, call journal, output, receipt
    variants.json
    investigation/pack.json, report.md
    completion.receipt.json
artifacts/main_dev_artifacts/     tiến độ/báo cáo tiện xem, không là source of truth
```

Toàn bộ dữ liệu trên local/private/ignored. Ngân sách vẫn có dated prices, output
bounds và durable ledger; unlimited chỉ dựa trên quyết định tác giả được ghi.
Chi phí báo là charged/reserved cận trên, không phải hóa đơn. Không có human IAA
hay holdout evaluation trong milestone này.
