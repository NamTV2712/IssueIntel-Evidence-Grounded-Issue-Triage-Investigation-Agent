# So sánh controller có đăng ký — V03

So sánh rules/Jev cần cùng Planned recipe, model sinh, snapshot, công cụ và
ngân sách gồm cả controller. Web hiện dùng rules v3 và Jev v2; không lấy hai
default này làm hai treatment. Harness dưới đây khóa một experiment riêng;
chưa có kết quả Jev tốt hơn rules hoặc quyền gọi thêm Typesafe.

## Stage boundary cho product

`investigation.product_study` tạo `product_controller_study_policy_v1` bằng
`make_policy()`; `pipeline.investigate()` nhận cặp tham số tùy chọn
`product_study_policy`/`product_study_stage_executor`.

Chỉ nhận case GitHub issue public canonical, revision chính xác và snapshot
`benchmark_base`, không benchmark split/instance, prepared evidence, historical
proof, recovery hoặc continuation. Recipe cố định Planned v2, rules hoặc native
Typesafe Planned controller, Groq 120b low/4.096, top-k 12, graph expansion và
independent critic. Policy khóa full case/snapshot/graph/parser, native source
hashes, recipe và digest runtime/source do caller cung cấp. Nó kiểm tính nhất
quán, không tự chứng minh nguồn public đã được tải đúng hoặc cấu hình opaque
provider thực sự khớp digest khai báo.

Executor nhận stage name, exact binding và native `produce` callback, phải trả
full native payload. Retrieval được replay deterministic; generation vẫn qua
native Annotation/provenance/evidence validation; verifier kiểm structural
projection và strict critic schema. Guard trước/sau mỗi stage, model call kể cả
output repair, và decision boundary phát hiện thay đổi source/context.

Cache ở runner chỉ được dùng khi **toàn bộ** operation input/recipe khớp. Giữ
nguyên annotation timestamp và repair count: native planner tính logical calls
từ `1 + repair_count`, nên shared seed không được miễn lượt logical. Payload
của call HTTP riêng lẻ chưa phải một native stage đã hoàn tất. Cần giữ stage
owner, mọi call/receipt liên quan và reference của từng arm ngoài pack gốc.

Lối này tách khỏi strict Main Dev experiment gate và product recovery tapes.
Không thay defaults của Workbench/CLI, không mở đường đưa benchmark case sang
product, không bỏ kiểm của V01. Không cấu hình key hoặc khởi động API chỉ bằng
cách tạo policy.

## Ngân sách chung trước provider

`runtime.study_budget.StudyBudgetLedger` giữ policy finite theo study/provider/
model/config hash/price hash/ngày, cận attempt và USD của từng arm cùng tổng
paid owner. Caller phải xác minh giá có ngày và tính đúng cận native trước
`reserve()`; lớp này không tự lấy giá, tải cấu hình hoặc cấp quyền chi tiêu.

- `reserve(...)`: lưu intent và cận chi phí trước construction/HTTP. Request đã
  có owner không được reserve lại để retry; schema repair có request và ordinal
  riêng nhưng vẫn tiêu attempt.
- `publish_receipt(...)`: kiểm native Groq/Typesafe contract, model, bound và
  reservation ID duy nhất. Terminal receipt không bị viết lại. Breach được giữ
  và khóa call tiếp theo; thiếu receipt vẫn giữ nguyên conservative bound.
- `finish(...)`/`close_unknown(...)`: giữ outcome riêng với receipt. Chỉ
  completed có returned-compatible receipt và payload SHA; không suy thành công
  từ raw response, chi phí settled hoặc status của provider.
- `reference(...)`: phân bổ chi phí của call terminal sang arm khác, không gọi
  API và không cho phép dùng lại payload. Shared successful stage phải mang cả
  call lỗi trước output repair; không miễn chi phí đó cho arm thứ hai.
- `inspect()`: tổng paid owner dedup theo call và logical arm references riêng;
  không cộng ngang logical costs thành chi tiêu thực.

Groq receipt settled hợp lệ có thể không lưu observed input/output khi native
call không có context row. Giữ charged total và directional observations
unknown; không tự chia token. Native provider/account limits vẫn là cổng bổ sung.
Lỗi ledger là `StudyBudgetStop(OSError)`, để caller truyền ra ngoài fallback
controller. Provider wrapper còn phải bảo đảm lỗi callback bị native client bọc
lại không biến thành quyền gọi tiếp sau integrity stop.

Event files fsync và SQLite `BEGIN IMMEDIATE` kiểm cap atomically qua hai
provider, giữ exact inventory/hash chain/index. Crash sau fsynced event được
replay vào index, không tạo lại paid call. Đây là kiểm tính nhất quán local,
không chữ ký tác giả, hóa đơn hoặc bảo vệ trước coordinated rollback của cả
journal và index. Constructor/inspect có thể mở transaction để replay index;
không phải API chỉ đọc filesystem tuyệt đối.

