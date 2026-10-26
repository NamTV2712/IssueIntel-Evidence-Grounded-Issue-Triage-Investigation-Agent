# Common automatic grounding cho V03

Phép chấm này dùng một pass mới trên cùng public projection của cả rules và
Jev, sau full controller inference seal. Nó không dùng lại critic trong
treatment và không mở benchmark gold, Holdout hoặc Live. Nhãn vẫn là nhận xét
của model về evidence nhìn thấy, chưa phải semantic accuracy hoặc causal truth.

`evaluation.common_grounding` là lớp thuần cho projection/request, dedup và
thống kê. `evaluation.controller_grounding` giữ native parent replay, kế hoạch,
quyền, budget và journal. Source inference đã đăng ký phải được giữ nguyên;
adapter mới chạy từ checkout khác.

## Giao diện và cô lập source

Chạy CLI từ checkout có adapter mới, truyền `--root` là checkout inference đã
khóa. Ví dụ dưới dùng đường dẫn minh họa; thay bằng checkout frozen thực tế:

```powershell
python -m issueintel --root C:/IssueIntel-frozen controller-grounding-preview --parent-study <parent-study-id>
python -m issueintel --root C:/IssueIntel-frozen controller-grounding-inspect --study <parent-inference-seal-sha>
python -m issueintel --root C:/IssueIntel-frozen controller-grounding-run --study <parent-inference-seal-sha> --authorization artifacts/common-pilot-authority.json --limit 1
# Sau common pilot hợp lệ và matching matrix authority:
python -m issueintel --root C:/IssueIntel-frozen controller-grounding-run --study <parent-inference-seal-sha> --authorization artifacts/common-matrix-authority.json --limit 5
python -m issueintel --root C:/IssueIntel-frozen controller-grounding-score --study <parent-inference-seal-sha>
```

CLI spawn `-I`: chỉ native package của frozen root đi vào import path. Hai file
helper/adapter được nạp theo exact filename, không thêm thư mục lân cận vào
import path. Plan pin raw hashes của hai file cùng toàn bộ native parent source,
runtime/config/prices, nguồn và inference seal. Đổi source làm plan stale,
không sửa hash để qua cổng. Cần giữ cả checkout native lẫn hai file adapter trong
suốt quá trình chạy/replay; không archive chúng khi còn được plan tham chiếu.

Parent cần đúng năm case/mười ô terminal, native pack/stage/controller replay
khớp, không pending/unknown/locked, mọi receipt có owner. Loader đọc existing
event/index và native SQLite bằng immutable/query-only; chỉ nhận DB rollback
journal đã đóng, từ chối WAL/journal trước khi tạo sidecar. Không construct
Workbench hoặc parent budget/stage index để phục hồi dữ liệu ngầm. Full inventory
trước/sau phải bằng nhau. Parent đang đổi sẽ bị từ chối, không suy thành missing.

## Input và ý nghĩa của một judge unit

Native public projection chỉ giữ hypotheses và evidence cần cho critic, bỏ
controller/trace/in-treatment verdict/provenance. Hidden fields bị kiểm trước
khi bỏ metadata. User JSON được canonicalize để thứ tự field/Python hash seed
không tạo treatment khác. Model/call recipe là Groq `openai/gpt-oss-120b`, low,
output tối đa 2.048 token, native structured critic schema.

Mỗi exact canonical request duy nhất có một fresh owned call. Hai arm hoặc
nhiều case có request giống hệt chỉ trả tiền một lần, giữ mapping/alias về từng
selected cell. Đây không tạo thêm case độc lập. Một call đã chạy trong treatment
không được nhận làm fresh judge dù prompt giống nhau.

Mẫu số luôn là năm case/mười arm rows. Parent failed/unavailable/missing không
được bịa annotation hoặc nhãn unsupported. Label fractions dùng reviewed subset
và báo coverage riêng. Chỉ so paired fractions trên common reviewed cases;
không đổi missing thành negative label hoặc coi dedup aliases là quan sát độc lập.

## Quyền, chi phí và single owner

