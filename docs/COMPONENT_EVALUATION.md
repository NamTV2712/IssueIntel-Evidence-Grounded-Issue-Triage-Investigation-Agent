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

Pilot cục bộ đầu tiên ngày 24/10/2026 dừng ở call generation đầu vì lỗi provider
không có HTTP status trong metadata cũ. Chưa nhánh nào hoàn tất, không đọc gold
và không tạo inference seal. Reservation giữ cận USD 0.002857 với usage chưa rõ;
GET danh sách model sau đó trả 200 không chứng minh POST đã thành công. Hồ sơ lỗi
được giữ nguyên, không tự thử lại, thay ca hoặc báo kết quả chất lượng. Chẩn đoán
transport mới chỉ áp dụng cho call sau, không bổ sung dữ kiện đoán vào receipt cũ.

## Continuation riêng cho pilot lỗi transport

Runner gốc giữ một arm lỗi ở trạng thái terminal, không tự gọi lại provider.
Adapter continuation hỗ trợ trường hợp cụ thể: đúng pilot năm nhánh, bốn nhánh
đã hoàn tất và một generation của counter-search lỗi ở pha request, có loại
lỗi mạng được nhận diện, receipt `failed_bound` đã đóng và không có response
trả về. Không nhận outcome unknown, response đã trả nhưng thiếu native stage,
lỗi schema, lỗi đọc response hoặc lịch sử/source đã thay đổi.

Adapter không thay code inference đang đăng ký. CLI mới chạy child Python cô
lập, nhập toàn bộ `issueintel` inference từ `--root` của checkout gốc; ba file
adapter được nạp riêng bằng đường dẫn và khóa hash. Preview replay nguồn và
bốn pack hoàn tất, rồi lưu plan private bất biến. Plan khóa cả inventory gốc,
request lỗi, runtime, adapter và cận sử dụng. Không sửa fingerprint hay receipt
cũ để làm đăng ký hết stale; cần giữ checkout inference gốc nguyên vẹn.

Admission kiểm native preflight trên đủ 25 nguồn một lần trong lượt preview
hoặc đầu lượt run. Việc replay pack chỉ dựng thêm context cho ca có pack đã
hoàn tất; ca chưa có pack vẫn phải qua cohort preflight. Preview gộp create/save
trong cùng lượt kiểm, rồi kiểm lại source/config và inventory trước khi lưu.
Run kiểm lại inventory dưới lease trước credentials; stage/provider guards vẫn
replay nguyên current case và native graph trước/sau call. Final replay và seal
giữ cổng full cohort/native packs, không dùng kết quả timing thay proof.

Đọc journal dùng kích thước file đã kiểm cùng một byte sentinel, thay vì xin
buffer bằng trần 16 MB cho mỗi record nhỏ. File đổi kích thước trong lúc đọc bị
từ chối. Overlay vẫn đọc đủ calls/stages/arms, kiểm seal, namespace, symlink/
reparse và cận file/count/total bytes như trước; không chỉ lọc calls để bỏ qua
audit records. Đây là tối ưu buffer, không phải bỏ kiểm integrity hoặc chi phí.

Mốc tối ưu đạt 147 test, một symlink test skip trên Windows; actual junction,
full 125-cell seal/reopen, crash/no-retry và common grounding đều được kiểm.
Trên 120 journal tổng hợp, ba lượt đo luân phiên cho median reader cũ 1,031s và
reader mới 0,406s với exact inventory/records giống nhau. Đây chỉ là timing
reader local, không phải quality hoặc tốc độ inference toàn bộ. Native preview
dữ liệu thật vẫn replay đủ 25 nguồn và bốn pack gốc, không gọi provider/gold.

Đổi adapter để bỏ lượt kiểm trùng làm thay hash và cần plan/authorization mới.
Plan cũ giữ nguyên, không được rehash để trông hợp lệ với code mới. Một preview
cũ chưa tạo execution intent không phải paid attempt hoặc kết quả inference;
source adapter cũ có thể giữ riêng để đối chiếu. Frozen inference code và mọi
arm/receipt gốc giữ nguyên.

```powershell
# Chạy CLI từ checkout có adapter mới; --root trỏ tới checkout inference đã khóa.
python -m issueintel --root <frozen_checkout> component-continuation-preview --experiment <registration_sha256>
python -m issueintel --root <frozen_checkout> component-continuation-inspect --plan <plan_sha256>
python -m issueintel --root <frozen_checkout> component-continuation-run --plan <plan_sha256> --authorization artifacts/continuation-pilot.json --limit 1
# Chỉ sau khi đủ năm nhánh effective và có quyền matrix khớp:
python -m issueintel --root <frozen_checkout> component-continuation-run --plan <plan_sha256> --authorization artifacts/continuation-matrix.json --limit 25
```

