# Phục hồi proof cho 107 Main Dev pending

Theo yêu cầu tác giả ngày 13/10/2026, đây là ưu tiên duy nhất hiện hành. Bulk LLM
dừng ở 69 investigation pack hoàn chỉnh; giữ mọi output, receipt và started
intent để reuse/recovery sau. Không gọi Groq trong phase proof recovery.

Chỉ 107 ca pending thuộc 400 Main Dev ban đầu được xử lý: 19 Tier B rồi 88 Tier C;
không còn Tier A pending. Không nhập task khác, đọc holdout annotations, đổi split,
mở evaluation hoặc dùng gold/human rationale để quyết định eligibility.

Tác giả xác nhận giữ đúng 400 ca gốc và chỉ nhận ca có proof hợp lệ. Không thay ca
không đạt bằng task mới. BigQuery đã bị hủy trước khi chạy bất kỳ query job nào;
quyết định này được ghi riêng trong `configs/strict_recovery_scope_decision_2026-10-13.json`.

Baseline đã đóng băng: 293 accepted, 107 pending, 0 excluded. Giữ nguyên
`main-dev-strict-v1` và tất cả hash/receipt. Ca phục hồi có receipt riêng, chỉ
được đưa vào cohort kế tiếp khi replay được proof và snapshot. Không sửa membership
của cohort cũ trong im lặng.

## Điều kiện được giữ

- Issue creation độc lập, không lấy PR creation thay thế.
- Query là phiên bản lúc mở; raw opened event cần `updated_at == created_at`, hoặc
  official Trac version 0 được replay. Không bỏ qua mơ hồ lịch sử.
- Publication của snapshot phải độc lập và **available_at < issue.created_at**.
- Selected snapshot thuộc ancestry của base nguồn; mọi raw commit object được
  kiểm SHA1 và parent link. Git dates chỉ lên kế hoạch tìm archive.
- Source case/base/query, raw event và snapshot/tree/blob hashes được giữ/bind.
- Repo đổi tên/chuyển chủ chỉ được nối khi numeric repository ID của raw event
  khớp official repository identity fact; issue ID/number/creation cũng phải khớp.
  Không đổi tên trong raw event hoặc sửa raw hash để làm validator pass.

## Các giới hạn tra cứu cũ chưa đủ để kết luận

71 ca snapshot pending đã dừng ở 256 ancestor; nhiều ca chưa tới commit trước
issue. Tìm sâu hơn không thay việc chứng minh publication. Recovery lưu raw parent
chain dài hơn, giữ cùng temporal/source criteria. Safety cap hiện hành vẫn là
giới hạn tài nguyên; chạm cap không phải exhaustion.

Một số archive scan dừng do line/byte/time cap. Recovery retry full selected hours
với bound lớn hơn, ghi EOF/coverage, các giờ đã kiểm, cap/error và phần frontier
còn chưa kiểm. `coverage_complete` của một giờ không chứng minh hết mọi giờ.

Recovery hiện đã tìm first-parent tới 16.384 commit và mở frontier tới 72 giờ
cho các ca chưa có snapshot; đây vẫn là search bounds, không phải exhaustion.
Raw commit được đọc theo batch có kiểm framing/size/SHA1/parent để giảm thời gian.
Snapshot được materialize bằng fetch exact revision trước khi chạy builder chung;
Git lazy fetch bị tắt trong builder, process tree được dừng khi fetch timeout.
Materialization giữ nguyên file shallow boundary có trước khi tải, kể cả khi
fetch lỗi. Fetch depth 1 không được làm lần truy hồi ancestry kế tiếp bị cắt.
Cache đã bị cắt trước sửa lỗi cần audit/repair riêng; không dùng traversal ngắn
để suy ra commit không tồn tại hoặc để đánh dấu search complete.

## Lịch sử nội dung từ GitHub API

`UserContentEdit.diff` được tài liệu mô tả là summary. API ở các ca đã kiểm có
trả cả nội dung từng phiên bản. Chỉ một giá trị trong trường này chưa được tính
là proof. Nhánh `official_initial_edit_snapshot_v1` yêu cầu đồng thời:

- Raw response bytes/hash, original case, repo/issue ID và source selection khớp.
- Paginate hết các phiên bản trả về; không có node bị xóa, thiếu hoặc mơ hồ.
- Bản mới nhất khớp nguyên văn toàn bộ `issue.body` hiện tại.
- Có **stored version cụ thể** với `editedAt == issue.createdAt`; zero edits
  không được suy ra là proof của bản mở issue.
- Replay bổ sung phải có `Issue.includesCreatedEdit == true` trong raw official
  response ở mọi page. Field này xác nhận có creation data; chỉ kiểm timestamp
  của node và latest-body equality chưa đủ. Giữ nguyên initial ID, toàn bộ body,
  title, identity và original proof giữa receipt cũ và attestation mới.
- Lịch sử đổi tiêu đề đủ và replay được từ tiêu đề hiện tại về tiêu đề ban đầu.
- Raw ancestry và witness công khai của snapshot vẫn cần **strict `<`** creation.

