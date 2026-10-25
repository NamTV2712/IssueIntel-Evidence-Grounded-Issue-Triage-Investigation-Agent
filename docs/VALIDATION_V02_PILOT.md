# V02 — pilot review cặp bằng model

Ngày đo: **25/10/2026**. Đây là phép chấm tự động trên năm ca product,
không phải kiểm người độc lập hoặc phép đo hiệu quả làm việc của lập trình viên.
Kết quả có cả ưu thế và thất bại; V02 chưa nghiệm thu lợi ích thực tế.

## Selection và phiên bản

Chọn trước năm issue public không phải PR, có body trong cache, theo số tăng dần:
`pypa/sampleproject` #1, #2, #3, #4, #7. Cả năm dùng revision
`621e4974ca25ce531773def586ba3ed8e736b3fc`. Capture kiểm lại issue/revision qua
GitHub một lần rồi khóa native batch preview; collection không tải lại query.
Đây là selection thuận tiện trên một repository mẫu nhỏ, không đại diện cho
phân bố bug thực tế và không phải cohort benchmark hay temporal test.

Pipeline product native được khóa tại source commit
`5e3fbb7f4362e96998e034722b5c835d3cf4e485`. Generation, critic, câu hỏi và
check plan dùng Groq `openai/gpt-oss-120b`, effort `low`, output cap 4.096.
Không mở Main Dev, Holdout, Live hoặc gold cho pilot này. Không chạy code đích
hoặc đăng comment GitHub.

Mỗi ca có hai cặp trên cùng raw pack/evidence theo
[giao thức review cặp v2](USEFULNESS_REVIEW.md):

- Question: intake questions native so với adaptive clarifications đã lưu.
- Check: ba saved falsification checks đầu theo native report, so với check plan
  mới. Baseline giữ wording/liên kết gốc; provenance LLM của hypothesis không
  biến thành nguồn deterministic hay bằng chứng cho quy trình kiểm tra.

Thứ tự cặp và A/B được khóa một lần. Judge chỉ nhận reviewer-facing card và
rubric, không nhận organizer mapping, tên phương pháp hoặc receipt. Wording
vẫn có thể lộ treatment. Judge dùng cùng model/effort/output cap như trên;
đây là một reviewer model, không có kiểm người hoặc agreement giữa reviewers.

## Coverage và preference

Collection giữ **5/5** pack, **5/5** adaptive question exports và **4/5** check
plan exports. Check plan thứ năm lỗi sau hai attempt HTTP 400
`json_validate_failed`. Giữ nguyên lỗi và hai `failed_bound`, không sinh lại
để thay kết quả. Hai attempt đó gồm call ban đầu và một native output repair.

Đủ **10/10** card được chấm bằng một call/card, không sửa output hoặc retry.
Phía check plan lỗi không nhận nhãn chất lượng hoặc preference; phía baseline
của card đó vẫn được chấm. Tất cả ca/cặp giữ trong mẫu số đã chọn.

| Kind | Cặp đã chọn | Đủ điều kiện preference | Ưu tiên baseline | Ưu tiên mới | Tie / uncertain / missing | Không đủ điều kiện |
|---|---:|---:|---:|---:|---:|---:|
| Question | 5 | 5 | 0 | 5 | 0 / 0 / 0 | 0 |
| Check | 5 | 4 | 1 | 3 | 0 / 0 / 0 | 1 |

Theo từng chiều, question mới được judge gán `yes` ở cả năm ca. Với check plan
mới, `specificity`, `grounding`, `feasibility` có 4 `yes` trong mẫu số 5;
`discrimination` và `useful_next_action` có 3 `yes`, 1 `no`, 1 failed không nhãn.
Baseline checks có 5 `yes` ở discrimination, feasibility và useful next action.
Vì vậy preference 3–1 không có nghĩa check plan mới thắng mọi chiều hoặc bù được
ca lỗi generation. Không chuyển failed thành nhãn chất lượng `no`.

Cả mười card vẫn chỉ là **năm case clusters**, không phải mười ca độc lập.
Không tính kiểm định thống kê, accuracy, causal efficacy hoặc thời gian người
dùng tiết kiệm từ số preference này.

## Accounting

Dùng một durable study counter cho collection và judge, cận 45 attempt toàn bộ,
35 collection và 10 judge; mỗi ca tối đa 9, riêng judge tối đa 2. Đây là cận
triển khai cho request đã được phép, không phải số do người dùng trực tiếp đặt.

| Phase | Attempt sở hữu |
|---|---:|
| Source generation + critic | 10 |
| Adaptive questions | 5 |
| Check plans, kể cả lỗi/repair | 6 |
| Pair judge | 10 |
| Tổng | 31 |

Có 31 reservation riêng: **29 settled**, **2 failed_bound**, không thiếu receipt.
Tổng ledger là **32.666 micro-USD (0,032666 USD)**, gồm 25.682 micro-USD settled
và 6.984 micro-USD bound của hai call lỗi. Judge chiếm 8.404 micro-USD settled.
Token tính sổ/cận là 133.715; không gọi đó là toàn bộ token quan sát thực tế.
Giá được khóa từ bảng giá kiểm ngày 24/10/2026. Các số này chỉ thuộc ledger
product V02 ở checkout thu thập; không cộng lẫn với chi phí V01 hoặc Typesafe,
không phải hóa đơn provider đã đối soát.

## Bằng chứng và giới hạn

Raw issues, packs, requests, reviewer CSV, model responses và receipt bundles
giữ private dưới ignored `artifacts/`/`data/`, không xuất bản cùng source.
Các định danh dưới đây giúp đối chiếu bản local, không phải chữ ký tác giả:

| Artifact | Raw SHA256 |
|---|---|
| Paired selection | `e9719facf07252ae3a4301d70058ccd494d9e67dc1dbd8d8e917ca13908ad157` |
| Frozen review manifest | `9b1f36aa6f05638c4ddb31628186ad37224d72008adf926a5b383f116bb0ede9` |
| Frozen reviewer display | `7427d0613dd620bc77b2742a5a9fc7f86d94bbe3c7b76281fc069d33d8e8ba06` |
| Imported model ratings CSV | `d68cde805dbbbf7f838d50940e4188ae21aefa4b8cd791d36a29934cbd353af1` |
| Native paired score | `a03c8e40a3b2fbaebf2520ae2e3b3a73f8556ea32efb60152376ac484c2ecc2b` |
| Judge execution report | `0299286e5566d99e87c6b49ef0768a8472de7e6c1276abea96e68ac5e65857d0` |

Native score được mở lại từ frozen source copies và append-only model-rating
receipt. Helper có 12 synthetic checks gồm đủ mười card, shared counter,
journal trước provider, CSV/status bindings, failed/unknown và no retry;
các test này kiểm cơ chế chạy, không thay phép chấm chất lượng.

Kết quả chỉ hỗ trợ kết luận: **trong pilot này, model reviewer thích câu hỏi
mới và phần lớn check plan đã sinh thành công**. Cần giữ riêng failure rate,
từng chiều chất lượng và scope một repo nhỏ. Không suy ra semantic correctness,
thành công khi thực thi check, hiệu quả với developer hoặc ưu thế trên Main Dev.

Nếu sửa prompt/validator dựa trên pilot này, phải ghi phiên bản mới và thiết kế
cohort tiếp theo trước khi chạy; không thay labels, selection hoặc chạy lại năm
ca để ghi đè kết quả hiện tại. Holdout vẫn đóng.
