# Sản phẩm theo proposal.md

Luồng sản phẩm gọi `investigation.pipeline.investigate()` dùng chung với benchmark:
issue → snapshot Git chỉ đọc → AST graph → evidence → competing hypotheses →
verifier → investigation pack. `investigation.workbench` quản lý job/lịch sử;
`interfaces.web` chỉ cung cấp HTTP và giao diện localhost.

## Chạy thử

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,data,llm]"
.\.venv\Scripts\python.exe -m issueintel serve
```

Mở `http://127.0.0.1:8787`. **Thử ví dụ tổng hợp** tạo repository Python nhỏ trong
`artifacts/demo/`, đọc Git objects và xuất evidence; không tải benchmark hay gọi
API. Với issue tự nhập, cần repository local hoặc `owner/repo` và revision rõ ràng.
Đầu vào này dùng `benchmark_base`, không nhận nhãn strict lịch sử.

Main Dev chỉ liệt kê accepted thuộc 400 ca gốc từ cohort bất biến và checkpoint
phục hồi hash-bound. Trước khi chạy mỗi ca, loader replay eligibility, query ban
đầu, nguồn, proof và snapshot. Nó không đọc gold, human rationale, Holdout hay
Live. Các ca recovered không được biểu diễn là cohort mới đã freeze.

Groq là tùy chọn do người dùng bật. Nó dùng `openai/gpt-oss-120b`, structured
output, generation và critique riêng, giá có ngày, giới hạn từng call và durable
budget ledger hiện có. Đây là lời gọi API thật; dữ liệu được gửi là query và code
excerpts. Không tự chạy LLM khi mở trang, mở lịch sử hoặc xuất báo cáo. Failure/
restart không tự retry call đã bắt đầu. Hủy được kiểm trước/sau call; call đang
ở provider có thể vẫn tính phí.

## Phạm vi chức năng

Danh sách dưới đây đối chiếu F01–F20 của proposal; không coi roadmap là kết quả
đã hoàn thành.

