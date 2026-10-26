# V03 — fresh common automatic assessment, 26/10/2026

Đã chấm bằng pass mới trên toàn bộ output có thể chấm của matrix đã sealed.
Mỗi arm có **4/5 case được review**; trong mẫu này không có chênh lệch nhãn
giữa Jev và rules. Các projected outputs giống nhau dùng chung judge, nên đây
không phải tám nhận xét model độc lập hoặc bằng chứng hiệu quả trên repo khác.

Source adapter: `aa13a9a190e201a89095f686206d4228aad10604`.
Parent: [matrix năm case](VALIDATION_V03_MATRIX.md), study
`b49dcd3210b0447bfedcb11010661a451834a82f4fd94b882c268615e3dc073b`.
Parent seal/child ID:
`c22c249116c821224d02b1f73165363e204ede29391c02ed499339b40b7ff045`.
Plan SHA: `b9e5c76612aa484980c98946b6b43b5bd4a867f697e97025847a40bdc5ea158e`.
Protocol: [CONTROLLER_GROUNDING.md](CONTROLLER_GROUNDING.md).

| Quan sát | Kết quả |
|---|---|
| Selected scope | 5 case, 10 arm rows; không thay case lỗi |
| Parent có output | 8 pack completed, tạo 4 exact public critic requests |
| Fresh common calls | 4 Groq; cả 4 settled, 0 failed/unknown, không repair/retry |
| Common coverage mỗi arm | 4/5 case; `case-04` giữ failed/unavailable từ parent |
| Nhãn mỗi arm | 10 supported, 4 unsupported, 0 contradicted, 0 uncertain |
| Paired difference | 0 trên 4 common-reviewed case, do cùng projected inputs |
| Chi phí fresh judge | 0,004355 USD; không dùng lại critic trong treatment |
| Tổng parent + judge | 0,022402 USD = 0,018595 settled + 0,003807 failed bound cũ |
| Tổng owned calls | 33: 15 Groq + 18 Jev; không gọi thêm Jev trong common phase |

Judge dùng Groq 120b low, output cap 2.048, native structured critic. Public
hypotheses/evidence được canonicalize, không gửi controller/trace hoặc verdict
cũ. Source inventory, native replay và parent seal được kiểm; parent không sửa.
Mười bốn nhãn ở mỗi arm là logical references tới cùng bốn response. Không
cộng chúng thành 28 độc lập hoặc cộng referenced costs thành physical spend.

Native receipts giữ 22.444 charged/reserved token cho bốn judge calls; input/
output directional observations đều unknown, không báo thành observed zero.
Observed active unit duration tổng 107,483 giây gồm guard/journal, không phải
pure network latency hoặc một phép đo speedup giữa controller.

Child có single owner theo parent seal, paid stage bind phase authority SHA.
Groq phase dùng scope mục tiêu/ủy quyền Groq đã lưu, giữ nguyên cận kết hợp
90 Groq/20 Jev/1,935360 USD đã duyệt; không khai báo một human approval mới.
Pilot một fresh unit đạt trước matrix. Không đọc gold, mở Holdout/Live, thêm
case, freeze mới hoặc gọi thêm Typesafe.

Audit riêng tái lập parent/child seals, native response và score; đối soát
bốn RID mới tách khỏi 29 parent RID. Cả mười SQLite DB mở read-only/query-only;
toàn inventory parent/child/owner giữ nguyên hash trước/sau. Không construct
provider, đọc `.env` hoặc gọi network trong audit. Private audit ở
`artifacts/experiments/controller-common-2026-10-26/audit.json`, raw SHA
`249d82b59ac6c10364d1cebbc483e01e4dd444c6826449be1396de7718989b6f`.
Common score canonical SHA:
`64d6c06cd85a457888b1e3a1cee85b96e01ce5c50fd96726988d6f31537dfb40`.

Đây là bounded automatic comparison trên repo mẫu nhỏ; generator và judge cùng
model nên có shared bias. Nhãn supported không xác nhận root cause, sửa lỗi,
semantic accuracy hoặc developer benefit. Không có kiểm người mới. Kết quả
đã đo chưa cho thấy lợi ích thêm của Jev trên mẫu này; không ngoại suy sang
repository, task khó hơn hoặc hoạt động production.
