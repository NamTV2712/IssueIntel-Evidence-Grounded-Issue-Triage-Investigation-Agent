# Bộ nhớ repository GitHub

`integrations.github_index.RepositoryIndex` là service dùng chung cho việc khởi
tạo repository public. Chỉ gửi GET GitHub và fetch một revision Git vào bare
cache. Không checkout/chạy code đích, không gọi LLM, không đọc benchmark/gold và
không ghi GitHub. Token `GITHUB_TOKEN` được nạp từ `.env` bởi CLI, không lưu trong
index hay báo cáo. Không hỗ trợ private repository ở phase này.

## Đồng bộ và tiếp tục

Connection tự động dùng `github_sync`: backfill liên tiếp theo đợt 20 GET trên
cùng queue, có cooldown rate limit/network qua restart. Không tự gọi LLM. HMAC
installation events cần App ID hoặc installation ID đã cấu hình; các repo public
được chọn tự kết nối. Repo hook chỉ cập nhật connection đã tồn tại, kiểm global
ID và installation binding. Tắt/gỡ installation tạm dừng; dữ liệu không bị xóa.
`repository_updates` lưu delivery hash, page cursor và canonical API receipts;
current snapshot giữ riêng baseline scan để không đổi nguồn cũ. Chỉ push đúng
tracked branch mới resolve code hiện tại. Lỗi identity/integrity cần kiểm tra
trước khi bật tiếp. Các mô tả manual bên dưới áp dụng cho nút/lệnh manual;
connection tự động tiếp tục trong khi local server còn chạy.

Trong workbench, mở **Bộ nhớ repository GitHub**, nhập `owner/repo`, revision và
số GET mỗi lượt, bấm **Đồng bộ / tiếp tục**. Job dùng chung hàng đợi một worker,
capacity 4 với investigation; lịch sử đồng bộ giữ riêng, không thành investigation
pack hay được phép publish comment. Hết ngân sách GET là một invocation hoàn
tất nhưng scan có thể vẫn pending; UI báo rõ để tiếp tục chủ động. Checkbox
refresh chỉ áp dụng cho lượt vừa gửi và tự bỏ chọn sau khi server nhận job.

**Dừng sau trang hiện tại** kiểm trước request/stage kế tiếp. Request đã bắt đầu
có thể hoàn tất và được commit trước khi dừng. Sau restart không tự tiếp tục;
**Xem trạng thái local** đọc checkpoint hiện có. Reload/đổi ngôn ngữ/theme,
tìm kiếm và mở lại nguồn không gọi GitHub hoặc LLM.

Search có code links tại exact commit/span và issue/PR/comment links kèm source
hash. **Điền issue này vào form điều tra** chỉ chọn URL và snapshot revision,
tắt Groq; cần bấm điều tra riêng. Form GitHub đọc lại issue hiện tại khi chạy,
không giả định nội dung đã lưu còn mới hay là opening version.

```powershell
.\.venv\Scripts\python.exe -m issueintel sync-repository --repo owner/repo --revision HEAD --max-requests 50
.\.venv\Scripts\python.exe -m issueintel repository-status --repo owner/repo
# Chạy lại đúng repo/revision để tiếp tục; scan đã xong sẽ không tự tải lại.
.\.venv\Scripts\python.exe -m issueintel sync-repository --repo owner/repo --revision HEAD --max-requests 50
# Sau khi hoàn tất, yêu cầu quét đối soát mới và resolve HEAD lần nữa.
.\.venv\Scripts\python.exe -m issueintel sync-repository --repo owner/repo --revision HEAD --refresh
```

Mỗi invocation kiểm lại public/global repository ID trước khi lấy trang tiếp.
Rename/transfer/ID bị thay đổi sẽ dừng, cần kết nối tên mới rõ ràng; không âm thầm
trộn hai lịch sử. Pagination chỉ theo cùng endpoint/filter, page tăng một; cho
phép alias `/repositories/<id>` khi ID đã được kiểm. Không theo redirect.

Các stream lấy cả trạng thái open/closed: issues (có cả PR), issue comments,
review comments, pulls, rồi files/commits của từng PR. Trường `kind` phân biệt
issue và PR. PR giữ base/head/merge SHA nếu API cung cấp; file giữ filename,
previous filename, blob SHA và trạng thái; commit giữ SHA/message. **Không lưu
diff/patch**. Comment giữ issue/PR đích; review comment thêm path/commit. `#NNN`
trong text là mention chưa xác minh, không phải closing link hay duplicate.

Response hash, URL, thời điểm fetch, normalized-record hashes và next cursor nằm
trong receipt; page records + receipt + cursor commit trong cùng SQLite
transaction. Trang lỗi không tiến cursor. OS lock ngăn hai writer; process chết
thì lock tự nhả. Không retry mạng tự động. Chạy lại sau lỗi tiếp tục đúng trang;
snapshot đã hoàn tất được replay hash, không resolve HEAD lại giữa cùng scan.

