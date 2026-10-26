# IssueIntel

Trợ lý điều tra issue bằng snapshot Git, code graph, bằng chứng có nguồn và các giả thuyết cạnh tranh. Demo và IssueIntel-Bench dùng cùng một pipeline chỉ đọc. [proposal.md](proposal.md) là thiết kế sản phẩm; [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) mô tả các ranh giới code.

Entry point `run.py` chạy trực tiếp từ clean checkout bằng Python 3.11+ và Git,
không cần pip, virtualenv, Node, PostgreSQL hay dữ liệu benchmark. `--check` chỉ
kiểm runtime và không gọi API. Các extras bên dưới dành cho development, Parquet
và tokenizer tối ưu; lõi vẫn có token upper-bound bảo thủ khi thiếu tokenizer.
Nếu máy đã có dữ liệu benchmark Parquet, cần extra `data` để mở Main Dev;
thiếu reader không ảnh hưởng điều tra repository hoặc ví dụ tổng hợp.

## Môi trường phát triển trên Windows

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,data,llm]"
# Tạo .env theo .env.example nếu chưa có; thêm GROQ_API_KEY trên máy.
.\.venv\Scripts\python.exe -m issueintel bootstrap
.\.venv\Scripts\python.exe -m issueintel status
.\.venv\Scripts\python.exe -m pytest -q
```

Lõi dùng thư viện chuẩn Python. `data` để nhập Parquet; `dev` để kiểm thử; `llm` thêm tokenizer gốc cho các input lớn trên Groq. Không cần PostgreSQL, Docker, Torch hoặc GPU để chạy lõi.

### Trạng thái hệ thống và dung lượng

Mở panel **Trạng thái hệ thống và dung lượng / System readiness and storage**
để kiểm Python/Git/FTS5, cấu hình provider/giá có ngày, sync local, cache và điều
kiện dùng từng chức năng. Có key hoặc cấu hình hợp lệ chưa chứng minh key còn
hiệu lực, có ngân sách cho call tiếp theo hoặc GitHub App/HTTPS đã nghiệm thu.
Mở/làm mới chỉ đọc trạng thái local; không gọi model, tải dữ liệu hoặc khởi động
đồng bộ. Các thao tác điều tra vẫn kiểm nguồn, consent và budget riêng.

**Xem trước dung lượng và cache** là bước riêng, quét metadata có giới hạn và
báo rõ nếu chỉ đo được một phần. Số byte là độ dài logic của file đã thấy,
không phải số byte chắc chắn giải phóng được; dung lượng volume hiển thị riêng.
Symlink, junction/reparse và ranh giới filesystem bị bỏ qua. Dữ liệu, artifact,
receipt, checkpoint, Git cache, môi trường và source được giữ mặc định. Preview
chỉ liệt kê một số cache công cụ/bytecode có dạng đã biết và đã kiểm đủ phạm vi;
nội dung lạ hoặc hardlink làm mất điều kiện. Không có thao tác xóa trong app.

### Kiểm thử trình duyệt tổng hợp

Development có extra `browser` tùy chọn, pin Playwright 1.64.1. Chạy trên Windows:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,browser]"
.\.venv\Scripts\python.exe -m playwright install --only-shell chromium
$env:ISSUEINTEL_BROWSER_TESTS = "1"
.\.venv\Scripts\python.exe -m pytest tests/browser -q
Remove-Item Env:\ISSUEINTEL_BROWSER_TESTS
```

Các test mở Chromium thật tới server HTTP loopback, dùng repository/config/receipt
tổng hợp trong thư mục tạm. Không cần `.env`, khóa thật hoặc dữ liệu benchmark;
transport giả lập giữ cơ chế receipt của provider, không gửi API ra ngoài.
CI có job browser riêng. Chỉ khi test lỗi mới lưu screenshot/trace dưới ignored
`artifacts/browser-tests/`. Cài browser/Playwright dành riêng cho kiểm thử; chạy
sản phẩm vẫn dùng lõi Python chuẩn và không cần Node hoặc Chromium.

## So sánh thành phần trên Main Dev

Các lệnh `component-register`, `component-preflight` và `component-inspect`
khóa/replay thiết kế so sánh lexical/symbol, graph, lịch sử có proof, verifier
và counter-search trên đúng 25 accepted Main Dev gốc. Chúng không đọc `.env`,
gọi model hoặc mở gold. `component-run` cần ủy quyền khớp đăng ký và pilot một
ca trước matrix; `component-score` chấm riêng sau inference seal đầy đủ.
Xem [giao thức và giới hạn diễn giải](docs/COMPONENT_EVALUATION.md).
Đây là công cụ V01; chưa phải bằng chứng chất lượng đã được nghiệm thu.

