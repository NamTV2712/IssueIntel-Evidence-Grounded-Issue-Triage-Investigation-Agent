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

## Workbench chạy local

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
Planned chưa hỗ trợ Main Dev, follow-up hoặc tiếp tục paid stage sau lỗi.
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
Phần **So sánh hai phiên điều tra** đối chiếu các báo cáo đã lưu của cùng issue:
query/snapshot, evidence và hypotheses. Đổi context cần gắn lại citation;
khác biệt giữa hai kết quả không được coi là đã cải thiện chất lượng hay sửa lỗi.
Thanh đầu trang hỗ trợ **Tiếng Việt / English** và **sáng / tối / theo hệ thống**,
nhớ lựa chọn trong browser. Đổi ngôn ngữ không gọi model hoặc dịch nội dung nguồn.
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
Với báo cáo issue GitHub, mục **Chọn nguồn cho điều tra** tìm trong
repository đã đồng bộ. Xem trích đoạn rồi chọn tối đa sáu nguồn; phiên mới giữ
query và commit gốc, chỉ dùng đúng các nguồn đã chọn. Nội dung/receipt đổi sẽ yêu
cầu tìm lại; Groq mặc định tắt. Đây là context hiện tại, chưa xác minh duplicate
hoặc lịch sử. Báo cáo và receipt gốc được giữ nguyên.

Tab **Kế hoạch kiểm tra / Check plan** lưu trạng thái và ghi chú theo phiên bản,
mở lại sau restart và xuất JSON riêng. Trạng thái hoàn tất là tự báo cáo, chưa
xác minh bản sửa; hệ thống không chạy code/test của repository đích.

Tab **Đồ thị bằng chứng / Evidence graph** đọc báo cáo đã lưu, phân biệt nguồn
truy xuất, citation hỗ trợ/phản bác và nhận xét model. Chọn nút bằng chuột hoặc
bàn phím để xem trích đoạn/hash, lọc quan hệ, mở citation về báo cáo và xuất JSON.
Không gọi lại LLM; đường nối không chứng minh nguyên nhân hay kiểm người.

Tab **Token và chi phí** và mục chi phí bên dưới tiến độ hiển thị receipt của
đúng phiên, cả khi job lỗi hoặc interrupted. Mỗi reservation được gắn về job
trước HTTP; lỗi/thiếu usage giữ cận trên. Có export JSON riêng; phiên cũ không
có receipt hiện unknown. Đây là số liệu ghi nhận/giữ chỗ, không phải hóa đơn.

Mục **Đối chiếu phiên bản code** dưới báo cáo product nhận commit/tag khác, đọc
snapshot và lưu riêng thay đổi code cùng hash nguồn/đích. Diff ưu tiên file trong
evidence; file bị skip giữ ngoài phạm vi, không báo nhầm đã xóa. Có thể thêm
**Commit tham chiếu** để kiểm phiên bản đích có chứa commit đó không và xem tag
Git local chứa nó. Quan hệ thiếu lịch sử giữ unknown; không tự tải thêm lịch sử
hoặc toàn bộ tag. Tag local chưa chứng minh bản phát hành đã công bố hay lỗi đã
sửa. Không gọi model/chạy test. Main Dev/strict và follow-up không dùng luồng này;
bản báo cáo gốc giữ nguyên.

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
GitHub App/relay HTTPS ngoài máy vẫn cần thiết lập một lần; bản local không tự
công khai workbench. Phần đồng bộ thủ công bên dưới vẫn dùng được độc lập.

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

## Dữ liệu: 400 Main Dev gốc

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

Jev có cổng quyết định typed và fallback theo luật; transport/dịch vụ Jev chưa được cấu hình. Không dùng ngân sách Groq để tự gọi dịch vụ khác. Các tính năng sản phẩm mở rộng trong proposal vẫn thuộc roadmap, không được coi đã triển khai.