| Mã | Trạng thái hiện tại |
|---|---|
| F01 | Nhận issue/repo/revision; intake gợi ý bug/feature/question/unknown bằng lexical cues, nguồn dòng/hash và thông tin cần xem lại; không coi cue là phân loại đã xác minh. |
| F02 | Tìm ứng viên liên quan trong lịch sử local; repository memory ở UI/CLI thêm FTS trên issue/PR/comment đã đồng bộ. Chưa xác nhận duplicate, chưa tự đưa kết quả vào model. |
| F03 | Định vị file/symbol/dòng qua lexical, identifier và typed graph retrieval ở đúng snapshot. |
| F04 | Repo đã kết nối tự truy xuất tối đa 6 nguồn; người dùng cũng có thể tìm/chọn chính xác nguồn đã đồng bộ để tái điều tra cùng query/commit. Record, receipt và repository ID được replay; dùng trong generation/critique/export. Current context không phải temporal proof. |
| F05 | Job/trace/checklist có phiên bản; Deep thêm một vòng. Planned mới chạy một đến ba vòng có budget, tránh lặp query/evidence, chỉ sinh lại khi có nguồn mới và lưu chain/stop reason replay được. Tool điều tra mở rộng vẫn còn roadmap. |
| F06 | `JevController` typed allowed actions và rules fallback đã có; remote transport chưa cấu hình/đánh giá. |
| F07 | Generation 3–5 hypotheses, support/counter IDs; đồ thị tương tác từ báo cáo lưu sẵn tách truy xuất/citation hỗ trợ/citation phản bác/critique, có inspector và export. |
| F08 | Kiểm citation/hash, tìm thêm code theo giả thuyết ở Deep/Planned, critique riêng và abstention. Planned dừng rõ khi query/evidence không đổi. Kết quả tìm vẫn là candidate, counter có thể `not_found`. |
| F09 | Câu hỏi intake theo mẫu và câu hỏi riêng từ issue/code/giả thuyết đã lưu; Việt/Anh, có lý do hỏi/cách dùng câu trả lời/refs. Nhận trả lời một phần/“chưa biết”, tạo follow-up cùng commit, khóa và replay source pack/snapshot/question job/Q&A. Tạo câu hỏi qua một paid attempt được duyệt riêng; chất lượng phân biệt và dữ kiện mới vẫn unverified. |
| F10 | Durable job/artifact receipts, plan ledger và repository page checkpoints; product có tiếp tục riêng verifier sau replay checkpoint và duyệt chi phí, một intent cho mỗi nguồn. Chưa resume mọi stage; lỗi/interrupt không retry API ngầm. |
| F11 | Cache Git/snapshot/graph, follow-up lineage, saved-run retrieval; kết nối tự backfill/tiếp tục, webhook cập nhật canonical và tracked branch. Memory có as-of proof còn roadmap. |
| F12 | Quick lấy evidence gọn; Deep thêm tối đa một lượt. Planned chọn một đến ba vòng theo dấu hiệu file/gap/counter còn thiếu từ seed; tối đa chín Groq calls kể cả repair. Đây là heuristic quan sát, chưa phải đánh giá độ khó/chất lượng. Routing benchmark giữ policy/pilot riêng. |
| F13 | Trạng thái/hủy, token và USD theo phiên từ receipt riêng; đang chạy/lỗi/interrupted vẫn xem được cận giữ chỗ. |
| F14 | Lịch sử, mở lại pack và so sánh hai phiên cùng issue/repo: query/snapshot/proof bindings, evidence và hypotheses; điều tra tiếp với input mới vẫn tạo job riêng. |
| F15 | Issue URL/collaborator command; HMAC-signed installation/repository events tự onboarding public repos và incremental sync có durable inbox/dedup. App/HTTPS relay thật chưa cấu hình trên máy. |
| F16 | Preview comment có code links; exact-body approval, target binding và durable one-attempt intent trước POST. Không tự đăng. |
| F17 | Tổng quan và JSON/Markdown/nháp GitHub dùng chung report contract có schema/hash về pack/query/snapshot; tách model critique, citation và kiểm người, giữ nguyên unknown. Xem evidence/hypotheses/trace và pack JSON gốc; đồ thị có lọc quan hệ, chọn bằng bàn phím và mở citation về báo cáo. |
| F18 | Reservation trước HTTP gắn về job, cập nhật usage/price date sau call; tab chi phí/export riêng, không nhận tổng ledger thành chi phí phiên. Phiên cũ thiếu receipt giữ unknown. |
| F19 | HTTP API, một worker, hàng đợi tối đa 4; benchmark CLI có concurrency bounded. |
| F20 | Đối chiếu commit/tag, diff evidence và ancestry local; kiểm riêng một GitHub Release công khai theo tag với commit tham chiếu, giữ ID/receipt/hash và phân biệt draft/prerelease. Quan hệ hiện tại không chứng minh nội dung tag lúc công bố hoặc lỗi đã sửa. |

Đồ thị mặc định tập trung vào một giả thuyết và các cạnh trực tiếp; có tổng quan,
thẻ nguồn chữ thật, màu/nét theo loại quan hệ và cửa sổ phóng rộng với inspector.
Escape trả về báo cáo, chọn nút/đổi chế độ không gọi lại API hoặc LLM. Trên màn
hình hẹp chỉ vùng bản đồ cuộn ngang; nội dung và controls vẫn ở trong viewport.

## API và dữ liệu local

Tab **Tổng quan / Overview** là màn đầu khi mở báo cáo, gồm trích issue được ghi
rõ là lời người báo, giả thuyết cạnh tranh, bước kiểm tra chưa chạy và vị trí code
ứng viên. Mở rộng để xem nhận xét model, giới hạn và hashes nguồn. Citation dẫn
về evidence của đúng phiên; link GitHub pin đúng commit/dòng. Không tự sinh phần
tóm tắt mới hoặc điểm tin cậy. Các nút **Markdown**, **Report JSON** và **Pack JSON**
phân biệt bản trình bày với nguồn máy đọc gốc. UI đổi Việt/Anh; nội dung nguồn và
export giữ nguyên ngôn ngữ. Pack cũ đọc qua projection mới, không bị viết lại.

Giao diện hỗ trợ **Tiếng Việt / English** và **Light / Dark / System** từ thanh
đầu trang. Theo hệ thống phản ứng với đổi theme của OS; lựa chọn rõ ràng giữ
nguyên. Preferences được lưu bằng `issueintel.preferences.v1` trong browser,
chỉ gồm language/theme, không chứa issue, token hoặc kết quả. Storage bị chặn
vẫn dùng được. Đổi ngôn ngữ render lại dữ liệu đã có, không gửi lại job/call.
Issue/code/model output và JSON/Markdown export giữ nguyên nguồn và hash;
không tự dịch evidence hay thay prompt. Ngôn ngữ mặc định theo browser, fallback
English. Chuyển ngôn ngữ khi đang xem draft GitHub bỏ phần duyệt cũ; cần xem
và duyệt lại nội dung trước khi đăng.