Nếu pilot đã giữ bốn nhánh hoàn tất và nhánh counter-search lỗi transport,
`component-continuation-preview/run/inspect/score` hỗ trợ một continuation riêng,
khóa adapter mới và chạy inference từ checkout gốc còn nguyên. Các kết quả,
receipt và chi phí lỗi cũ vẫn được giữ; không tự retry hoặc bỏ qua cổng pilot.
Xem [điều kiện phục hồi và cách dùng](docs/COMPONENT_EVALUATION.md#continuation-riêng-cho-pilot-lỗi-transport).

`component-grounding-preview/run/inspect/score` thêm phép chấm model chung sau
full inference seal: fresh critic cho mọi projected input độc nhất, giữ mapping
năm arm và receipt owner. [Giao thức grounding](docs/COMPONENT_GROUNDING.md)
phân biệt automatic assessment với citation integrity và kiểm người; không mở
gold hoặc thay đổi pack inference. Chưa có kết quả quality từ tooling này.

## Review câu hỏi và bước kiểm tra ngoại tuyến

Ba lệnh `usefulness-build`, `usefulness-import`, `usefulness-score` chuẩn bị và
tổng hợp review các question/check exports **product đã lưu**, không đọc `.env`,
mở benchmark, gọi model hoặc chạy code đích. Selection khóa file/hash và giữ
cả trạng thái rỗng/lỗi/không có đề xuất; xem schema và giới hạn trong
[docs/PRODUCT.md](docs/PRODUCT.md#review-usefulness-ngoại-tuyến--chuẩn-bị-v02).

```powershell
python -m issueintel usefulness-build --selection artifacts/selection.json --output artifacts/reviews/study-01
# Đọc reviewer/review.html, điền reviewer/ratings.csv; khai báo reviewer riêng.
python -m issueintel usefulness-import --review artifacts/reviews/study-01 --ratings artifacts/reviews/study-01/reviewer/ratings.csv --reviewer artifacts/reviewer.json
python -m issueintel usefulness-score --review artifacts/reviews/study-01
```

Chỉ thư mục `reviewer/` có nội dung ẩn tên phương pháp; `organizer/` chứa mapping
và nguồn private. Nhãn nhập không tự thành kiểm người độc lập hoặc bằng chứng
cải thiện chất lượng. Không tự tạo nhãn hoặc chấm khi chưa có reviewer.

Review cặp dùng `usefulness-pair-build`, `usefulness-pair-import` và
`usefulness-pair-score`: so intake questions/checks đã có với bộ đề xuất mới trên
cùng pack và evidence, đóng băng thứ tự A/B một lần. Baseline giữ nguyên nội dung
nguồn; trường chưa có để trống, không giả provenance LLM hoặc sinh giả kết quả.
[Giao thức review cặp](docs/USEFULNESS_REVIEW.md) mô tả selection v2, CSV và giới
hạn diễn giải. Các lệnh v1 vẫn giữ nguyên contract; đây chưa phải phép đo lợi
ích thực tế cho lập trình viên.

## Workbench chạy local

So sánh controller riêng có `controller-study-register/inspect/run/score`:
khóa năm captured original GitHub inputs, cùng Planned v2/Groq 120b low và
allowance chung cho rules/Jev. Register/inspect/score không gọi model; run cần
quyền Typesafe riêng và pilot một cặp trước matrix. [Giao thức V03](docs/CONTROLLER_STUDY.md)
ghi cận chi phí, full stage replay, crash/no-retry và giới hạn metrics. Đây là
harness kiểm chứng, chưa phải kết quả Jev tốt hơn rules.

```powershell
.\.venv\Scripts\python.exe -m issueintel serve
# Mở http://127.0.0.1:8787
```

Chọn một Main Dev accepted hoặc nhập issue cùng repository/revision. Nút **Thử ví
dụ tổng hợp** chạy được khi chưa có dataset/token. Mặc định không gọi API: kết quả
có snapshot, graph và code evidence. Bật Groq để sinh giả thuyết và chạy verifier
riêng theo cấu hình/ngân sách hiện có. Quick/Deep thay độ rộng evidence; export
JSON/Markdown và lịch sử lưu riêng trong ignored `artifacts/demo/`.
Tab **Tổng quan / Overview** trình bày trích issue, giả thuyết, bước kiểm tra và
vị trí code cần xem từ cùng một `InvestigationReport` có phiên bản. Web, bản
Markdown, JSON báo cáo và nháp GitHub cùng dùng lớp này, khóa vào bytes của pack,
query và snapshot. JSON pack gốc vẫn có nút tải riêng. Báo cáo cũ được đọc qua lớp
trình bày mới mà không sửa pack/receipt hoặc gọi lại model. Nhận xét model, kiểm
citation và kiểm người được phân biệt; chưa tự xác nhận nguyên nhân, mức ảnh
hưởng, xác suất tin cậy hoặc việc đã chạy các bước kiểm tra.
Tab **Giả thuyết / Hypotheses** có bảng đối chiếu từ cùng báo cáo đã lưu:
nguồn hỗ trợ/phản bác, nhận xét model, khoảng trống và bước kiểm tra của từng
giả thuyết. Chọn hai giả thuyết để xem nguồn dùng chung hoặc cùng nguồn được
model gán hai vai trò khác nhau. Đây là quan hệ citation cần xem lại, chưa phải
mâu thuẫn ngữ nghĩa hoặc bằng chứng các giả thuyết độc lập. Không xếp hạng hoặc
chọn một nguyên nhân thắng. JSON/Markdown đối chiếu khóa vào report/pack/query/
snapshot/commit; mở lại, đổi ngôn ngữ và xuất không gọi model. CLI export thêm
`hypothesis-review.json` và `hypothesis-review.md`; pack và report v1 giữ nguyên.
Khi bật Groq, Deep có thêm tối đa một vòng truy xuất theo giả thuyết trong cùng
snapshot; chỉ sinh lại nếu có evidence mới. Checkpoint từng bước được giữ để
xem/tải khi call sau lỗi. Kết quả tìm thêm chưa phải counter-evidence đã xác nhận.
Với issue product ban đầu, **Planned / Nhiều bước** chọn từ một đến ba vòng
theo số file, khoảng trống snapshot và counter-evidence còn thiếu được quan sát.
Mỗi vòng tối đa ba query/sáu excerpt mới; không lặp query hoặc evidence đã lấy.
Chỉ sinh lại khi có evidence mới, rồi chạy verifier tối đa một lần. Cận là chín
call Groq kể cả sửa schema, vẫn chịu giới hạn và receipt từng call hiện có.
Chọn Planned không tự bật Groq; có thể chạy offline với zero call. Tab **Trace**
hiển thị các vòng, nguồn mới, lý do dừng và cận sử dụng; routing này chưa phải
điểm độ khó hay chất lượng đã đánh giá. CLI dùng `investigate --planned`.
Planned chưa hỗ trợ Main Dev hoặc follow-up; phục hồi stage lỗi của phiên gốc
được mô tả bên dưới.
Planned mới trên Web hoặc CLI `--durable` với rules lưu policy `bounded_planner_v3`; CLI thường và Jev giữ
`bounded_planner_v2`. Các policy này dùng truy xuất theo khoảng trống: mỗi hành động chỉ rõ
giả thuyết, khoảng trống và mục tiêu support/counter/caller/documentation/history.
Caller đi ngược cạnh call tĩnh có đích exact; tài liệu và changelog chỉ tìm trong
snapshot đã khóa. Lịch sử issue/PR chỉ xem context có receipt đã được chọn, không
tự tải nguồn mới hoặc coi lịch sử hiện tại là proof trước ngày issue. Không có
nguồn thì ghi unavailable; chưa tìm thấy không chứng minh nguồn không tồn tại.
Vòng không có excerpt mới có thể chuyển sang mục tiêu kế tiếp trong budget còn
lại, không gọi generation. Hồ sơ v1/v2 vẫn đọc/replay nguyên bản; API Python giữ
policy v1 mặc định, nhận policy v2/v3 khi caller chọn rõ.
Với Web rules v3, sau một lượt tìm thực sự không thêm evidence, hệ thống có thể
lưu trạng thái **Chờ câu trả lời** khi còn câu hỏi intake chưa được trả lời và
còn budget cho generation mới cùng critic. Tab **Trace** cho kiểm khả năng tiếp
tục, xem câu hỏi Việt/Anh và giới hạn còn lại. Trả lời một phần hoặc “chưa biết”,
rồi duyệt chi phí để tạo đúng một phiên con cho lần dừng đó. Không tự gọi API
khi mở báo cáo, đổi ngôn ngữ hoặc xem câu hỏi; lỗi/hủy/restart không tự thử lại.
Phiên con dùng cùng commit, giữ code evidence và context đã khóa của cha, sinh
giả thuyết mới cho query có câu trả lời chưa xác minh. Tổng toàn chuỗi vẫn chịu
cận gốc: tối đa ba vòng truy xuất, bốn lượt generation, một critic và chín call
Groq kể cả schema repair; continuation không đặt lại budget hoặc mở rộng nguồn.
Các form follow-up/context riêng bị chặn khi Planned đang chờ. Báo cáo, hồ sơ
issue và gói bàn giao giữ lineage/câu trả lời; gói cần cả tổ tiên để kiểm offline.
Phần waiting/continuation đã nghiệm thu ở mục 5; chưa phải
resume stage lỗi hoặc bằng chứng cải thiện chất lượng điều tra.
Planned có tùy chọn **Jev / Typesafe** để chọn retrieve/verify/stop từ các bước
được code cho phép. Mặc định vẫn dùng rules. API key là `TYPESAFE_API_KEY` trong
`.env`; cấu hình riêng ở `configs/typesafe.toml` mặc định tắt, trần request và
USD bằng 0. Cần tác giả chọn trần request/ngày, USD/ngày, USD/tháng và bật trước
khi sử dụng; không kế thừa quyền chi tiêu Groq. UI chỉ gửi yêu cầu Jev khi bạn
chủ động chọn, Planned đang bật và Groq được chọn để sinh giả thuyết.
Mỗi phiên tối đa bốn call Jev bổ sung, với receipt/budget riêng và không retry.
Issue, phần trích code và giả thuyết được rút gọn có giới hạn trước khi gửi.
Lỗi dịch vụ, budget hoặc output không hợp lệ chuyển về rules và ghi rõ fallback.
Trace và chi phí phân biệt call điều phối Jev với call sinh/kiểm Groq; xác suất
Jev trả về là cho lựa chọn hành động, không phải xác suất nguyên nhân đúng.
CLI dùng thêm `--controller jev --planned --provider groq`. Ngày 17/10/2026 đã
kiểm tra kết nối thật bằng đúng bốn call dữ liệu tổng hợp, sau ủy quyền riêng
trần USD 0.011; cả bốn response/schema/receipt hợp lệ, ghi nhận USD 0.000079.
Đây là kiểm tra kết nối, chưa đánh giá chất lượng controller hoặc nghiệm thu
luồng GitHub thật. Receipt và dữ liệu thử giữ private; cấu hình thường vẫn tắt
với trần 0. Quyền thử một lần không cấp ngân sách Jev sử dụng lâu dài.
Nếu generation đã lưu nhưng verifier lỗi hoặc bị ngắt, mở phiên lỗi trong lịch
sử rồi chọn **Tiếp tục verifier từ checkpoint**. Sau khi kiểm snapshot/citation/
hash và xác nhận chi phí, hệ thống tạo phiên mới chỉ chạy verifier, giữ output
generation cùng receipt cũ. Mỗi phiên nguồn chỉ có một intent tiếp tục; gửi lặp
không tạo thêm call. Chỉ áp dụng checkpoint product Quick/Deep mới đủ proof,
không Main Dev hoặc Planned. Thay đổi policy/source code có thể làm checkpoint
cũ không còn đủ điều kiện tiếp tục; bytes và receipt cũ vẫn được giữ nguyên.
Nếu **generation và verifier đều hoàn tất nhưng xuất báo cáo lỗi**, chọn
**Kết xuất từ checkpoint đã hoàn tất** ở phiên lỗi. Preview và xác nhận khóa cả
hai SHA, rồi tạo bản xuất mới từ dữ liệu đã lưu với **0 call mới**, không cần
API key hay repository Git còn trên máy. Hệ thống kiểm bytes/hash, schema và
nguồn request; đây không phải đọc lại toàn bộ Git. Một intent cho mỗi phiên
nguồn chặn xuất trùng qua restart. Chi phí cũ vẫn thuộc phiên nguồn. Luồng này
chỉ hỗ trợ product Quick/Deep, giữ nguyên generation/verifier và không tự retry.
Với **phiên product gốc mới** bị lỗi, hủy hoặc ngắt trước các bước đó, mở lịch sử
rồi chọn **Tiếp tục các stage còn thiếu**. Workbench ghi journal cho Quick/Deep
và Planned dùng rules hoặc Jev. Preview kiểm nguồn tại commit gốc, liệt kê stage đã lưu,
bước còn thiếu và số lượt thử đã ghi, tính cả lượt lỗi/chưa rõ đã tới provider.
Sau xác nhận, một phiên con dùng lại prefix đã hoàn tất và chỉ chạy phần còn
thiếu; receipt cũ thuộc phiên nguồn, receipt mới thuộc phiên con. Cận toàn lượt
vẫn là 4/6/9 call Quick/Deep/Planned, hoặc 0 khi offline. Nếu phần còn lại không
cần API thì không cần key; vẫn cần Git để kiểm nguồn. Mỗi nguồn có một intent,
gửi lặp trả cùng phiên con; lỗi/hủy/restart không tự retry. Worker còn giữ quyền
thực thi thì nguồn chưa được nhận phục hồi. Policy/source đổi có thể từ chối
phục hồi nhưng không sửa hồ sơ đã lưu. Hồ sơ issue và gói bàn giao giữ journal,
attempt accounting và lineage để kiểm các bytes đã gửi mà không gọi model.
Nếu phiên phục hồi lại lỗi, mở chính phiên đó để xem preview và xác nhận tiếp.
Hệ thống ghép tối đa tám phiên nguồn, giữ chủ sở hữu và bytes của từng stage;
mọi lượt lỗi trước đó vẫn trừ trong cận của lượt gốc, không cấp ngân sách mới.
Planned mới giữ thêm cận 5/7/9 của routing sau seed; preview dùng cận đã biết,
cả khi cận này nhỏ hơn profile chung 9. Đã hết cận thì không gọi thêm model.
Nguồn thiếu attempt ledger/intent hoặc đã hết cận không được nhận. Hồ sơ và gói
bàn giao giữ cả các tổ tiên lỗi, không chép lại stage/receipt cũ sang phiên mới.
Phiên trả lời bổ sung Planned mới cũng có journal: nếu lỗi, preview giữ đúng
pack cha và câu trả lời đã xác nhận. Phần đã dùng ở cha được trừ trước khi cấp
cận phục hồi; UI hiển thị riêng số kế thừa và số lượt thử của đoạn đang phục hồi.
Generation đã xong không chạy lại khi critic lỗi. Lỗi export sau khi đủ stage
có thể phục hồi không API. Luồng này dùng chung Workbench, CLI và gói bàn giao;
không chép receipt của cha hoặc cấp lại ngân sách khi đổi phiên.
Follow-up mới cũng có journal: bổ sung dữ kiện, trả lời câu hỏi làm rõ, quan sát
kiểm tra, memory đã chọn và context repository đã chọn. Khi phục hồi, hệ thống
giữ đúng phiên cha/dữ kiện/selection ban đầu, dùng lại generation đã xong và
giữ cận 4/6 call của phiên follow-up đó. Đây là cận của lần điều tra dữ kiện mới;
các lần phục hồi của nó không cấp lại ngân sách hoặc chép receipt cũ. Nguồn
memory/context mới thay đổi có thể chặn gọi tiếp; báo cáo đã lưu vẫn mở từ
provenance đã khóa, không cần đọc lại nguồn hiện tại. Lỗi export có thể tiếp
tục không API. Checkpoint legacy chưa có recovery journal không tự được chuyển đổi.
Planned Jev mới lưu thêm từng quyết định và receipt đúng phiên sở hữu. Quyết
định đã lưu được dùng lại, kể cả khi job ngắt trước checkpoint kế tiếp. Cả chuỗi
giữ tối đa bốn lượt thử Jev, tách riêng cận Groq 5/7/9; đổi phiên không cấp lại
ngân sách. Preview nêu provider có thể dùng và receipt đã kết thúc nhưng thiếu
quyết định; chi phí cũ vẫn giữ nếu tác giả xác nhận thử bước thiếu trong cận còn
lại. Receipt còn reserved chặn gọi tiếp để đối soát. Khi chỉ còn critic/export,
không tạo Typesafe client hoặc đọc cấu hình Typesafe. Gói bàn giao kiểm được
quyết định, lượt thử và receipt xuyên các phiên mà không có token.
Với Planned được phục hồi rồi
dừng chờ, có thể trả lời để tiếp tục khi đủ ngân sách. Preview trừ cả các lượt
thử lỗi trước phục hồi; phiên trả lời kế thừa phần đã dùng đúng một lần, không
sửa plan/waiting receipt cũ. Unknown accounting hoặc thiếu ngân sách cho
generation, schema repair và critic thì từ chối gọi.
Phần **So sánh hai phiên điều tra** đối chiếu các báo cáo đã lưu của cùng issue:
query/snapshot, evidence và hypotheses. Đổi context cần gắn lại citation;
khác biệt giữa hai kết quả không được coi là đã cải thiện chất lượng hay sửa lỗi.
Tab **Hồ sơ issue / Issue dossier** gom các phiên cùng issue, kế hoạch, quan sát
và đính chính thành dòng thời gian. Trong tab này, **Gói bàn giao ngoại tuyến**
cho chọn phiên, tự thêm tổ tiên bắt buộc và xem preview file/hash/dung lượng
trước khi xác nhận tải ZIP riêng tư. Gói gồm báo cáo, pack nguồn, excerpt,
kế hoạch/quan sát/lineage đã chọn, HTML đọc offline và `verify.py` dùng Python
chuẩn. Bên nhận có thể kiểm bằng `python verify.py bundle.zip --expected-sha256
SHA` hoặc `python -m issueintel verify-handoff bundle.zip --expected-sha256 SHA`.
Không cần token, Git cache, full repo hoặc model. Trang HTML không tự kiểm hash;
hash chỉ kiểm tính nhất quán, không xác thực tác giả hoặc nguyên nhân lỗi.
Phiên con giữ chính xác quan sát đã dùng;
bảng A/B chỉ đối chiếu nhãn tự báo với dự đoán model, ghi riêng commit đã thử
và đính chính về sau. Hai giả thuyết cùng ID không tự được ghép. Người dùng có
thể ghi liên kết giữ lại/điều chỉnh/mới/không mang sang, kèm lý do và citation;
đính chính append-only. Mở lại và xuất hồ sơ JSON không gọi model hoặc Git.
Tab **Kiểm chứng / Verification** có **Kiểm chứng từng nhận định / Claim-level
review** riêng cho product đã có ít nhất hai giả thuyết. Sau khi đồng ý chi phí,
Groq tách tối đa bốn đoạn nhận định trong mỗi câu gốc, trích đúng source span,
ghi giả định và phản bác. `claim_sufficiency_v1` giữ `uncertain/unsupported` khi
thiếu source support, applicability, code cho suy luận cơ chế/hành vi, hoặc còn
giả định chưa xác minh. Nhận định nhân quả không được nâng thành đã xác minh
bằng code tĩnh. Giao diện phân biệt nhận xét model với kết quả sau cổng mức đủ
bằng chứng, hiển thị cả phần câu chưa được kiểm. Một intent bền vững cho mỗi
pack, tối đa hai call kể cả sửa output; lỗi transport không retry. Bản kiểm lưu
riêng, có JSON/Markdown, đi cùng dossier/gói bàn giao; không sửa pack/report hay
chạy code đích. Mở lại không cần Git, cấu hình API hoặc model. Đây là kiểm nguồn
và rubric trên nhận xét tự động; chưa chứng minh độ chính xác chẩn đoán.
Thanh đầu trang hỗ trợ **Tiếng Việt / English** và **sáng / tối / theo hệ thống**,
nhớ lựa chọn trong browser. Đổi ngôn ngữ không gọi model hoặc dịch nội dung nguồn.
Nháp checklist, dữ kiện bổ sung, câu trả lời làm rõ và quan sát kiểm tra được giữ
trong bộ nhớ của trang theo đúng phiên/pack và bộ câu hỏi hoặc kế hoạch nguồn.
Đổi tab/ngôn ngữ hoặc mở B rồi trở lại A khôi phục đúng nháp của A; B không nhận
nháp hoặc lựa chọn Groq của A. Nháp checklist chưa lưu được mở lại để thấy phần
đang sửa. Form bổ sung dữ kiện giữ lựa chọn Groq riêng của đúng nguồn; form làm
rõ/quan sát tắt consent khi đổi nguồn hoặc ngôn ngữ. Vẫn cần chủ động gửi từng
form. Reload/đóng trang mất nháp chưa lưu; nội dung riêng tư không được ghi vào
localStorage. Mở báo cáo A khi B còn chạy hiển thị rõ A; B vẫn chạy và có thể hủy,
còn response/progress muộn của B không ghi đè trạng thái báo cáo đang xem.
Tab **Thông tin đầu vào / Intake** chỉ ra cue và thông tin cần xem lại. Form
**Bổ sung dữ kiện / Add context** tạo phiên mới cùng commit, giữ nguồn dẫn và
hash về phiên cha; dữ kiện mới không được kế thừa nhãn strict lịch sử.
Với phiên product, **Trả lời câu hỏi gợi ý** đưa ra câu hỏi Việt/Anh theo các cue chưa
tìm thấy, không gọi model. Trả lời một phần hoặc ghi “chưa biết”, rồi chủ động
tạo phiên mới cùng commit. Báo cáo giữ nguyên câu hỏi/câu trả lời và hash nguồn;
không coi nội dung này là xác minh lịch sử hoặc độ đầy đủ. Code được truy xuất
lại; nguồn repository đã chọn trước đó không tự đi theo query mới.
Với báo cáo product đã có ít nhất hai giả thuyết, **Câu hỏi theo giả thuyết và
code** tạo 1–4 câu hỏi riêng cho issue bằng Groq sau khi duyệt chi phí. Mỗi câu
có bản Việt/Anh, lý do hỏi, cách dùng câu trả lời và liên kết về giả thuyết/code.
Một lượt gọi cho mỗi báo cáo nguồn; gửi lặp nhận lại tác vụ cũ, lỗi không tự thử
lại. Có thể mở/xuất bộ câu hỏi đã lưu và dùng câu trả lời tạo phiên tiếp nối.
Câu hỏi là đề xuất của model, chưa chứng minh khả năng phân biệt hoặc nguyên nhân.
Tab **Issue liên quan / Related issues** tìm ứng viên trong lịch sử local cùng
repository, có đoạn khớp và hash nguồn. Điểm tương đồng chưa xác nhận duplicate;
kết quả không tự đi vào inference strict.
Trong tab này, **Đối chiếu hai issue** cho chọn một phiên product đã lưu hoặc một
**issue** GitHub đã đồng bộ của đúng repository, xem trước hai nguồn rồi chủ động
duyệt gửi chúng tới Groq. Báo cáo offline chưa có giả thuyết cũng dùng được.
Tìm ứng viên và preview không gọi model; PR/comment chỉ làm context ở luồng chọn
nguồn bên dưới, không được dùng thay issue ứng viên trong đối chiếu.

Kết quả riêng gồm triệu chứng, cơ chế, phiên bản và khác biệt, với đoạn hỗ trợ/
phản bác ghi rõ phía trái/phải, nguồn, revision và vị trí Unicode. Model nêu
`related`, `possible_duplicate` hoặc `insufficient`; server tính lại mức đủ bằng
chứng và giữ nguyên nhận xét gốc, giả định, phản bác và phần chưa biết. Cùng thông
báo lỗi chưa đủ để nhận `possible_duplicate`. Phiên bản ảnh hưởng chưa rõ vẫn
hiển thị thành khoảng trống cần hỏi thêm; commit code không tự thành phiên bản
phát hành. `possible_duplicate` chỉ là ứng viên cần xem lại, chưa xác nhận
duplicate, nguyên nhân hoặc bản sửa.

Mỗi lựa chọn cặp/nguồn/policy có một intent bền vững, tối đa hai call kể cả một
lần sửa output; lỗi, hủy và restart không tự tạo lượt trả phí thay thế. Snapshot
và nguồn được kiểm trước provider, các call kiểm lại nguồn/policy và giữ receipt
đúng owner. Nội dung ứng viên được khóa để lần đồng bộ sau không đổi bản đã lưu.
Đọc/xuất JSON hoặc Markdown không cần Git, token hay cấu hình provider. Hồ sơ issue
và ZIP bàn giao giữ kết quả cùng nguồn ứng viên đã khóa; preview tính cả phần
nguồn bổ sung này, dù ứng viên không thuộc các phiên đang chọn trong dossier.
Không tự comment, gắn nhãn hoặc đóng issue.

Với báo cáo issue GitHub, mục **Chọn nguồn cho điều tra** tìm trong
repository đã đồng bộ. Xem trích đoạn rồi chọn tối đa sáu nguồn; phiên mới giữ
query và commit gốc, chỉ dùng đúng các nguồn đã chọn. Nội dung/receipt đổi sẽ yêu
cầu tìm lại; Groq mặc định tắt. Đây là context hiện tại, chưa xác minh duplicate
hoặc lịch sử. Báo cáo và receipt gốc được giữ nguyên.

Tab **Kế hoạch kiểm tra / Check plan** lưu trạng thái và ghi chú theo phiên bản,
mở lại sau restart và xuất JSON riêng. Trạng thái hoàn tất là tự báo cáo, chưa
xác minh bản sửa; hệ thống không chạy code/test của repository đích.
Điều tra product có giả thuyết lưu sẵn còn có **Kiểm tra phân biệt giả thuyết**:
duyệt gửi nguồn tới Groq để đề xuất 0–3 kiểm tra, gồm procedure, điều kiện kiểm
soát, kết quả A/B và dự đoán A/B/unknown cho từng giả thuyết. Gợi ý bước xem
trước dựa trên số cặp A/B do model nêu, rồi công sức ước tính; chưa đánh giá
chất lượng kiểm tra hoặc xác nhận nguyên nhân. Một tác vụ cho mỗi nguồn, tối đa
hai call gồm một output repair; lỗi/hủy/restart không tự gọi lại. Kế hoạch bổ
sung khóa pack/report/query/snapshot/annotation, xuất JSON/Markdown và mở lại
offline. Nó giữ nguyên report v1 và nhật ký gốc. **Quan sát kiểm tra phân biệt**
lưu A/B/khác/chưa rõ/chưa chạy theo từng kiểm tra bổ sung, điều kiện, kết quả,
diễn giải và commit tự báo; đính chính tạo event mới. Chọn tối đa ba quan sát
đang hiệu lực và chủ động tạo phiên điều tra tiếp cùng commit phân tích gốc.
Groq mặc định tắt; mỗi yêu cầu chính xác chỉ tạo một child, không tự gọi lại khi
lỗi/hủy/restart. Phiên con giữ bản quan sát đã chọn; sửa nhật ký sau đó không
đổi kết quả cũ. Quan sát chưa được xác minh, không tự kết luận giả thuyết đúng,
không kế thừa chứng nhận lịch sử. Main Dev/strict và lineage benchmark bị chặn.
Với điều tra product, tab này còn có **Nhật ký kiểm tra giả thuyết**: chọn đúng
bước đề xuất trong báo cáo, ghi điều kiện/đầu vào, mong đợi, quan sát, diễn giải
và full SHA của commit thực sự đã thử nếu biết. Commit này do người dùng cung
cấp, không được kiểm Git hoặc tự điền bằng commit phân tích. Có thể ghi chưa
chạy/chưa rõ; đính chính tạo bản ghi mới trỏ về bản cũ, không xóa lịch sử.
Nhật ký khóa vào report/pack/query/snapshot, có tối đa 100 bản ghi mỗi phiên và
chống hai nơi ghi cùng phiên bản. JSON xuất riêng; Markdown gồm báo cáo cùng
toàn bộ quan sát và đính chính. Nháp giữ trong bộ nhớ browser khi đổi ngôn ngữ
hoặc lưu lỗi; đóng/reload trang sẽ mất phần chưa lưu. Dữ liệu đã lưu ở SQLite
private, mở lại không cần repo/network/model. Không đưa quan sát vào prompt,
nhãn benchmark hoặc trạng thái xác nhận nguyên nhân/bản sửa. Main Dev/strict
và hậu duệ có nguồn benchmark bị chặn, checklist cũ giữ riêng.

Tab **Đồ thị bằng chứng / Evidence graph** đọc báo cáo đã lưu, phân biệt nguồn
truy xuất, citation hỗ trợ/phản bác và nhận xét model. Chọn nút bằng chuột hoặc
bàn phím để xem trích đoạn/hash, lọc quan hệ, mở citation về báo cáo và xuất JSON.
Không gọi lại LLM; đường nối không chứng minh nguyên nhân hay kiểm người.

Tab **Token và chi phí** và mục chi phí bên dưới tiến độ hiển thị receipt của
đúng phiên, cả khi job lỗi hoặc interrupted. Mỗi reservation được gắn về job
trước HTTP; lỗi/thiếu usage giữ cận trên. Có export JSON riêng; phiên cũ không
có receipt hiện unknown. Đây là số liệu ghi nhận/giữ chỗ, không phải hóa đơn.

Phần **Chi phí toàn chuỗi điều tra** ở mục 9 có hai phạm vi:
**Phiên hiện tại** và **Toàn chuỗi điều tra**. Phạm vi thứ hai theo đúng liên kết
cha/nguồn đã lưu, gồm các phiên tiếp nối/phục hồi và tác vụ câu hỏi, check plan,
claim review hoặc đối chiếu issue gắn với nguồn đó. Mở một finalization có thể
thấy 0 call mới trong phiên hiện tại và chi phí cũ của cha ở toàn chuỗi. Các
phiên gốc độc lập, nhánh con khác, chi phí điều tra ứng viên được so sánh, lần tạo
memory và tổng ledger toàn cục không tự được cộng vào.

Groq và Typesafe giữ riêng phần chi phí đã biết, tổng trong phạm vi đã ghi, số
reservation/cận còn giữ và trạng thái quyết toán usage. Receipt trùng cùng owner
chỉ tính một lần; cùng reservation nhưng khác owner hoặc bytes là bất nhất,
không chọn một bản để báo tổng. Đủ bản ghi vẫn có thể còn cận reserved/failed/
missing-usage. Thiếu attempt tracking hoặc receipt giữ tổng unknown dù phần đã
biết vẫn xem được. Thiếu proof tổ tiên/khám phá sidecar làm toàn chuỗi chưa đủ,
không xóa số liệu của phiên hiện tại đã có đủ bản ghi riêng.

Đọc/làm mới/xuất JSON hoặc Markdown chỉ chụp lại metadata/receipt local có giới
hạn, không gọi model, Git, API key, giá mới hoặc provider ledger. API `/usage`
cũ giữ `job_usage_v1`; `/investigation-usage` là projection riêng có snapshot và
hash nguồn. Gói bàn giao thêm accounting của **từng phiên được chọn**; các bảng
có thể dùng chung tổ tiên nên không cộng chúng thành tổng của cả ZIP. Hash kiểm
bytes và binding cục bộ được gửi, không chứng minh không có call/file bị thiếu
hoặc số tiền trên hóa đơn provider. Bộ kiểm lõi, collector/runner, bàn giao,
hồi quy rộng và browser QA đã đạt với dữ liệu tổng hợp. CI phát hành và quyết
định nghiệm thu mục 9 đã đạt trên commit `300164a`; issue #9 đã đóng.

Mục **Đối chiếu phiên bản code** dưới báo cáo product nhận commit/tag khác, đọc
snapshot và lưu riêng thay đổi code cùng hash nguồn/đích. Diff ưu tiên file trong
evidence; file bị skip giữ ngoài phạm vi, không báo nhầm đã xóa. Có thể thêm
**Commit tham chiếu** để kiểm phiên bản đích có chứa commit đó không và xem tag
Git local chứa nó. Quan hệ thiếu lịch sử giữ unknown; không tự tải thêm lịch sử
hoặc toàn bộ tag. Tag local chưa chứng minh bản phát hành đã công bố hay lỗi đã
sửa. Không gọi model/chạy test. Main Dev/strict không dùng luồng này; phiên con
product và phiên stage recovery dùng được sau khi server kiểm đầy đủ nguồn gốc.
Bản báo cáo gốc giữ nguyên.

**Thay đổi ảnh hưởng bằng chứng nào?** đối chiếu từng đoạn code đã được dẫn:
còn nguyên tại vị trí cũ, khớp ở vị trí khác, thay đổi, không thấy trong phạm vi
đã đọc, chưa đủ điều kiện đối chiếu hoặc có nhiều vị trí khớp. Mỗi mục giữ đoạn
nguồn, tọa độ/commit/hash hai phía và ID giả thuyết đã dẫn nguồn đó; không chuyển
citation hay kết luận của giả thuyết sang commit mới. Chọn bộ lọc trạng thái để
xem đoạn liên quan, rồi xuất riêng JSON/Markdown. Báo cáo cũ chỉ có diff file
vẫn mở được và không được suy thêm kết quả đối chiếu excerpt.

“Khớp ở vị trí khác” là khớp nguyên văn theo dòng trong phạm vi text đã đọc,
chưa chứng minh Git rename hoặc cùng ý nghĩa. File bị skip hoặc tìm kiếm vượt
cận giữ chưa đủ điều kiện; nhiều vị trí không tự chọn vị trí đầu. Số file/dòng/
byte, cận tìm và phạm vi bỏ ngoài được giữ trong export. Việc không thấy hoặc
chỉ có một vị trí khớp là quan sát local đã ghi: export không chứa toàn bộ text
repository đích để kiểm độc lập tính đầy đủ/không tồn tại. Mở/export bản đã lưu
không đọc lại Git, cấu hình API hoặc gọi model. Đối chiếu không xác nhận lỗi đã sửa.

Với điều tra GitHub product, mục **Kiểm tra GitHub Release** nhận một tag và
full SHA của commit tham chiếu. Nó đọc metadata công khai, resolve tag (kể cả tag
có chú thích), rồi hỏi GitHub về ancestry giữa hai SHA cố định. Quan sát có ID
repository/release, ngày đọc, receipt/hash và export riêng; mở lại không gọi
network/model. Draft/prerelease được báo riêng. Tag hiện tại không chứng minh
nội dung tại ngày công bố; chứa commit cũng chưa chứng minh lỗi đã sửa.
Tag hỗ trợ tối đa 200 ký tự chữ/số ASCII và `_ . / -`; commit tham chiếu cần đủ
40 hoặc 64 ký tự hex viết thường. Đây là kiểm một release được chọn, không phải
danh sách mọi release hoặc chứng nhận tính đúng đắn của bản sửa.

Nguồn **GitHub issue URL** nhận issue thật và revision rõ ràng. Tab **GitHub**
cho xem bản nháp comment với code links và duyệt trước khi đăng. Adapter webhook
nhận lệnh `/issueintel investigate`; việc cài GitHub App và endpoint HTTPS được
ghi riêng trong [docs/PRODUCT.md](docs/PRODUCT.md), chưa triển khai tự động.

Khởi tạo bộ nhớ repository public (code, issue/PR, bình luận, commit và file của
PR) trong bảng **Bộ nhớ repository GitHub** của workbench, hoặc qua CLI, không
gọi LLM. Giao diện hỗ trợ Việt/Anh và sáng/tối; hiển thị scan/revision, phạm vi
đã đọc, checkpoint và tìm kiếm local riêng với báo cáo điều tra.

**Kết nối và tự đồng bộ** chạy backfill đến hết trang, tiếp tục qua restart và
chờ theo rate limit. Webhook HMAC cho repo/installation đã kết nối cập nhật
issue, PR, comment và snapshot nhánh đang theo dõi. Một scheduler local dùng
chung worker/queue với investigation, không gọi model. App installation event
có App ID/installation ID cấu hình trước sẽ tự kết nối các repo public được chọn.
GitHub App/gateway HTTPS ngoài máy vẫn cần thiết lập một lần; bản local không tự
công khai workbench. `python run.py --webhook-relay` chạy cổng loopback 8788 chỉ
nhận `POST /api/github/webhook` có chữ ký, rồi chuyển tới Workbench loopback
8787. Gateway chỉ nối vào relay; UI/session/API điều tra không được phục vụ qua
cổng này. Cần `GITHUB_WEBHOOK_SECRET` local; lệnh không tự tạo tunnel hoặc cài App.
Phần đồng bộ thủ công bên dưới vẫn dùng được độc lập. Nghiệm thu App/HTTPS thật
vẫn chưa hoàn tất; kiểm repo public chỉ đọc không thay thế bước đó.

```powershell
.\.venv\Scripts\python.exe -m issueintel sync-repository --repo owner/repo --revision HEAD --max-requests 50
.\.venv\Scripts\python.exe -m issueintel repository-status --repo owner/repo
.\.venv\Scripts\python.exe -m issueintel search-repository --repo owner/repo --query "parser error"
.\.venv\Scripts\python.exe -m issueintel search-repository --repo owner/repo --query "parse_input" --code
.\.venv\Scripts\python.exe -m issueintel repository-item --repo owner/repo --key issue:12
```

Chạy lại `sync-repository` để tiếp tục trang chưa hoàn tất; sau khi xong, thêm
`--refresh` để quét đối soát mới. Mỗi lần giới hạn 1–500 GET (mặc định 50, tính cả
kiểm định danh repository); Git fetch revision có giới hạn riêng. Đổi revision
cần đợi scan xong rồi refresh. Chi tiết coverage/giới hạn và dữ liệu private ở
[docs/REPOSITORY_INDEX.md](docs/REPOSITORY_INDEX.md). Đây là context GitHub hiện
tại; không đưa vào strict benchmark, không tự gọi model hoặc đăng comment.

Ngày 14/10 tác giả chọn bắt đầu sản phẩm với Main Dev đã đạt, giữ Holdout để đánh
giá cuối và không chờ đủ 400 strict. [Quyết định](configs/development_start_decision_2026-10-14.json)
không tự mở evaluation, đóng băng cohort mới hay khởi động lại bulk LLM.
Trạng thái implementation theo proposal nằm trong [docs/PRODUCT.md](docs/PRODUCT.md).

## Điều tra một danh sách issue

Panel **Điều tra nhiều issue / Batch investigations** nhận manifest JSON gồm
1–20 đầu vào product `local`, `github` hoặc `example`, dùng rules. Có thể dán
JSON, đọc file cục bộ hoặc điền mẫu tổng hợp. Ví dụ không gọi model:

```json
{
  "schema": "product_batch_manifest_v1",
  "run_key": "demo-batch-1",
  "items": [
    {"key": "quick", "request": {"mode": "example", "provider": "none", "depth": "quick"}},
    {"key": "deep", "request": {"mode": "example", "provider": "none", "depth": "deep"}}
  ]
}
```

**Xem trước** có thể đọc Git/GitHub để chụp nội dung issue và resolve commit đầy
đủ; không gọi model. Preview hiển thị nguồn, commit, lỗi từng mục và cận call.
Sửa manifest/file hoặc đổi ngôn ngữ làm mất hiệu lực consent trước đó. Nếu có
`provider: "groq"`, cần duyệt toàn bộ preview và chi phí riêng trước khi chạy;
tổng USD chưa biết không được coi là 0. Cận từng call/giá/ledger vẫn được kiểm.
Không dùng Main Dev, Holdout/Live, follow-up hoặc Jev trong batch product.

Mỗi mục giữ job ID, receipt và báo cáo riêng; dispatcher dùng worker/queue chung,
tối đa một mục chạy mỗi lúc. Có trạng thái từng mục, partial success, tạm dừng
nhận mục mới và hủy từng mục/cả lô. Tạm dừng không hủy call đang chạy; hủy cần
đợi ranh giới stage và call đã gửi vẫn có thể tính phí. Mục lỗi/ngắt không tự
được thử lại. Mở child là thao tác riêng; tiến độ batch không đổi báo cáo đang xem.

Giữ nguyên `run_key` để nhận lại cùng lô/child IDs khi gửi trùng hoặc restart;
cùng key nhưng manifest/nguồn duyệt khác sẽ bị từ chối. Mất phản hồi chỉ mở đối
soát từ danh sách đã lưu, không tự gửi lại hoặc đổi key. Sau restart, đọc trạng
thái không tự chạy tiếp; chủ động **Tiếp tục mục chưa chạy**, duyệt lại Groq nếu
có. Kết quả đã xong vẫn mở được mà không gọi model. Manifest/nguồn chụp lưu private
trong ignored artifacts; mẫu mới không cấp quyền chi tiêu Jev hoặc evaluation.
Mục 11 đã nghiệm thu tại `34552c1`, cả hai job CI test/browser thành công;
issue #11 đã đóng. Chi tiết kiểm thử ở `docs/PRODUCT.md`.

## Memory repository lưu trên máy

Memory kiến trúc và giả thuyết từ phiên product đã lưu có CLI dùng riêng,
không gọi model hoặc đọc token. Chọn đúng `pack.json` trong export của phiên:

```powershell
.\.venv\Scripts\python.exe -m issueintel memory-capture --pack artifacts/demo/runs/JOB_ID/pack.json --max-facts 500
.\.venv\Scripts\python.exe -m issueintel memory-capture --pack artifacts/demo/runs/JOB_ID/pack.json --kind lesson --hypothesis H1 --note "Cần xem lại guard này"
.\.venv\Scripts\python.exe -m issueintel memory-list --repo "OWNER/REPO" --query "parser"
.\.venv\Scripts\python.exe -m issueintel memory-show --id CAPTURE_ID
.\.venv\Scripts\python.exe -m issueintel memory-assess --id CAPTURE_ID --pack artifacts/demo/runs/OTHER_JOB_ID/pack.json
```

Thay `H1` bằng hypothesis ID thực trong pack; repository identity lấy đúng từ
`case.repository` (có thể là đường dẫn Git local). Capture/assessment đọc lại
Git tại commit đã khóa, không chạy code đích. Show/list dùng chứng cứ đã lưu,
không cần checkout hoặc credentials. Kho private ở `artifacts/repository_memory/`
dùng SQLite/FTS, source pack và cache snapshot nén theo hash; cùng blob code
không được chép lại cho mỗi observation. Gửi lặp cùng capture trả cùng receipt.
Nguồn hỏng bị từ chối, không ghi đè hash. Search chỉ index tối đa 16.000 ký tự
cho mỗi capture, chưa đo semantic recall. `lesson` giữ nguyên giả thuyết được
chọn và ghi chú chưa xác minh, không là nguyên nhân/bản sửa đã kiểm chứng;
luôn bị chặn khỏi strict inference. Các lệnh này từ chối benchmark/strict packs
trước khi đọc Git. Memory chưa tự đi vào prompt.

Workbench có tab **Bộ nhớ điều tra / Investigation memory** trên phiên product
đã lưu. Mở **Lưu từ phiên đang xem** để lưu architecture observations hoặc chọn
tối đa năm giả thuyết cũ cùng ghi chú chưa xác minh. Xem capture, mở code source
và đối chiếu với revision đang xem bằng các thao tác riêng. Hai commit được giữ
rõ; nguồn thay đổi/mất/không đọc được không được gắn nhãn còn nguyên. Đối chiếu
lại lỗi sẽ xóa trạng thái thành công cũ, còn nguồn cache vẫn mở được. Tab hỗ trợ
Việt/Anh, sáng/tối và màn hình hẹp; không tự gọi model hoặc tự tạo phiên mới.
Trong capture đã mở, chọn tối đa sáu mục memory xuyên nhiều capture rồi dùng
**Kiểm lựa chọn và xem preview**. Preview giữ từng item/receipt SHA, commit đang
xem, nguồn cũ và nội dung context chính xác (tối đa 16.000 ký tự). Quan sát kiến
trúc không khớp AST hiện tại bị từ chối toàn lựa chọn; giả thuyết cũ chỉ là hint
chưa xác minh và ghi rõ code nguồn đã đổi hay chưa. Đổi lựa chọn xóa preview cũ;
Planned đang chờ không mở nhánh mới qua preview. Preview không gọi model.
Sau khi xem nội dung, dùng **Tạo phiên từ preview này** để điều tra ở cùng
commit với code được truy xuất lại. Mặc định offline; bật Groq cần đồng ý gửi
issue, memory đã chọn và code tới provider. Memory là context chưa xác minh,
không mang citation/nhãn đúng sai của giả thuyết cũ sang phiên mới. Nội dung
memory không tự được tính là dữ kiện người báo đã cung cấp trong Intake.
Mỗi preview chỉ có một intent qua restart; gửi lặp trả phiên cũ, cả khi lỗi/hủy,
không đổi provider/depth để tạo thêm lượt gọi. Mất phản hồi thì **Kiểm trạng thái
phiên đã gửi** bằng đúng yêu cầu cũ. Nguồn/policy được kiểm trước/sau call và
trước công bố kết quả; receipt đã phát sinh vẫn được giữ khi phải dừng.
Phiên con có **Memory đã dùng trong phiên này**; report/dossier giữ nguồn và
hash. Gói bàn giao có `memory-selection.json`, context chính xác và tổ tiên
bắt buộc; kiểm offline không cần kho memory, Git hoặc API. Nó kiểm tính nhất
quán của lựa chọn đã ghi, không kiểm lại AST, xác thực capture gốc hoặc chứng
nhận tính đúng của giả thuyết/khả năng dùng nguồn trong lịch sử.

## Dữ liệu: 400 Main Dev gốc

### Diagnostic chính sách điều tra và kiểm tra phân biệt giả thuyết

Lõi `investigation.discriminating_checks` biểu diễn procedure, assumptions, hai
outcome A/B và dự đoán A/B/unknown cho từng giả thuyết. Code chọn check có nhiều
cặp giả thuyết được model dự đoán khác nhau nhất, rồi ưu tiên effort thấp hơn.
Đây là quy tắc dựa trên khai báo model, không phải information gain đã hiệu
chỉnh, độ đúng ngữ nghĩa hoặc bằng chứng đã chạy test. Nếu không có cặp phân
biệt được thì không chọn check. Product dùng chung quy tắc trong kế hoạch bổ
sung ở Workbench; không nhập artifact diagnostic hoặc đổi generation/critic
gốc. Tính năng product không mở benchmark evaluation.

```powershell
python -m issueintel register-policy-pilot
python -m issueintel run-policy-pilot --experiment <SHA> --limit 1
python -m issueintel run-policy-pilot --experiment <SHA> --limit 25
python -m issueintel score-policy-pilot --experiment <SHA>
```

Pilot chỉ chọn 25 Main Dev gốc đã accepted, round-robin repository rồi ID trước
khi xem output/gold. Hai prompt policy dùng chung tool-agent harness, seed
retrieval, model 120B medium, tối đa ba thao tác
search/read và năm call mỗi arm,
gồm tối đa một lượt sửa dành cho báo cáo cuối (protocol v10).
Baseline có tool thật; final schema giống nhau ở cả hai arm. Đây là ablation
chính sách prompt, chưa so toàn bộ IssueIntel với coding agent thương mại.
Hai arm ca đầu phải qua transport/contract trước khi chạy 24 ca còn lại.
Transport v10 dùng `json_schema` với `strict: true` cho cả lựa chọn và báo cáo,
không khai báo native tools. Đây là phương thức Groq hỗ trợ cho 120B;
[tài liệu provider](https://console.groq.com/docs/structured-outputs).
Planner phân loại một catalog ID: STOP, S1… hoặc R1…; chương trình mới thực hiện
thao tác tương ứng. Mỗi lựa chọn R trỏ vào khoảng đọc đã kiểm 60 dòng/4.000 ký tự,
tối đa 12 khoảng chưa dùng từ file trong evidence hiện tại. Mỗi S trỏ vào query
do code tạo từ định danh/từ khóa issue và symbol trong evidence, tối đa 24 query
không trống, không trùng và chưa dùng. Tìm kiếm vẫn trên toàn snapshot, có thể
đưa file khác vào evidence/catalog round sau. Model không gửi query/range tự do;
hai arm cùng catalog/rules. Đây là thay đổi phạm vi chọn search/read của harness,
không phải chỉ đổi cách đóng gói request. Chất lượng heuristic query chưa được
đánh giá. Backend vẫn kiểm lại bounds/lặp, không âm thầm cắt yêu cầu sai.
Bước trả báo cáo nhận schema đầy đủ và vẫn kiểm toàn bộ
schema/citation/độ dài bằng code; chỉ xếp hạng file đã truy xuất/đọc. Bước trả
báo cáo dùng `file_preferences`: một mức rank_1…rank_5/omit cho mỗi ID file đã
khóa trong catalog. Code chọn tối đa năm file duy nhất; đồng hạng giữ thứ tự
catalog đã lưu và được ghi rõ trong `ranking_projection`. Đây là ưu tiên tương
đối do model khai báo cùng quy tắc code, không phải xác suất hay thứ tự model
đã phân biệt hoàn toàn. Wire schema không dùng keyword uniqueItems bị provider
từ chối; danh sách cuối vẫn phải qua validator không lặp và citation đầy đủ.
Bước trả báo cáo đóng phase chọn tool. Output báo cáo sai có thể dùng đúng một stage repair
riêng từ context gốc, có receipt riêng; không lấy output sai làm evidence hoặc
tự sửa citation/path. Lỗi auth/rate limit/network/budget không được repair.
Planner sai không dùng lượt sửa này: code dừng truy xuất và ghi rõ
`controller_degraded`, dùng evidence đã có để thử báo cáo cuối. Không thực thi
action sai. Báo cáo cuối vẫn phải đạt toàn bộ validator; không tạo success giả.
Receipt/intent ghi trước HTTP; stage failed/incomplete không tự gọi lại. Output và
sources được replay trước resume. Không chạy code đích, Jev, Holdout hay Live.

Scorer chạy riêng sau khi seal inference, chỉ đọc patch columns của đúng 25
Main Dev để tính file Recall@5/MRR proxy; file ngoài snapshot giữ trong mẫu số.
Contract failures vẫn thuộc 25 ca, không loại để làm đẹp số. Một trial/case/arm
chỉ cho diagnostic mô tả, không chứng minh ưu thế hoặc statistical significance.
CSV review có ID được xáo thứ tự, không ghi arm; đánh giá ý nghĩa dự đoán/độ hữu
ích còn trống cho reviewer, không tự gọi model agreement là human validation.
Mọi registration, output, receipt và report nằm trong ignored
`artifacts/experiments/policy_pilot/`; không phải cohort freeze hay bulk labels.

Để kiểm bản sửa đầu ra, `register-policy-pilot --reliability-parent <SHA>`
đăng ký riêng bốn Main Dev từng lỗi được ghi trong authorization. Chạy cùng
`run-policy-pilot --limit 1` trước, rồi `--limit 4`. Đây là regression có chọn
ca theo lỗi cũ, không phải so sánh chất lượng; scorer từ chối đọc gold cho mode
này. Tối đa 40 call trên bốn ca/hai arm mỗi registration; control tổng hợp giai đoạn sửa này
tối đa 40 call qua các revision. Cổng ca đầu của regression yêu cầu không suy giảm, repair hoặc
action sai/lặp. Runner báo riêng failed call, repair và invalid action; chỉ đặt
`clean_regression=true` khi đủ tám nhánh và mọi bộ đếm lỗi bằng 0. Kiểm ổn định
v10 yêu cầu hai registration mới liên tiếp đạt cổng này, cùng fingerprint;
không chạy lại stage cũ đến khi nhận output thuận lợi.
Bundle cũ và receipt giữ nguyên; không so trực tiếp điểm v1 với protocol v10
vì transport, search/read/ranking contract và ngân sách đã đổi. Bundle cũ mở bằng report đã
lưu hoặc checkout commit đã ghi; code hiện tại từ chối replay fingerprint cũ.

**Phục hồi proof được giữ riêng: [107 pending ban đầu](docs/STRICT_RECOVERY.md).** Bulk LLM đã
tạm dừng theo tác giả; giữ 69 pack đã hoàn tất và mọi receipt. Không kết thúc
strict dưới 400 chỉ vì tìm giới hạn chưa thấy proof. Kiểm Tier B rồi Tier C,
giữ nguyên cohort v1; recovery thành công được ghi riêng để replay/freeze kế tiếp.

Theo yêu cầu ngày 13/10/2026, [eligibility v1](docs/STRICT_DEV_PROTOCOL.md) được
tách khỏi generation. Chỉ kiểm đúng 400 Main Dev đã có; không mở rộng DEV,
đọc gold/rationale, gọi LLM trong eligibility hoặc mở evaluation.

```powershell
.\.venv\Scripts\python.exe -m issueintel build-strict-dev --namespace main-dev-strict-v1 --target 400 --resume --concurrency 6 --acquire
```

Lệnh replay receipts theo hash, acquisition theo stage, snapshot-only và báo
pending riêng. Sau khi kiểm đủ 400, freeze N ≤ 400 ca thực sự đạt strict; không
bù bằng dữ liệu ngoài split. Groq 20b/120b routing là artifact phase sau cohort
và pilot; các lệnh/recipe cũ bên dưới là API tham khảo, không phải lệnh chạy phase này.

Milestone A đã audit đủ 400: 293 strict accepted, 107 pending, 0 excluded,
eligibility 0 LLM call. Milestone B dùng [DEV artifacts](docs/DEV_ARTIFACTS.md):
public retrieval → hypotheses → critique → investigation pack, có receipt từng
stage. Paired pilot 30 ca được replay trước bulk; policy model báo riêng lý do
ngoại lệ theo stage. Nhãn mới vẫn automatic/unverified.

## Điều tra một issue

```powershell
# Offline: issue.txt là nội dung bạn muốn điều tra; revision phải được chọn rõ.
.\.venv\Scripts\python.exe -m issueintel investigate --repo D:\path\to\repo --revision <commit> --issue-file issue.txt

# Repo/issue công khai, gọi Groq có ngân sách:
.\.venv\Scripts\python.exe -m issueintel investigate --repo https://github.com/owner/repo/issues/123 --revision <commit> --provider groq

# Annotation dev: tiếp tục từ receipt đã lưu, không chạy lại ca hoàn tất.
.\.venv\Scripts\python.exe -m issueintel annotate --limit 1
.\.venv\Scripts\python.exe -m issueintel annotate --limit 1 --judge
.\.venv\Scripts\python.exe -m issueintel review
```

`--provider none` xuất snapshot, graph, bằng chứng và báo phần còn thiếu; không giả lập nhãn LLM. `--judge` là bước đánh giá tự động theo patch/tests tham chiếu, không phải kiểm người hay xác nhận chân lý nhân quả. Báo cáo JSON/Markdown nằm trong `artifacts/investigations/`.

CLI có thể dùng cùng job store/journal với Workbench bằng `--durable`:

```powershell
.\.venv\Scripts\python.exe -m issueintel investigate --durable --repo D:\path\to\repo --revision COMMIT --issue-file issue.txt
.\.venv\Scripts\python.exe -m issueintel resume-stages --job JOB_ID
# Sau khi xem preview, dùng đúng binding SHA và một xác nhận tương ứng:
.\.venv\Scripts\python.exe -m issueintel resume-stages --job JOB_ID --binding-sha256 SHA --with-api
# Hoặc --without-api khi preview không cần call mới.
```

`--durable` nhận product gốc với critic, không benchmark case, custom output
hoặc tắt critic. Quick mặc định, `--refinement-rounds 1` chọn Deep; `--planned`
với rules dùng v3. Có thể chọn `--controller jev --planned --provider groq`
để dùng Jev v2 với cổng cấu hình/ngân sách riêng; có key chưa cấp quyền chi tiêu.
Rules chỉ gọi API khi thêm `--provider groq`. CLI thường vẫn giữ contract/output
cũ. Durable output ở `artifacts/demo/runs/JOB_ID/`, hiện
trong lịch sử Workbench; job ID được in ngay vào stderr, trạng thái cuối ở JSON
stdout. Job lỗi trả exit 2 và giữ stage/receipt; không tự retry.

`resume-stages` không có flags xác nhận chỉ kiểm preview, không load `.env`,
API config/provider hoặc khởi động đồng bộ GitHub. Preview vẫn đọc Git để kiểm
nguồn. Khi xác nhận, dùng lại đúng intent của Web; gửi lặp không tạo child/call
mới. Phiên còn được tiến trình khác sở hữu được giữ queued/running bằng lease,
không coi interrupted chỉ vì CLI mới không có trong active set của Web. Lease
được giữ từ enqueue tới khi worker kết thúc, kể cả hủy; sau process crash, lock
được OS thả và nguồn đủ journal mới có thể được nhận phục hồi.

## Benchmark và dữ liệu

Bootstrap nhập 500 Verified, chia 400 dev/100 holdout theo nhóm base snapshot, và 300 ứng viên Live. Nguồn có revision và SHA256 cố định. `created_at` gốc là ngày PR; ứng viên Live chưa phải temporal test đã xác minh. [docs/DATA_PROTOCOL.md](docs/DATA_PROTOCOL.md) mô tả nguồn, giới hạn thời gian và annotation.

```powershell
# Chuẩn bị dataset: snapshot/graph/localization, không chạy dự đoán hoặc metrics.
.\.venv\Scripts\python.exe -m issueintel build-assets --limit 3
.\.venv\Scripts\python.exe -m issueintel build-assets --limit 800 --all-splits
# Chỉ metadata/asset, không chạy model evaluation:
.\.venv\Scripts\python.exe -m issueintel map-main --limit 500 --all-splits
.\.venv\Scripts\python.exe -m issueintel map-main --limit 500 --all-splits --remap-pending
.\.venv\Scripts\python.exe -m issueintel prepare-asof --limit 10 --query-policy original_opening_query
# Đăng ký recipe trước khi sinh nhãn. Lệnh trả về SHA để dùng bên dưới.
.\.venv\Scripts\python.exe -m issueintel register-annotation --protocol issue_time --query-policy original_opening_query --splits main_dev main_test temporal_candidate
.\.venv\Scripts\python.exe -m issueintel annotate-dataset --recipe <SHA_vừa_đăng_ký> --limit 800
# Chốt các ca đủ proof và đủ bốn stage; ca còn thiếu được ghi thành exclusions.
.\.venv\Scripts\python.exe -m issueintel register-release --recipe <SHA_recipe>
.\.venv\Scripts\python.exe -m issueintel verify-release --release <SHA_release>
```

Development chỉ đọc `main_dev`. Holdout evaluation cần `open_test_split()` và ủy quyền riêng; lệnh đóng băng cũ không cấp quyền đánh giá bộ mới.

API evaluation dùng `from issueintel.evaluation import open_test_split`. Khi có release, truyền `release_sha256` để giới hạn cases/gold/labels vào cohort hoàn tất đã chốt. Mở capability còn cần câu ủy quyền evaluation mới và purpose; chốt release không tự chạy đánh giá.

`annotate-dataset` tạo nhãn theo recipe cố định, có thể chuẩn bị nhãn holdout, và không tính metrics hoặc dùng kết quả để chọn prompt/model. Recipe khóa code/config/model; registration mới giữ ZIP source có hash. Generation, critique, gold judgment và variants có receipt riêng, resume từng stage. Nhãn pilot trước registration được giữ riêng.

`register-release` lưu manifest bất biến trong `data/benchmark/releases/`, giữ số ca đã nhập, số ca hoàn tất và exclusions riêng. Release kiểm lại nguồn, archive của recipe, query lịch sử, snapshot và các stage; nhãn mới vẫn là nhãn tự động chưa được người kiểm. Release không cấp quyền đánh giá holdout và không đổi manifest bootstrap.

Tác giả chọn `original_opening_query`: mô tả lúc mở issue được lưu thành overlay có parent/hash, giữ nguyên SWE-bench và gold. Main Django dùng ticket Trac, không phải GitHub issue. Snapshot lịch sử chọn base hoặc ancestor đã có bằng chứng public trước lúc mở issue. Thiếu proof vẫn pending; tọa độ dòng/hàm không chuyển giữa revision nếu chưa đối chiếu chính xác.

```text
configs/             model, budget, giá có ngày, cutoff
src/issueintel/      repository | retrieval | investigation | annotations
                    benchmarks | evaluation | runtime | interfaces
tests/               fixture tổng hợp; không gọi API thật
docs/                kiến trúc, giao thức dữ liệu, cách gán nhãn
data/                private, ignored
  upstream/          file nguồn bất biến
  benchmark/         cases công khai cho inference; gold riêng
  annotations/       nhãn người gốc, nhãn tự động và temporal receipts
    original_queries/ mô tả ban đầu, tách khỏi nguồn SWE-bench
    recipes/          cấu hình sinh nhãn đăng ký trước call
    recipe_sources/   code/config tương ứng, nén và có hash
    dataset/          stage outputs theo recipe/case
  derived/           snapshot/blob dùng chung, graph/localization và build receipts
  repos/             cache Git bare, không checkout/chạy code của repo
  runtime/           sổ ngân sách và ủy quyền evaluation
artifacts/           báo cáo, blind review, lỗi và tiến độ; ignored
```

Groq: `openai/gpt-oss-120b`. Tác giả đã bỏ cả trần token và USD cho phần dựng lại cần thiết ngày 12/10/2026; quyết định ghi trong `configs/author_decisions.json`. Thiếu limit đơn thuần không bật unlimited. Vẫn có giới hạn context/output từng call, reservation và usage ledger qua restart; lỗi/usage thiếu giữ cận trên. Giá quá 30 ngày phải kiểm lại. Khóa nằm trong `.env`, không đưa vào báo cáo. Dữ liệu được lưu local; khi chọn Groq, issue và đoạn code được chọn sẽ gửi đến API Groq.

Jev có adapter Typesafe, cổng quyết định typed, fallback theo luật và stage recovery.
Kết nối đã được thử bằng bốn call dữ liệu tổng hợp theo quyền thử riêng; cấu hình
product thường vẫn tắt/trần 0, chưa có đánh giá chất lượng controller. Không dùng
ngân sách Groq để tự gọi Typesafe. Phạm vi đã triển khai và các mục còn lại được
đối chiếu tại [docs/PRODUCT.md](docs/PRODUCT.md); proposal không phải tuyên bố mọi
chức năng đã hoàn tất.