`--max-requests` là ngân sách GET mỗi invocation, mặc định 50, cho phép 1–500.
Metadata GET tính một request. Giá trị 1 chỉ đủ kiểm định danh và chuẩn bị
snapshot; chọn ít nhất 2 để tiến thêm trang. Git fetch dùng giới hạn timeout
riêng của acquisition. REST mỗi trang tối đa 100 records/10 MB, text tối đa
120.000 ký tự; quá giới hạn dừng rõ ràng, không cắt nguồn rồi báo đầy đủ.

`scan_complete` chỉ có nghĩa đã đi hết next links của các stream trong scan.
Nó không chứng minh toàn bộ lịch sử GitHub có mặt hay dữ liệu nhất quán tại một
thời điểm. PR commits API giới hạn 250, files 3.000: chạm ngưỡng sẽ ghi
`run.api_caps`, kể cả khi API không trả next link. Các record lỗi/không truy cập
được giữ scan pending. Không bỏ qua lỗi để báo complete.

Refresh hiện **quét đối soát toàn bộ**, upsert theo identity, giữ version cũ;
chưa có tối ưu incremental theo webhook. Record không xuất hiện lại vẫn giữ
trong local history: không coi sự vắng mặt là proof đã xóa. Search trả
`last_seen_run` để phân biệt nguồn cũ. GitHub sửa dữ liệu trong lúc phân trang có
thể làm kết quả dịch chuyển; không tuyên bố một bản export nguyên tử.

## Tìm kiếm và nguồn dẫn

```powershell
.\.venv\Scripts\python.exe -m issueintel search-repository --repo owner/repo --query "unicode parser"
.\.venv\Scripts\python.exe -m issueintel search-repository --repo owner/repo --query "parse_input" --code
.\.venv\Scripts\python.exe -m issueintel repository-item --repo owner/repo --key pull:34
```

Search text dùng SQLite FTS5/BM25 local (1–20 kết quả), có snippet/hash, URL,
record/page receipt SHA và fetched_at. Tìm code dùng shared lexical/symbol
retrieval tại đúng snapshot và replay graph/snapshot hash. Hai loại search đều
không gọi mạng/model. Điểm search không phải xác suất đúng. Item đọc lại nguồn
đã lưu và kiểm record/receipt binding trước khi trả nội dung. Product GitHub
investigation trên connection đang bật dùng bounded retrieval tối đa 6 nguồn/
7.200 ký tự, có source/query/receipt replay. Full source/receipt ở pack local;
model chỉ nhận excerpt và locator. Benchmark/strict/prepared inputs không nhận
memory hiện tại.

## Dữ liệu và phạm vi

- `data/repository_index/<repo-key>/index.sqlite`: private records, versions,
  page receipts, FTS và trạng thái scan; `writer.lock` không chứa credential.
- `data/derived/repository_index/`: snapshot blob gzip dùng chung và graph theo
  hash; snapshot giữ revision, skipped files và giới hạn hiện có.
- `data/repos/`: bare revision cache hiện có, không full clone lịch sử.

Nội dung REST hiện tại có thể đã sửa sau ngày mở issue. Index này không phải
opening-version proof, không được đọc trong Main Dev strict hoặc Holdout.
Không chạm benchmark receipts/release/freeze và không mở evaluation. CI dùng
Git/HTTP tổng hợp. Lõi dùng standard library, cần SQLite có FTS5 (đã có trong
Python runtime dự án); không thêm vector DB hay dependency mới.

Chưa bao gồm review threads đầy đủ, timeline/closing events, reactions hoặc full
Git history. App/HTTPS relay thật chưa được cấu hình trên máy. Installation
payload giới hạn 100 repo public mỗi delivery; nguồn private được bỏ qua. HTTP
webhook nhận tối đa 2 MB; payload vượt giới hạn cần đối soát lại.

## Local API

Các route cần session token, Host/Origin localhost và JSON bounds hiện có:

- `POST /api/repositories/sync`: repository, revision, max_requests, refresh;
  trả job ID (202), chỉ sync read-only, không nhận provider/split.
- `GET /api/repositories/jobs`: 20 lượt đồng bộ gần nhất, không chứa request
  nội bộ/credentials. `GET /api/jobs/<id>` theo dõi tiến độ local.
- `POST /api/repositories/status`: repository; đọc coverage local.
- `POST /api/repositories/search`: repository, query, code (boolean), limit.
- `POST /api/repositories/item`: repository, key; đọc source/receipt đã kiểm.
- `POST /api/jobs/<id>/cancel`: `{}`; dừng trước stage/request kế tiếp.

Nguồn chính thức đã đối chiếu ngày 15/10/2026, API version `2026-03-10`:
[repository issues](https://docs.github.com/en/rest/issues/issues#list-repository-issues),
[issue comments](https://docs.github.com/en/rest/issues/comments#list-issue-comments-for-a-repository),
[pull requests/files/commits](https://docs.github.com/en/rest/pulls/pulls),
[REST pagination](https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api).