Đây là attestation hồi cứu của một stored initial revision. `createdAt` của
record lịch sử có thể muộn hơn creation của issue; ghi nguyên ngày đó, không
đổi thành thời điểm capture sớm và không dựng một `IssuesEvent` giả. Source case,
source query hash và raw bytes được giữ nguyên. Chất lượng nhãn người của query
SWE-bench nguồn không được chuyển sang opening-text overlay.

Collector chỉ đọc GraphQL query cố định cho original pending Dev, lưu raw response
riêng; không mutation, gọi model hay đọc gold. Có fixture kiểm zero/deleted/partial
history, summary không khớp body, sai ID, lỗi title chain và snapshot bằng thời gian.

Guard bổ sung nằm trong `creation_attestation.py`; receipt/docs/code được bind
riêng vào checkpoint, không sửa report hoặc hash đã publish. Audit độc lập lấy
được creation-data attestation cho 7/7 ca đã phục hồi bằng API. Nguồn ngữ nghĩa:
[Issue GraphQL](https://docs.github.com/en/graphql/reference/issues) và
[chính sách bảo toàn original content ngày 02/07/2026](https://github.blog/changelog/2026-07-02-issue-fields-are-now-generally-available/).
Những ca không có creation data vẫn unknown; policy không được dùng để dựng
initial body từ current body ở history rỗng.

[GH Archive](https://www.gharchive.org/) có dữ liệu từ 12/02/2011; trước 2015 dùng
Timeline API với `repository` và `payload.shas`, sau đó dùng Events API với `repo`
và `payload.commits`. Các schema được đọc riêng, raw bytes không được normalize
thành một event giả. Ngày trước source inception cần nguồn độc lập khác, không
được loại task chỉ vì HTTP 404 từ một nguồn chưa tồn tại.

Raw sample `2014-10-31-21` có `created_at=2014-10-31T21:05:42-07:00`.
Một số filename cũ theo wall clock ghi ngay trong offset của event. Planner thử
thêm label offsets -7/-8; validator so filename với UTC hoặc native wall clock
của **raw timestamp có offset**, rồi vẫn so temporal eligibility bằng aware
instants. Không thay timezone trên dữ liệu nguồn hoặc sửa raw hash. Test riêng
đảm bảo `15:13-07:00` nối được với `22:13Z`, nhưng `15:13Z` bị từ chối.

Một raw opened event cũ của Requests chỉ có `action`, `issue` (global ID dạng
scalar), `number`. Source này giúp kiểm identity/time; không chứa title/body
hay initial-content version, nên không thể tự làm proof nội dung. Không điền
body hiện tại vào event đó. Các lần scan còn tìm payload đầu có
`updated_at == created_at` trong cả giờ, tránh dừng ở bản feed copy đã cập nhật.

[GitHub redirects](https://docs.github.com/en/repositories/creating-and-managing-repositories/renaming-a-repository)
giúp tìm tên hiện hành; redirect hoặc tên tương tự riêng lẻ chưa đủ identity proof.
Phải kiểm numeric ID và raw event/issue identity. Tên cũ có thể được dùng lại.

## Issue chuyển sang repository khác

Chuyển issue có thể đổi cả issue global ID và number. Nhánh
`transferred_initial_issue_archive_v1` giữ riêng origin và assignment hiện hành:

- Raw GHArchive chứa toàn bộ title/body lúc mở ở repository cũ, với
  `created_at == updated_at`; không chép nội dung hiện tại vào event.
- Official GraphQL có đúng một `TransferredEvent`, EOF, `fromRepository` và
  issue đích khớp numeric identities, ngày mở và assignment đã đăng ký.
- Official API của issue cũ trả 301; cả Location đã parse và raw JSON response
  trỏ đúng issue hiện hành. Receipt ghi rõ header được lưu dưới dạng parsed,
  không tự nhận có raw wire header bytes.
- Ba primary source files phải hash-check và khớp toàn bộ object embedded trong
  proof. Cả identity origin lẫn identity hiện hành được kiểm against reserved issues.
- Code publication vẫn phải thuộc numeric repository của code nguồn, đúng SHA
  trong raw PushEvent, trước ngày mở issue gốc. Publication ở repository chứa
  issue cũ không thể thay bằng chứng của code repository.
- Raw ancestry tới base, snapshot/tree/blob và query bytes được replay như các
  nhánh khác. Không đổi original case, mapping, cutoff hoặc receipt cũ.

Nhánh này chỉ hỗ trợ lineage một transfer đã chứng minh đầy đủ. Nhiều transfer,
conversion không rõ hoặc timestamp mơ hồ vẫn pending.

## Kết thúc phase

Inventory và wave receipts là machine-readable trong ignored
`data/development/recovery/` và `artifacts/strict_recovery/`. Mỗi pending cần ghi
opening, snapshot, rename/transfer, query/source, receipt/hash và ambiguity paths,
URL/lookup phạm vi, kết quả, coverage, cap/access failures và bước còn mở.

Không đánh dấu phase complete dưới 400 khi còn admissible path chưa kiểm hoặc bị
access/cap block. Không gọi bounded no-match là chứng minh proof không tồn tại.
Nếu task không thể đạt contract hiện hành, phải ghi constraint conflict bằng dữ
liệu đã kiểm, ví dụ source là PR nhưng chưa xác định issue độc lập, hoặc nhiều
issue không cùng tồn tại ở opening time. Hiện chưa tuyên bố exhaustion cho 107 ca.
