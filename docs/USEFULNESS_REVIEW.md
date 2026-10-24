# Review cặp câu hỏi và bước kiểm tra

Ba lệnh `usefulness-pair-build/import/score` chuẩn bị review V02 có baseline từ
hành vi product đã có. Chúng chỉ đọc saved product exports; không đọc `.env`,
Git, dataset hoặc gold, không gọi model và không chạy các bước kiểm tra được
đề xuất. Công cụ review v1 và native validators không thay đổi.

## Hai phía trên cùng hồ sơ

| Kind | Baseline có nguồn | Đầu ra mới |
|---|---|---|
| `question` | `intake_questions_v1`: đúng 0–4 prompt Việt/Anh từ `clarifications.questions_for(query)` | `adaptive_clarifications_v1` đã lưu, qua native validator |
| `check` | `saved_falsification_v1`: đúng ba mục đầu đã dedup của `build_report().next_checks`, theo thứ tự gốc | `product_check_plan_v1` đã lưu, qua native validator |

Mỗi cặp có cùng raw pack, query, hypotheses, evidence và revision. Baseline được
tái dựng bằng code, không gọi model mới; wording của checks cũ vẫn có thể bắt
nguồn từ hypotheses do LLM sinh. Organizer giữ provenance gốc riêng với
`deterministic_derivation`. Không biến nó thành output mới của LLM.

Checks baseline giữ đúng check ID, text và liên kết hypothesis/evidence của
report, với vai trò `hypothesis_source_link`. Các liên kết này chưa phải bằng
chứng cho quy trình kiểm tra. Assumptions, A/B outcomes và predictions chưa được
ghi phải giữ null/array rỗng. Trường còn thiếu không tự động nhận nhãn `no`.

Không nhận Main Dev, Holdout, Live hoặc pack strict bằng cách bỏ split hay đổi
ID thành product. Phép đo benchmark cần adapter và cổng riêng. File hashes kiểm
tính nhất quán bytes đã đưa vào, không chứng minh authority hoặc full Git replay.

## Selection v2

JSON có đúng `schema: usefulness_pair_selection_v2`, `purpose`, `protocol_id`,
`language: en|vi`, `pairs` (1–100 cặp). Mỗi pair có đúng:

- `unit_id`: cluster của cùng một ca; có thể xuất hiện một lần cho mỗi kind.
- `kind`: `question` hoặc `check`.
- `source_job_id` và `pack: {path, sha256}` khóa raw source.
- `variants`: đúng hai phần tử baseline/native, mỗi phần tử có
  `condition_id`, `variant`, `status`, `reason`, `artifact`.

Hai condition IDs phải khác nhau. `artifact: null` cho baseline; code tái dựng
và kiểm `completed/empty` từ nội dung thật. Native `completed/empty` cần reference
`{path, sha256}`; chỉ native checks được empty, adaptive questions giữ cận 1–4.
Native `failed/unavailable` không có artifact và cần reason. Đây là khai báo của
organizer, chưa đo được failure rate của provider. Source paths là JSON dưới
ignored `artifacts/`; không lấy thêm nguồn bằng discovery.

Một `unit_id` dùng cùng pack/job cho cả question và check. Ví dụ 5 ca × 2 kinds
là 10 cặp, 20 proposal bundles, nhưng vẫn chỉ có **5 case clusters**.
Mỗi source job và raw pack hash chỉ được thuộc một unit, kể cả khi file có nhiều
đường dẫn sao chép. Job/pack khác nhau chưa tự chứng minh hai ca độc lập.
Ví dụ đếm ở trên là theo các unit đã khai báo; cần chọn
nguồn và điều kiện trước khi xem kết quả nếu muốn gọi đó là một study đăng ký;
chạy công cụ này riêng không tự xác lập preregistration hoặc sample coverage.

```powershell
python -m issueintel usefulness-pair-build --selection artifacts/paired-selection.json --output artifacts/reviews/paired-study
# Reviewer đọc reviewer/review.html và điền reviewer/ratings.csv.
python -m issueintel usefulness-pair-import --review artifacts/reviews/paired-study --ratings artifacts/reviews/paired-study/reviewer/ratings.csv --reviewer artifacts/reviewer.json
python -m issueintel usefulness-pair-score --review artifacts/reviews/paired-study
```

Organizer giữ frozen raw sources, mapping và exact baseline derivations. Chỉ
thư mục `reviewer/` được chia sẻ nếu muốn ẩn condition metadata. Thứ tự cặp và
orientation A/B lấy mẫu một lần rồi khóa bằng manifest/hash. Card có chung
context, hai whole bundles, không chia mỗi câu hỏi thành một mẫu độc lập.
Condition/provider/model/rank/effort không hiển thị; wording hoặc cấu trúc vẫn
có thể lộ phương pháp nên không tuyên bố blinding hoàn hảo.

HTML offline escape dữ liệu, CSP chặn script/network; responsive hai cột/đơn
cột. Reopen tái dựng display/mapping/template từ raw copies, không cần original
exports. File/review tổng có cận, từ chối link/junction/reparse, không ghi đè
review cũ. Nội dung private và generated bundles không commit lên GitHub.

## Nhãn và kết quả

CSV có `blind_id`, `display_sha256`, năm chiều cho từng phía A/B:
`specificity`, `grounding`, `discrimination`, `feasibility`, `useful_next_action`,
cùng `A_rationale`, `B_rationale`, `preference`, `preference_rationale`.

Mỗi chiều là `yes/no/uncertain` hoặc trống; phía có nhãn cần rationale. Không
gán nhãn chất lượng cho phía empty/failed/unavailable. Preference chỉ hợp lệ khi
cả hai completed: `A/B/tie/uncertain` hoặc trống, có rationale khi được chọn.
Không ép preference để loại uncertain hoặc che thiếu dữ kiện.

Reviewer JSON dùng schema/provenance v1: `kind: human|agent|model`, assistance,
relationship, model ID khi có, thời điểm và declaration. Đây là khai báo, chưa
xác thực danh tính hay tính độc lập. Nhãn model/agent không đổi thành kiểm người.
Import giữ exact CSV/reviewer UTF-8 trong receipt append-only; correction trỏ
đúng leaf cũ của cùng reviewer với lý do. Reimport nguyên bytes idempotent,
không nhân receipt. Lock và cận file/count/bytes giữ qua restart.

Scorer báo từng reviewer/condition/kind, status coverage, missing/uncertain,
preference sau khi mở mapping và số cluster. Không cộng card hoặc reviewer thành
ca độc lập, không tự tính agreement hay kiểm định causal. Chưa có nhãn thì báo
`unreviewed_no_nonblank_ratings`; chỉ nhãn uncertain không thành kết quả dương.

Review do model thực hiện có thể là proxy được ghi rõ. Nó chưa chứng minh
developer chọn kiểm tra đúng nhanh hơn, đề xuất đã chạy thành công, hoặc tính
năng cải thiện hiệu quả điều tra. Các kết luận đó cần phép đo phù hợp riêng.