Plan ID là parent inference seal SHA. Child nằm ở
`artifacts/controller-grounding/<parent-seal-sha>/`, có owner riêng cho đúng
parent seal/plan. Preview lặp lại chỉ nhận đúng plan cũ; không tạo ID khác để
đặt lại budget. Không thêm call vào parent ledger đã sealed.

Cận kết hợp giữ **90 Groq, 20 Jev, 1.935.360 microUSD**, riêng Jev tối đa
53.760 microUSD, tính cả parent. Child chỉ dùng Groq; allowance còn lại trừ
toàn bộ owned parent spend/bounds và attempts, tối đa mười fresh units. Failed
usage vẫn giữ bound. Cận token/USD từng request tính từ native request đã
canonicalize và dated price đã pin, trước construction/HTTP. Đây là ceiling
của protocol, không tự cấp quyền chi tiêu.

Authority `controller_grounding_authorization_v1` khóa plan/parent/seal/source/
runtime/adapter hashes, đủ case/unit order, phase và finite combined caps.
`max_new_judge_calls` tính toàn phase, gồm các unit pilot được reuse ở matrix.
Cần `execution_authorized` và `groq_spending_authorized`, quyết định tác giả,
ngày/expiry UTC không quá hai ngày từ ngày khai báo. Jev spending, gold,
Holdout/Live, freeze và bulk restart đều false. Quyền Groq đã có chỉ dùng được
trong scope được tác giả cho phép; key hoặc plan không thay authority.

Common pilot chọn các unique units của case đầu, tối đa hai. Nếu case đầu
không có đầu vào mà các case sau có, không tự đổi case pilot hoặc mở paid
matrix. Matrix cần pilot reviewed và persistent gate khớp. Không có source unit
thì báo coverage, không tạo lời gọi để đủ số.

Mỗi paid budget stage bind execution-intent SHA. Unit intent/outcome phải trỏ
đúng authority có scope chứa unit đó; history đã hết expiry vẫn được kiểm như
history, không biến thành quyền chạy mới. Xóa/rehash/đổi phase intent hoặc pilot
gate không được qua bằng cách đổi metadata của unit.

## Failure và scoring

Joint allowance/intent được lưu trước provider construction. Source, phase,
child inventory, native receipt và parent seal được kiểm quanh call; factory
config/model phải khớp trước HTTP. Mỗi unit tối đa một call, không schema repair
hoặc tự retry. Receipt, closed failure, raw outcome và unknown/pending giữ riêng;
response đã trả về không đủ để dựng lại một outcome chưa commit.

Chỉ đủ mọi selected judge units terminal và usage đóng mới tạo child seal.
Inspect/score không đọc `.env` hoặc construct provider; score native-replay
response/schema, kiểm seal trước/sau, rồi báo fresh labels, aliases, coverage,
unique cost và latency riêng với parent. Mở lại không phát call. Directional
token totals thiếu observations giữ `None`, có known sums/coverage riêng;
failed bound không báo thành spend chắc chắn hoặc observed zero.

Giới hạn kế thừa: native parent inspection còn kiểm freshness của price config;
sealed trial có thể bị từ chối sau khi giá quá hạn. Không tự cập nhật giá/hash
của parent để vượt cổng. Event/index/hash chứng minh local consistency, không
authorship, hóa đơn provider hoặc chống coordinated rollback toàn bộ storage.
Model generator và judge giống nhau có shared bias; không có human validation,
executed falsification checks hoặc phép đo developer benefit trong tooling này.

Kiểm cục bộ: 139 helper/adapter/CLI và shared dispatch checks đạt, gồm canonical hash seeds,
native receipt/breach/unknown usage, single owner, crash/no-retry, authority
lineage/gate, source drift và SQLite WAL rejection. Read-only smoke trên parent
thật replay đủ mười ô/29 calls, tám pack completed và bốn unique judge inputs
mà không tạo child plan, gọi provider hoặc sửa parent. Tooling và model labels
là hai phạm vi riêng. [Kết quả chạy thật](VALIDATION_V03_COMMON.md) có bốn fresh
calls, giữ source failure trong coverage và không kết luận Jev tốt hơn rules.