Màu dùng semantic tokens chung giữa hai theme, có focus bàn phím và hỗ trợ
reduced motion. Đã đo 22 cặp màu text/control: text thấp nhất 5.43:1, control/
focus thấp nhất 3.83:1; helper light trước sửa `#778780` trên trắng chưa đạt
4.5:1. Đây là kiểm palette và các trạng thái được thử, chưa phải audit WCAG đầy
đủ. Ngưỡng đối chiếu [text contrast](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html)
và [non-text contrast](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html).

API cần session token của trang trong `X-IssueIntel-Token`. Origin/Host phải đúng
localhost; server không cung cấp CORS hay duyệt filesystem. Source excerpts được
render bằng `textContent`, không thực thi HTML/Markdown từ repository hoặc issue.

- `GET /api/catalog`: metadata accepted Main Dev; không chứa gold.
- `POST /api/investigations`: tạo job với `mode`, `provider`, `depth` và input.
- `POST /api/comparisons`: `before_id`, `after_id` của hai job đã hoàn tất cùng issue/repository; kiểm hash cả hai export, không gọi model.
- `POST /api/versions`: `job_id`, `revision`, tùy chọn `reference_revision`; job đối chiếu code và ancestry dùng chung queue/worker, không model hoặc thực thi code/test.
- `GET /api/jobs/<source>/versions`: tối đa 20 lượt đối chiếu của báo cáo nguồn.
- `GET /api/jobs/<comparison>/versions.json`: export riêng có hash và binding về pack nguồn.
- `POST /api/releases`: `job_id`, `tag` và `reference_commit` full SHA; kiểm đúng một release của nguồn GitHub product, không gọi model/tải Git history.
- `GET /api/jobs/<source>/releases`: tối đa 20 lượt kiểm release đã lưu, kể cả lỗi/hủy.
- `GET /api/jobs/<check>/releases.json`: export quan sát có receipt và binding về source pack/query/snapshot; replay không network/model.

- `POST /api/related`: `job_id` đã hoàn tất; tìm ứng viên từ các job local, không nhận scope/split/query override.
- `POST /api/context-search`: `job_id`, `query`; tìm tối đa 20 nguồn trong index của đúng repo GitHub của báo cáo, không network/model.
- `POST /api/investigations` với `mode: context`: `parent_job_id`, `parent_pack_sha256`, `selected_sources` (1–6 bộ key/record_sha256/receipt_sha256), `provider`, `depth`; giữ query/snapshot nguồn, không nhận repo/revision/query override.
- `GET /api/jobs`, `GET /api/jobs/<id>`: lịch sử/trạng thái.
- `GET /api/jobs/<id>/intake`: cue trong query có dòng/hash, áp dụng cả pack cũ mà không sửa export gốc.
- `GET /api/jobs/<id>/evidence-graph`: đồ thị từ citation đã replay, hash-bound về pack; không gọi model hoặc đọc nguồn mới.
- `GET /api/jobs/<id>/usage`: metadata receipt theo phiên, bao gồm job lỗi/interrupted; token/USD charged-or-reserved, không phải hóa đơn provider.
- `GET/POST /api/jobs/<id>/plan`: checklist theo pack; ghi cần expected SHA256, step ID, status và note.
- `POST /api/jobs/<id>/cancel`: yêu cầu hủy với JSON `{}`.
- `GET /api/jobs/<id>/pack.json`, `.../report.md`: export kiểm hash.
- `GET /api/jobs/<id>/investigation-report.json`, `.../investigation-report.md`: report v1 dựng từ pack đã replay, có raw-pack/query/snapshot SHA256; không model/Git/network.
- `GET /api/investigation-report-schema`: JSON Schema 2020-12 của report v1; cùng session/origin guard như API đọc khác.
- `GET /api/jobs/<id>/stages/<stage>`: tải nguyên bytes checkpoint đã ghi, kể cả job thất bại; kiểm path/hash và session.
- `POST /api/jobs/<id>/github-preview`: bản nháp comment từ job GitHub đã hoàn tất.
- `POST /api/jobs/<id>/github-publish`: body SHA256 và xác nhận cụ thể từ người dùng.
- `POST /api/github/webhook`: GitHub signature/event/delivery guards riêng; không tự gọi model hay đăng comment.

