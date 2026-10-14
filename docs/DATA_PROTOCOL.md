# IssueIntel-Bench: giao thức dữ liệu

## Nguồn bất biến

| Nguồn | Revision | Vai trò |
|---|---|---|
| [princeton-nlp/SWE-bench_Verified](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified) | `c104f840cc67f8b6eec6f759ebc8b2693d585d4a` | 500 original-format tasks |
| [SWE-bench-Live](https://huggingface.co/datasets/SWE-bench-Live/SWE-bench-Live) | `b51a86422e10cfd403beb4773e5a2947953e36ec` | full source, chọn 300 ứng viên |
| [OpenAI source human annotations](https://openai.com/index/introducing-swe-bench-verified/) | ZIP SHA256 `b9297c31f6c2ef712218b5bb2830bd47a7f600dedf6b927191d0170177266ab9` | Ordinal underspecification gốc |

File exact sizes/hash/download URLs nằm trong `data/manifests/sources.json`. Không tải branch HEAD. Import mới khác cohort không được ghi đè cohort đã có; identical bootstrap idempotent. Một ID Live (`conan-io__conan-18153`) có hai record khác metadata; cả hai được loại và ghi trong selection audit.

Main: 400 dev, 100 holdout. Nhóm repository+base_commit (và explicit parent khi có) không bị tách; seed 42. Đây là split của IssueIntel, không đổi tên official SWE-bench test thành một bộ training độc lập. Variant luôn chung split với parent. Live `full/test/verified/lite` overlap, không cộng số lượng các split.

## Bốn lớp và trạng thái

1. Original task: public problem_statement/repository/base_commit; patch/tests/hints riêng trong gold/upstream.
2. Automated assets: snapshot/AST graph, localization và leakage/build receipt. `benchmark_base` không được mô tả là snapshot đã chứng minh tồn tại trước issue. File thiếu/snapshot bị cắt được ghi rõ.
3. LLM overlay: 3–5 hypotheses, support/counter IDs, falsification test, annotation provenance. Thiếu phản chứng ghi `not_found`; không tạo bằng chứng giả để đủ số. Correctness ban đầu unverified. Judge gold riêng chỉ cho nhãn reference-relative tự động.
4. Answerability: ordinal human clarity giữ nguyên và synthetic variants có parent hash/split. Severity 1 không tự đổi thành abstain. Xóa evidence/chèn conflict tạo controlled target `abstain`, nhưng validation vẫn unreviewed vì evidence còn lại có thể đủ trả lời.

Manifest bootstrap ghi trạng thái lúc nhập, không cập nhật lại hash theo một annotation run. Overlay/receipts và `issueintel status` ghi số thực tế hiện tại. Có 800 task không đồng nghĩa 800 task đủ bốn lớp.

## Temporal test

Model `openai/gpt-oss-120b`: [model card](https://cdn.openai.com/pdf/419b6906-9da6-406c-a19d-1bb078ac7637/oai_gpt-oss_model_card.pdf) ghi cutoff June 2024. Dùng `2024-07-01T00:00:00Z` làm ranh giới bảo thủ, không bịa ngày chính xác.

Created_at upstream là ngày tạo PR theo [SWE-bench builder](https://github.com/SWE-bench/SWE-bench/blob/main/swebench/collect/build_dataset.py) và [Live builder](https://github.com/microsoft/SWE-bench-Live/blob/python-only/curation/swe_task_crawling/build_dataset.py). Chọn theo PR date chỉ tạo candidate. Audit phải kiểm mọi issue được liên kết, thời điểm mở thật, text tại thời điểm query và bằng chứng public availability của đúng base revision. Git author/committer date không đủ.

API issue GET phục vụ metadata enrichment từng task, không crawl một corpus mới. Main Django lấy ticket từ Trac; số ticket không được đổi thành số GitHub issue. Ánh xạ giữ riêng exact source-text match, quan hệ PR khai báo đóng issue/ticket, và các ca thiếu hoặc mơ hồ. Quan hệ khai báo là lineage tự động, không phải kiểm người cho query mới.

Receipt cũ không bị sửa; audit event mới append và buộc vào SHA manifest/receipt bundle. Case có issue trước cutoff bị loại khỏi temporal eligibility. Chưa đủ proof thì pending, không nâng thành validated. Post-cutoff cũng không chứng minh model không từng thấy task qua fine-tuning hoặc bản host khác.

### Query ban đầu và snapshot lịch sử

Tác giả đã chọn `original_opening_query` ngày 12/10/2026. Query tại lúc mở được giữ trong overlay riêng có parent case, source-query SHA, selected-query SHA, thời điểm, split và receipt. File SWE-bench gốc, base commit và restricted gold vẫn bất biến. `source_query` giữ hành vi đối chiếu nguyên mô tả nguồn; không tự chuyển ca mismatch sang exact match.

GitHub dùng opened event trong [GH Archive](https://www.gharchive.org/). Trac dùng trang version 0, raw HTML có hash, parse lại và timestamp mở ticket. Receipt query phải đúng danh tính và phiên bản ban đầu. Query có thể bằng thời điểm mở; toàn bộ code phải available **trước** thời điểm đó. Query ghép từ các issue mở vào những thời điểm khác nhau không được coi cùng tồn tại ở mốc sớm nhất.

Với GitHub, query ban đầu được tái dựng theo ngữ nghĩa `IssuesEvent/action=opened`; timestamp của event có thể muộn hơn `issue.created_at` do độ trễ phát sự kiện. Vì vậy đây là bằng chứng về phiên bản lúc mở theo nguồn GitHub, không phải bằng chứng archive đã công bố payload đúng ngay giây mở issue. Mốc archive/event và mốc mở phải được giữ riêng. Receipt dựa vào tính đúng đắn của nguồn HTTP và ngữ nghĩa event, không có chữ ký mật mã xác nhận lịch sử.

Snapshot có thể chọn ancestor của base SWE-bench. Chuỗi raw Git commit objects chứng minh ancestry; public PushEvent hoặc receipt hợp lệ khác chứng minh availability. Git committer date chỉ gợi ý giờ tìm archive. Giữ riêng upstream base và selected revision. Patch localization vẫn giữ toàn bộ file trong mẫu số; line/symbol phải có hunk khớp chính xác. Insertion không có old-side context vào file đã đổi giữ unresolved, không chép tọa độ base.

Nhãn clarity và kiểm task gốc của SWE-bench chỉ áp dụng `upstream_query_only`. Mô tả ban đầu và hypotheses/variants mới chưa có independent human validation. Không suy ra nhãn answerability cho query mới từ ordinal của query nguồn.

Archive search có giới hạn byte/record/time/giờ. Chỉ giữ event khớp và provenance; hash prefix không được gọi là hash toàn archive. Không tìm thấy trong phần đã đọc không chứng minh không tồn tại. Pending attempts được giữ và không tự tải lại cùng phạm vi; retry lịch sử cần lựa chọn rõ ràng.

### Registration và annotation stages

`register-annotation` khóa cohort nguồn, protocol/query policy, model/output bounds, prompt/schema/code và config trước call. ZIP code/config có inventory/hash giúp giữ source của lần chạy. Không sửa registration cũ theo refactor và không nhận nhãn pilot làm nhãn đã preregister.

`annotate-dataset` dùng cùng pipeline public với sản phẩm, rồi mới truyền gold cho judge offline. Generation, inputs, critique, judged annotation, localization, answerability và variants là stage artifacts riêng có dependency/hash; generation không bị ghi đè bởi judgment. Unknown historical proof được ghi pending trước call. Recipe mới là construction protocol, không phải registration cho một thí nghiệm đánh giá holdout.

### Release có hash

`register-release --recipe <SHA>` chốt một manifest mới trong `data/benchmark/releases/`, không sửa manifest nhập nguồn. Mỗi ca được nhận phải có proof `issue_time` cho đúng query ban đầu, snapshot đã chọn và đủ generation/critique/judge/variants. Pilot hoặc recipe đã bị đánh dấu diagnostic không được nhận vào release chính.

Release giữ riêng exclusions do thiếu proof và do annotation chưa hoàn tất, số ca theo split, source/recipe/archive hashes và phạm vi nhãn tự động. Exclusions là trạng thái tại lúc chốt; hoàn tất thêm ca sẽ tạo release khác. Không tăng số ca hoàn tất chỉ vì ca đã có asset `benchmark_base`. `verify-release --release <SHA>` replay các binding trước khi dùng; kết quả cho inference và đường dẫn restricted labels được tách riêng.

Release không tự cấp quyền evaluation. Đặc biệt, 300 ứng viên Live trong bootstrap không tự trở thành 300 temporal test: ngày issue, query ban đầu, availability và annotation đều phải đủ. Không thay hash nguồn hoặc cờ bootstrap để bỏ qua cổng này.

### Cohort bổ sung

`benchmarks.cohorts` đăng ký và kiểm một selection Live bổ sung theo nguồn pin, parent manifest và metadata receipts. Cases, eligibility và restricted gold nằm riêng trong `data/benchmark/cohorts/<SHA>/`; `load_cohort_view` yêu cầu chọn namespace rõ ràng. Không append vào 800 ca bootstrap hoặc đổi recipe đang chạy. CLI có `register-cohort --selection <SHA>` và `verify-cohort --cohort <SHA>`.

Cohort này là input cho dataset construction, chưa phải strict temporal release. Loader dev/test mặc định và builder hiện tại vẫn dùng bootstrap. Tích hợp cohort view vào historical/annotation builders cần một phiên bản code và recipe riêng; thiếu proof, repo alias chưa xác minh hoặc issue trước cutoff vẫn được khai báo riêng.

## Đánh giá

Development dùng `load_dev_cases()` và dev gold ở evaluator/dataset construction. `open_test_split()` yêu cầu câu ủy quyền evaluation rõ ràng và purpose, ràng buộc manifest, thu hồi capability khi hết context. Đây là audit gate trong application, không phải OS sandbox. Không chạy holdout prediction/metric trong quá trình phát triển hiện tại.

Entry point `issueintel.evaluation.open_test_split(..., release_sha256=<SHA>)` mở đúng các ca đủ điều kiện và đủ stage trong release đã kiểm chứng, sau ủy quyền evaluation mới. Capability kiểm lại release mỗi lần đọc, tách cases/gold/labels và ghi source/recipe/release/cohort hashes vào log. Không truyền release thì dùng cổng source-cohort cũ; temporal bootstrap vẫn bị chặn. Module evaluation được tách khỏi recipe generation để việc bổ sung cổng đọc không đổi nhãn đã tạo.

Recall@K dùng tất cả file gold patch, kể cả ngoài snapshot; báo thêm unavailable. All-files@K khác Recall@K. MRR là vị trí file đúng đầu tiên. Function/line gold là patch-derived proxy, không phải root-cause causal label. Không tính citation correctness chỉ bằng việc tồn tại ID, không tính IAA từ hai lượt agent/model.

## Dung lượng và nộp bài

Upstream khoảng 103 MB; gold projection có metadata/test/hints dài nên cũng lớn. Git/source assets tăng theo số revision; blob cache và graph gzip giảm lặp. Các file này có mục đích tái lập, không phải tài liệu để gửi giảng viên. `.venv`, Git repos và generated packs là cache/runtime có thể dựng lại; tránh xóa ledger ngân sách trong kỳ còn hiệu lực.

Nộp code, proposal, configs mẫu, tests và docs. Không nộp `.env`, data, .venv, .git hoặc raw issue content. Gói review nhẹ chỉ cần code/docs và báo cáo số đo/provenance; dữ liệu private không được công khai tự động.