## Selection và registration

`evaluation.controller_study` nhận đúng năm original GitHub product inputs đã
lưu bởi Workbench, không tải issue mới để thay nguồn giữa hai arm. Selection:

```json
{
  "schema": "controller_study_selection_v1",
  "purpose": "paired_product_controller_comparison",
  "protocol_id": "controller_rules_jev_v1",
  "cases": [
    {"unit_id": "case-01", "source_job_id": "<24 hex>",
     "pack": {"path": "artifacts/demo/runs/<24 hex>/pack.json", "sha256": "<raw file SHA256>"}}
  ]
}
```

`cases` cần đủ năm dòng với unit/job/canonical issue khác nhau. Native job phải
completed, mode GitHub và issue URL khớp pack. Không dùng bản export sao chép
làm nguồn thay job gốc. Benchmark split/instance, history, context selection,
follow-up, recovery và strict evidence bị chặn. Source loader thật replay Git
objects/cache đã có và dựng lại graph; không fetch hoặc chạy code repository.
Workbench có thể khởi tạo metadata local, nên registration không được mô tả
là thao tác chỉ đọc mọi byte filesystem.

Registration khóa raw selection/pack/job/config/prices, full Python source
inventory, native recipe và full case/snapshot/graph/parser policy. Study ID
và inner registration SHA có vai trò riêng. Thay source hoặc giá làm đăng ký
cũ stale; không sửa hash để cho qua. Tất cả source/journal/score nằm private
trong ignored `artifacts/controller-studies/<study_id>/`.

```powershell
python -m issueintel controller-study-register --selection artifacts/controller-selection.json
python -m issueintel controller-study-inspect --study <study_id>
python -m issueintel controller-study-run --study <study_id> --authorization artifacts/pilot-authority.json --limit 1
# Chỉ sau pilot hợp lệ và quyền matrix riêng:
python -m issueintel controller-study-run --study <study_id> --authorization artifacts/matrix-authority.json --limit 5
python -m issueintel controller-study-score --study <study_id>
```

Register/inspect/score dispatch trước dotenv và không construct provider. Run
chỉ load environment sau khi kiểm source, authority, journal và pilot gate.
Inspect có thể replay SQLite index đã có; không phải filesystem read-only API.
Khi một call đang cập nhật nhiều journal, inspection có thể từ chối transient
inconsistency; điều đó không cấp quyền restart hoặc retry.

## Authority và cận đã đăng ký

Một arm rules có tối đa chín Groq attempts; Jev giữ cùng recipe và cận Groq,
thêm tối đa bốn quyết định. Native output repair tiêu attempt và chi phí thật.
Mỗi arm có cùng allowance **193.536 microUSD (0,193536 USD)**, gồm controller;
không cộng riêng ngân sách Jev để làm arm đó mạnh hơn. Shared stages vẫn tiêu
logical calls và referenced cost của từng arm; paid owner chỉ đếm một lần.

| Phạm vi | Groq tối đa | Typesafe tối đa | Tổng USD tối đa | USD Typesafe tối đa |
|---|---:|---:|---:|---:|
| Cặp pilot đầu tiên | 18 | 4 | 0,387072 | 0,010752 |
| Cả năm cặp, gồm pilot | 90 | 20 | 1,935360 | 0,053760 |

Các số là allowance hữu hạn của protocol, **không phải quyền chi tiêu** hoặc
dự báo hóa đơn. Cận trước từng call tính bằng native tokenizer/input bounds và
giá có ngày đã pin; thiếu key/giá hoặc thay config bị chặn. Hai provider còn
chịu native ledger riêng. Typesafe config sản phẩm vẫn disabled/zero; runner
chỉ derive config cho ledger study sau authority. Không sửa `.env` thật.

Authority `controller_study_authorization_v1` phải khóa study/registration,
source fingerprint/runtime/budget policy SHA, đủ năm unit theo thứ tự; có
`authorized_case_count` 1 hoặc 5 và phase tương ứng `first_case_pilot` hoặc
`registered_matrix`. Cần cả `execution_authorized`, `groq_spending_authorized`
và **`typesafe_spending_authorized`**, nguyên văn yêu cầu của tác giả, ngày và
expiry UTC không quá hai ngày từ ngày khai báo. Bốn field cận
`max_groq_attempts`, `max_typesafe_attempts`, `max_joint_micro_usd`,
`max_typesafe_micro_usd` phải dương và không vượt hàng tương ứng ở bảng trên.
`gold_read`, `holdout_access_authorized`, `live_access_authorized`,
`new_freeze_authorized`, `bulk_annotation_restart_authorized` đều phải false.
File declaration kiểm scope/local consistency; không xác thực danh tính tác giả.
Key đã có, quyền Groq hoặc bốn call Jev smoke cũ không thay approval Typesafe mới.