Đối chiếu mới lưu thêm `git_reachability`: pin source/target/reference về SHA
commit, kiểm ancestor hai chiều và kiểm reference có trong target. `contained`
chỉ nói về lịch sử Git; chưa chứng minh đúng bản sửa, patch tương đương, ngày
phát hành hoặc bản phát hành công khai. Tag được chụp từ refs local, tối đa 200
tag nhận kiểm containment, có số ngoài phạm vi/không kiểm được. Lịch sử shallow,
altered, thiếu object hoặc hết thời gian giữ unknown khi không đủ bằng chứng;
đường ancestor tìm thấy vẫn có thể chứng minh containment trong cache shallow.

Với input remote gốc, chỉ target và reference người dùng nhập được fetch theo
cơ chế acquire giới hạn hiện có; không tải toàn bộ tags hoặc deepen lịch sử.
Phần kiểm ancestry chỉ đọc local, không lazy-fetch. Git có cận 5 giây/lệnh,
60 giây tổng và 2 MiB output/lệnh. Export giữ inventory/ref hash lúc kiểm;
mở lại không chạy Git hoặc làm tag hiện tại thay đổi kết quả đã lưu. Báo cáo cũ
thiếu mục này vẫn mở được và không được suy thêm quan hệ ancestry.

Mọi job/pack và repository ví dụ ở ignored `artifacts/demo/`. Không commit data,
token, review bundle hoặc kết quả thật. CI dùng fixture tổng hợp. Evaluation,
metrics và thí nghiệm Holdout vẫn cần capability/ủy quyền riêng theo proposal.

## Chọn nguồn cho điều tra

Mục **Chọn nguồn cho điều tra** chỉ áp dụng báo cáo GitHub product
ngoài benchmark/strict và follow-up nhập văn bản. Tìm local không gọi model;
preview dùng đúng excerpt mà inference sẽ nhận, tối đa 1.200 ký tự/nguồn. Danh
sách lựa chọn không tự thêm nguồn khác. Cả record/receipt/global repo ID phải
khớp trước queue và trước provider; repo gốc được replay ở commit đã lưu, không
fetch lại HEAD hoặc mô tả issue hiện tại. Groq phải bật riêng và consent được xóa
khi đổi nguồn/phiên/ngôn ngữ. Không có retry sau lỗi nhận job.

`context_selection` khóa parent pack/context SHA, thứ tự nguồn và độ sâu tối đa
tám thế hệ, dùng chung giới hạn với follow-up. Toàn bộ proof nguồn được lưu
private, nên mở lại không phụ thuộc index đang thay đổi. Phiên mới không kế thừa
quyền đăng comment hoặc verifier resume của phiên cha; context vẫn unverified.
JSON, Markdown và Trace có provenance; giữ nguyên mọi byte/receipt của phiên cha.

## So sánh hai phiên

Phần dưới lịch sử cho chọn phiên trước/sau. So sánh là mô tả các đầu ra đã lưu,
không phải evaluation, điểm chất lượng hoặc xác nhận bản sửa. Query, commit,
snapshot, protocol, cutoff, availability và query provenance được báo riêng.
Khi một binding thay đổi, excerpt giống nhau vẫn cần gắn lại citation; hypotheses
không được coi giữ nguyên qua context mới. Không copy evidence sang job khác.

Evidence đối chiếu theo loại và locator, kèm hash/revision của từng phía. Đổi
range dòng được báo thêm/bỏ; chưa có semantic line alignment. Hypotheses giữ
nguyên cần cùng nội dung, bước kiểm, support/counter nguồn và context; đổi ID
cục bộ hoặc thứ tự không tự tạo khác biệt. Đầu vào local có ID phụ thuộc request,
nên chỉ ghép khi repository và query giống hệt. GitHub/Main Dev dùng identity
issue cố định và báo riêng khi query đổi. File hỏng, citation thiếu hoặc nguồn
khác snapshot bị từ chối. Kết quả giữ private trên máy, không đọc Holdout/gold.