Ủy quyền `component_continuation_run_authorization_v1` phải khớp toàn bộ plan,
nguồn adapter, model/effort/output, ordered cases và phase; tham chiếu đúng bytes
của ủy quyền native tương ứng. Kiểm plan/ủy quyền và cổng matrix diễn ra trước
khi đọc `.env` hay mở ledger. Việc tự viết file khai không xác thực danh tính
hoặc tạo quyền chi tiêu mới; cần căn cứ yêu cầu đã được tác giả cho phép.

Continuation lưu dưới `artifacts/experiments/component_continuation/`, tách khỏi
`cases/` gốc. Stage đã hoàn tất dùng lại nguyên payload và timestamp. Call mới
có ID attempt riêng; request đầu dùng đúng request lỗi đã khóa. Native replay
chỉ cho phép khác thứ tự key của JSON object trong user message, giữ nguyên
mọi giá trị, thứ tự list, system prompt, schema và cấu hình. Request gửi đi vẫn
là bản gốc. Schema repair chỉ đếm call của attempt mới; lỗi mạng cũ nằm trong
audit/accounting, không biến thành schema repair.

Cả referenced calls, owned calls và tổng thí nghiệm đều tính attempt lỗi cũ,
call mới và shared references, giữ cận năm call/arm và 625 call toàn matrix.
Receipt dùng chung chỉ có một owner và tính một lần trong tổng chi phí thực.
Nếu attempt mới lại lỗi, trạng thái terminal giữ nguyên, không tự trả tiền thử
lại. Crash sau khi chốt native stage cho phép reuse; crash khi chỉ có HTTP
response hoặc intent chưa chốt thì dừng để audit, không tái dựng timestamp giả.

Bốn nhánh gốc không được thay thế. Nhánh phục hồi có execution refs riêng và
audit refs giữ arm/stage lỗi cũ. Mốc thời gian gốc giữ nguyên, thời gian chuẩn
bị/pipeline phục hồi báo riêng; tổng active time không phải wall-clock hay
phép đo cold-cache độc lập. Call timing của runner bao gồm wrapper, ghi journal
và source guards quanh provider; không phải độ trễ mạng/API thuần. Năm pack
effective phải hoàn tất và replay native
trước matrix. Chỉ đủ 125 trạng thái terminal, toàn bộ stage/call/receipt có
owner và native pack replay hợp lệ mới tạo effective inference seal v2.

Chấm điểm là bước riêng, cần schema
`component_continuation_score_authorization_v1` khớp plan, adapter, selection và
effective seal, cho phép gold Main Dev và không cho call provider:

```powershell
python -m issueintel --root <frozen_checkout> component-continuation-score --plan <plan_sha256> --authorization artifacts/continuation-score.json
```

Scorer replay seal trước/sau gold boundary và dùng cùng định nghĩa localization
gốc, giữ cả original failure và effective outcome. Báo riêng transport attempt,
schema repairs, chi phí unknown, thời gian gốc và phục hồi. Không mở Holdout/Live
hoặc xác nhận semantic quality. Kiểm thử tổng hợp bao phủ phục hồi, full matrix,
crash boundaries, không retry, isolation và accounting; kết quả test cơ chế không
thay kết quả thí nghiệm thực.

### Phản hồi verifier đã lưu nhưng stage chưa chốt

`benchmarks.component_response_recovery` cung cấp bước chuẩn bị/replay ngoại
tuyến cho đúng một verification stage còn intent, có một response returned và
receipt settled thuộc stage đó. Nó replay source strict, retrieval và annotation
đã hoàn tất, rồi đưa nguyên response qua verifier/pipeline native. Request phải
khớp system/schema/roles, giá trị và thứ tự list; chỉ cho khác cách sắp key/spacing
của JSON object. Generation mới, thiếu response, usage chưa rõ hoặc nhiều intent
đều bị từ chối. Candidate khóa inventory gốc và mới, giữ thời gian stage/arm chưa
ghi là null; không mở ledger, credentials, gold hay provider và không sửa parent.

Các hàm `prepare_response_recovery`, `save_response_recovery` và
`load_response_recovery` chỉ tạo/kiểm một candidate riêng. Đây chưa phải adapter
tiếp tục inference: runner cũ vẫn từ chối intent, số terminal và seal không đổi.
Để chạy tiếp cần tích hợp recovery refs vào một successor được kiểm riêng;
không được chép payload hoặc thời gian giả vào journal gốc để vượt admission.

14 test tổng hợp đạt: crash sau response/before publication, replay không call,
reopen/idempotent save, từ chối source/request đổi, kết quả/thời gian bịa rồi
rehash, receipt chưa settled và nhiều intent. Đây là kiểm cơ chế phục hồi;
không tăng số nhánh inference đã nghiệm thu hoặc thay phép đo chất lượng.