Pilot cần cả hai arm hoàn tất, mọi paid outcome đóng và ít nhất một lựa chọn
Jev thật. Fallback-only không mở matrix. Matrix đổi thứ tự rules/Jev xen kẽ
theo chỉ số case, bắt đầu rules ở case đầu; không chọn case dễ sau khi xem output.

## Execution, replay và crash

`controller_study_execution` reserve joint allowance trước provider construction,
ghi request/raw/receipt/outcome, native stage binding/full payload và controller
decision. Exact native SQLite receipt phải khớp journal; receipt ID chỉ một
owner. Lỗi persistence/cap xuyên qua native Typesafe error wrapping, thoát khỏi
rules fallback. Transport/model failure có receipt đã đóng vẫn giữ nguyên;
pending/unknown hoặc journal lệch chặn lần chạy tiếp, không tự retry từ raw.

Stage dùng chung chỉ khi full binding khớp; seed timestamp và repair count giữ
nguyên, không nén thành hypothesis subset. Guard quanh từng stage/call/decision
kiểm code/config/source, authority và journal. OS lease chặn hai writer; byte
lease được kiểm path/size nhưng không đọc khi Windows đang lock, và không đi
vào inference content seal. Namespace, link/reparse, file/byte/count caps vẫn
áp dụng cho cả control file.

`controller_study_replay` chạy lại native Planned pipeline từ full cached stages
và exact recorded controller choices, từ chối mọi cache miss với zero API.
Native retrieval chạy deterministic; full final pack, stage trajectory, Jev
wire request/receipt và decision sidecar đều phải khớp. Chỉ rehash một pack khác
nhưng structurally valid không đủ qua replay. Inspection-only execution không
có khả năng gọi provider, chạy stage hoặc ghi decision; unknown nhất quán có
thể được báo, consistency/orphan errors vẫn bị từ chối.

Chỉ sau đủ mười ô terminal, không còn stage/decision/call pending/unknown và
đối soát paid ownership mới tạo full inference seal. Failed/unavailable vẫn
trong mẫu số; không bịa pack cho chúng. Score kiểm seal và full replay trước/sau,
không mở gold; mở lại và score lại không thêm call hoặc receipt.

## Metrics và giới hạn nghiệm thu

`controller_study_score` giữ năm selected case clusters cho mỗi arm. Báo
completion/failure/unavailable, trajectory, retrieval yield, native citation
integrity, critic assessments khi có, action/fallback, rule-shadow agreement,
latency và USD. Preparation chung tách khỏi pipeline latency và đếm một owner;
logical referenced costs không cộng ngang thành physical spend. Missing usage
giữ bound/unknown, không tự chia charged total thành input/output observations.
Arm chạy sau có thể reuse stage của arm trước, nên wall-clock pipeline latency
chịu ảnh hưởng thứ tự/cache. Báo cả measured elapsed và referenced provider
latency; tổng latency được phân bổ lại không phải phép đo cold end-to-end mới.
Năm cặp xen kẽ không đủ chứng minh một controller nhanh hơn ngoài thực tế.

Rule shadow chỉ trả lời rules sẽ chọn gì ở **state đã quan sát**; không dựng
đường chạy counterfactual, và agreement không phải decision accuracy. Native
critic là một phần treatment, không phải common independent output judge.
Structural grounding và critic labels không chứng minh semantic truth,
developer benefit, kiểm người hay Jev vượt rules. Cần actual authorized pilot/
matrix và common output-quality assessment trước khi nghiệm thu V03; tooling
tổng hợp không thay các phép đo đó. [Pilot thật một cặp](VALIDATION_V03_PILOT.md)
đã hoàn tất với sáu receipt settled; bốn lựa chọn Jev trùng rules. Sau quyền riêng,
[matrix năm case](VALIDATION_V03_MATRIX.md) đã sealed/chấm, giữ cả failed và
unavailable; [fresh common assessment](VALIDATION_V03_COMMON.md) đã đo trên
bốn completed pairs, không cho thấy chênh lệch nhãn. V01 giữ checkout/adapters đã khóa riêng;
Holdout/Live tiếp tục đóng.

[Common grounding harness](CONTROLLER_GROUNDING.md) đã có adapter riêng sau full
seal, dùng fresh canonical native critic, một paid owner/input và child allowance
trừ parent spend. Tooling không thay actual assessment; kết quả chấm chung thật
được báo riêng với in-treatment critic ở trên.

Kiểm cục bộ cho harness: 37 registration/CLI checks, 33 runner checks gồm
full native synthetic five-case matrix/seal/repeated scoring, 21 replay checks,
và 201 shared execution/budget/product-study/scorer checks đã đạt. Sau các sửa
admission cuối, 39 execution/review checks đã kiểm lại orphan native ledger,
revoked/expired authority, unfinished decisions và inspection-only unknown.
Các phạm vi có overlap, không cộng thành một tổng test độc lập. Không có API
Typesafe mới hoặc quality result thật trong các phép kiểm tổng hợp này.