## Intake và dữ kiện tiếp nối

Tab Intake/Thông tin đầu vào chỉ trích lexical cues trong query: hành vi thực tế,
mong đợi, cách tái hiện, môi trường/phiên bản. Các tín hiệu intent mâu thuẫn hoặc
không có giữ `unknown`. `mentioned` không xác nhận đủ thông tin; `not_found`
không chứng minh thông tin vắng mặt. Nguồn dài có hash cho dòng đầy đủ và excerpt
riêng, kèm dấu truncated. Không gọi model, tra gold hoặc gán nhãn người.

Mục **Trả lời câu hỏi gợi ý** dùng template v1 Việt/Anh cho những cue chưa tìm
thấy, tối đa bốn câu. Có thể trả lời một phần hoặc “chưa biết”. `mode=clarification`
nhận `parent_job_id`, `parent_pack_sha256`, `questions_sha256`, `language` và
`answers` (ID câu hỏi + nguyên văn câu trả lời, tối đa 4.000 ký tự/câu). Server
kiểm nguồn trước queue và worker; replay snapshot/excerpts trước provider. Chỉ
product được dùng, kể cả kiểm nguồn gốc xuyên các phiên tiếp nối; không benchmark.
Groq bật riêng cho lần gửi, không được kế thừa khi đổi nội dung/ngôn ngữ/nguồn.
Nguyên văn Q/A đã lưu xem ở tab Intake và có trong JSON/Markdown. Query mới lấy
code lại ở cùng SHA, không tự mang nguồn repository đã chọn của query trước.
Để câu chữ trong câu hỏi không tạo cue giả, intake của follow-up dùng projection
nội dung người dùng (query gốc + câu trả lời, bỏ câu hỏi/wrapper), với text/hash và
nguồn dòng riêng. Query SHA vẫn khóa toàn bộ đầu vào. Luồng theo mẫu này không
đặt câu hỏi phân biệt giả thuyết; phần model bên dưới đề xuất loại câu hỏi đó.

**Câu hỏi theo giả thuyết và code** nhận báo cáo product đã có 2–5 hypotheses;
mỗi lượt tạo 1–4 câu hỏi riêng qua `openai/gpt-oss-120b`, dùng issue, public code
excerpts và hypotheses của đúng báo cáo. Mỗi câu có Việt/Anh, mục đích, cách dùng
các câu trả lời khác nhau, ít nhất hai hypothesis refs và một code citation.
Phải duyệt việc gửi dữ liệu/chi phí; provider đi qua ledger/per-call bounds hiện
có, một call không tự sửa schema/thử lại. Gửi lặp cùng source nhận lại job cũ,
kể cả job lỗi; muốn attempt mới cần tạo báo cáo điều tra nguồn mới. Mở lịch sử,
đổi ngôn ngữ, export hoặc đọc bundle không gọi model. Câu hỏi/IDs đúng schema
không chứng minh hữu ích hoặc đúng về ngữ nghĩa.

API: `POST /api/adaptive-questions` nhận source job ID, pack SHA và confirmation
`generate_questions_with_api_cost`. `GET /api/jobs/<source>/adaptive-questions`
liệt kê local attempts; `GET /api/jobs/<question-job>/questions.json` replay/xuất
bundle. Trả lời dùng `mode=clarification` cùng `question_job_id`, parent hash và
bundle hash. Giữ nguyên hỏi đáp, mục đích, refs và language; câu hỏi không đi vào
lexical cue projection. Context mới chưa xác minh, không mang quyền write/resume,
không đọc benchmark/Holdout hoặc thay các receipts gốc.

Form bổ sung tạo `mode=followup` với parent job ID và context tối đa 20.000 ký tự.
Repository/commit lấy từ parent đã kiểm hash; client không được ghi đè. Pack cha
giữ nguyên, pack mới có parent pack SHA256, context SHA256, thời điểm và depth.
Mỗi lần mở/so sánh/tiếp nối replay mọi parent, giới hạn tám thế hệ và query tổng
100.000 ký tự. Parent bị hỏng hoặc lineage có vòng bị từ chối trước provider.

