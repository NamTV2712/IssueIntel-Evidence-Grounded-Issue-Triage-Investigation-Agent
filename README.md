# IssueIntel

Trợ lý điều tra issue bằng snapshot Git, code graph, bằng chứng có nguồn và các giả thuyết cạnh tranh. Demo và IssueIntel-Bench dùng cùng một pipeline chỉ đọc. [proposal.md](proposal.md) là thiết kế sản phẩm; [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) mô tả các ranh giới code.

## Chạy trên Windows

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,data,llm]"
# Tạo .env theo .env.example nếu chưa có; thêm GROQ_API_KEY trên máy.
.\.venv\Scripts\python.exe -m issueintel bootstrap
.\.venv\Scripts\python.exe -m issueintel status
.\.venv\Scripts\python.exe -m pytest -q
```

Lõi dùng thư viện chuẩn Python. `data` để nhập Parquet; `dev` để kiểm thử; `llm` thêm tokenizer gốc cho các input lớn trên Groq. Không cần PostgreSQL, Docker, Torch hoặc GPU để chạy lõi.

## Phase hiện hành: 400 Main Dev gốc

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