Mọi follow-up dùng `benchmark_base`, có case ID `followup:` riêng và context chưa
xác minh. Bổ sung vào Main Dev không thay cohort/query/proof gốc hoặc trở thành
một ca strict mới. Không tự gọi Groq; người dùng bật riêng ở form follow-up nếu
cần. Không kế thừa quyền đăng GitHub của job cha: follow-up không tạo/publish
comment. So sánh với parent nhận identity từ chuỗi nguồn đã replay và báo query/
context đổi, không tái sử dụng citation qua context mới.

## Issue liên quan trong lịch sử

Tab Related issues/Issue liên quan tìm thủ công trong tối đa 50 job local gần
nhất, chỉ cùng repository đã khai báo. Parent gốc của follow-up có thể cũ hơn cửa
sổ 50 job: lineage được replay và mở về query nguồn. Không đọc dataset/holdout,
gọi GitHub, embeddings hoặc LLM; không đưa kết quả vào inference hoặc pack strict.

Mỗi issue nguồn chỉ xuất hiện một lần; loại chính issue đang xem và follow-up của
nó. Với input local, query gốc + repository xác định một issue thay vì ID phụ
thuộc provider/depth. Nguồn hỏng bị bỏ và báo số lượng. Patch/test/rationale/review bundle/gold
không được dùng để tính tương đồng. Query trong pack local có hash kiểm lại.

Ranking BM25 dùng token Unicode, loại stopwords và cần ít nhất hai token chung.
Score không phải xác suất. Mỗi kết quả có matched terms, đoạn nguồn với hash,
query/pack SHA256 và nút mở báo cáo. Quan hệ luôn `candidate_unverified`, không
coi giống triệu chứng là duplicate. Chưa xác minh nguồn này có trước cutoff của
issue đích; chưa có semantic duplicate judgment hoặc đồng bộ toàn GitHub history.

## Kế hoạch kiểm tra có phiên bản

Tab Check plan/Kế hoạch kiểm tra lấy bước đề xuất trong pack; description không
cho ghi đè. Trạng thái gồm todo/in-progress/blocked/reported-done. Bước blocked
hoặc reported-done cần ghi chú; mọi quan sát là tự báo cáo, chưa xác minh. Không
thực thi code/test, thay đổi confidence hoặc đánh giá test split khi lưu checklist.

SQLite local tại `artifacts/demo/plans.sqlite` lưu event có hash/source pack/
parent hash, timestamp, version và change. Mỗi lần đọc replay từ plan gốc; dữ
liệu không khớp source hoặc chain bị từ chối. `BEGIN IMMEDIATE` cùng expected
SHA256 chặn ghi đè phiên bản cũ giữa thread/process. Giới hạn 200 version/job,
50 bước và 2.000 ký tự/ghi chú. Ghi không thay pack, hypotheses hoặc receipt gốc.
Đây là lịch sử local, không phải chứng thực chống mọi sửa đổi bởi chủ filesystem.

Có tải lại và export plan JSON riêng; mở lại sau restart không gọi model. Bản
nháp ghi chú giữ qua đổi ngôn ngữ. Metadata plan lỗi không ngăn xuất báo cáo
nguồn còn nguyên vẹn. Không đưa ghi chú checklist vào prompt tự động.

## Deep: truy xuất theo giả thuyết

Quick giữ một lượt sinh và critique riêng. Deep khi bật Groq dùng evidence ban
đầu để sinh hypotheses, rồi rules/typed controller có thể chọn retrieve/verify/
stop. Retrieve chỉ chạy tối đa một vòng, lấy statement/falsification của ba
hypotheses đầu làm query text, tối đa 1.201 ký tự/query, ba hit/query và tổng sáu
excerpt mới. Không thực thi nội dung query hay kiểm tra được model đề nghị.

Mọi hit được replay đúng file/range/text/SHA/revision/protocol/available_at trong
cùng snapshot. Loại đoạn trùng hoặc overlap; không đọc local history, GitHub,
gold hoặc snapshot khác. Nếu có đoạn mới thì sinh lại một lần rồi critique trên
evidence cuối. Không có đoạn mới thì bỏ call sinh lại. Query/proof gốc giữ nguyên.
Hit không tự trở thành counter-evidence, không chứng minh causal truth hoặc cải
thiện chất lượng; hypotheses vẫn automatic/unverified. Seed annotation và query
trace được lưu để phân biệt đầu ra trước/sau. Kết thúc sau vòng giới hạn hoặc
controller stop; không có vòng agent vô hạn.

Giới hạn trên là logical passes, không phải số HTTP request: generation có thể
repair/fallback schema theo policy sẵn có. Mỗi call thực vẫn qua provider bounds,
giá có ngày và durable budget ledger. Lỗi budget/network dừng, không retry ngầm.
Khi tắt Groq, Deep chỉ thay độ rộng evidence và không sinh/search theo hypotheses.

CLI mặc định giữ baseline; bật rõ `investigate --refinement-rounds 1`. Pipeline
không cho mở rộng `prepared_evidence` đã chốt. Không đổi recipes, registrations,
cohort hoặc receipts cũ để hợp thức hóa code mới; benchmark bulk vẫn tạm dừng.

Prepared input, seed generation, focused retrieval, refined generation và
verification có checkpoint riêng trong ignored artifacts. Workbench lưu dưới
`artifacts/demo/runs/<job>/stages/`; CLI dưới thư mục output/stages/<random-id>/.
Failed/interrupted job chỉ có những stage đã hoàn tất; không được coi là pack
hoàn thành. UI có tải checkpoint ở lịch sử job lỗi và báo job ID đang xem. CLI
in refs đã lưu khi lỗi. Mở/tải không gọi model và không đổi bytes/hash checkpoint.

Product Quick/Deep lưu thêm `verification_input` ngay trước verifier. Với phiên
GitHub/local/synthetic bị failed/interrupted/cancelled và chưa có verification
hoàn tất, người dùng có thể kiểm tra checkpoint rồi duyệt một lần gọi verifier
có chi phí. Generation và evidence cuối được dùng lại, kể cả refinement đã xong.
Không tự retry. Phiên mới giữ liên kết/hash về phiên nguồn; receipt mới chỉ thuộc
call mới, chi phí cũ có thể vẫn đã tính. Intent SQLite giữ một lần tiếp tục cho
mỗi nguồn qua gửi đồng thời/restart; gửi lại trả cùng job, kể cả job đó đã lỗi.

Trước preview và trước provider, replay snapshot Git local, query, evidence,
citation, annotation và dấu vân tay policy/code. Thiếu repo/commit, hash lệch,
policy đổi hoặc model/max-output không tương thích thì từ chối, không sửa hash.
Checkpoint cũ thiếu payload này không được suy diễn thành đầu vào hợp lệ. Không
áp dụng Main Dev/strict, follow-up, generation lỗi hoặc verifier đã hoàn tất;
không kế thừa quyền đăng comment của phiên GitHub. Đây là phần tiếp tục verifier
của F10, chưa phải resume tùy ý tại mọi stage hoặc phục hồi bulk benchmark.

## Tích hợp GitHub

Chọn **GitHub issue URL**, nhập URL và revision. `HEAD` lấy snapshot hiện tại;
không biến mô tả REST hiện tại thành opening-query strict. Tab **GitHub** tạo bản
nháp có code links theo commit đã đọc. Sau khi xem đúng issue đích và nội dung,
người dùng đánh dấu duyệt và bấm đăng. Token local cần quyền comment trên issue
đích. Thay nội dung/hash/target bị từ chối; một intent đã bắt đầu không retry ngầm
khi lỗi hoặc restart. Không có comment thật nào được đăng trong kiểm thử phát triển.

Adapter webhook hỗ trợ `/issueintel investigate [revision]` từ OWNER, MEMBER hoặc
COLLABORATOR trên issue của repo public. Nó tạo một job evidence-only; không tự
chi Groq hoặc gửi comment. Delivery lặp lại dùng cùng job. Cần đặt riêng
`GITHUB_WEBHOOK_SECRET` trong `.env` và cấu hình GitHub App webhook `issue_comment`.
Secrets không đặt trong repo/config mẫu hoặc chat. Endpoint local là
`/api/github/webhook`; server mặc định chỉ bind localhost. GitHub không thể gọi
localhost trực tiếp: triển khai endpoint/gateway HTTPS và cài App vẫn là bước
cấu hình tiếp theo, chưa được làm tự động hoặc coi đã hoàn thành.

Write adapter pin API version `2026-03-10`, kiểm HTTP 201, exact body và comment
URL/ID từ response. Nguồn đã đối chiếu ngày 14/10/2026:
[Create issue comment](https://docs.github.com/en/rest/issues/comments#create-an-issue-comment)
và [Webhook validation](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries).
