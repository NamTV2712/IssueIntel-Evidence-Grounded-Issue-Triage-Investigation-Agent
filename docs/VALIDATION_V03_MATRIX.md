# V03 — matrix product ngày 26/10/2026

Đã chạy hết năm case cố định của đăng ký, tạo đủ mười ô terminal và inference
seal. Rules và Jev đều hoàn tất **4/5 case**. Một verifier call lỗi kết nối được
giữ nguyên; kết quả chưa chứng minh Jev tốt hơn rules.

Source implementation: `fa020eac7735061de2ee63b4362e36988efc3862`.
Study: `b49dcd3210b0447bfedcb11010661a451834a82f4fd94b882c268615e3dc073b`.
Giao thức: [CONTROLLER_STUDY.md](CONTROLLER_STUDY.md).
[Pilot](VALIDATION_V03_PILOT.md) đã nằm trong tổng dưới đây, không cộng thêm.

Selection giữ năm issue `1, 2, 3, 4, 7` của `pypa/sampleproject`, cùng snapshot
`621e4974ca25ce531773def586ba3ed8e736b3fc`, Planned v2 và Groq 120b low.
Tác giả duyệt bốn cặp còn lại với cận toàn study 90 Groq + 20 Jev attempts,
1,935360 USD tổng và 0,053760 USD riêng Jev. Không thêm case, đọc gold hoặc mở
Holdout/Live.

| Quan sát | Rules | Jev |
|---|---:|---:|
| Case đã chọn | 5 | 5 |
| Completed | 4 | 4 |
| Failed | 0 | 1 |
| Unavailable | 1 | 0 |
| Retrieval rounds trong completed subset | 12 | 10 |
| Thêm evidence | 0 | 0 |
| Generation / schema repairs trong completed subset | 4 / 0 | 4 / 0 |
| In-treatment critic labels, logical references | 10 supported, 2 uncertain, 2 unsupported | Cùng các label đó |

Jev có **18 lựa chọn thật, không fallback**, trong đó 17/18 trùng rules ở state
đã quan sát. Riêng bốn pack hoàn tất: 13/14 trùng rules. Ở `case-03`, Jev chọn
verify sớm hơn, bỏ hai vòng/sáu query vốn không thêm evidence; final evidence
không đổi. Agreement không phải action accuracy, và critic dùng chung không
phải common independent output judge.

`case-04` chạy Jev trước theo thứ tự xen kẽ. Verification call lỗi
`URLError → ConnectionResetError`, errno `10054`, không có HTTP response.
Jev giữ failed; rules giữ unavailable từ exact shared failed stage. Không
retry, không nhân đôi paid owner hoặc coi đây là bằng chứng controller sai.

Có **29 call/reservation ID độc nhất**: 11 Groq, 18 Jev. 28 settled, một
failed-bound. Tổng **0,018047 USD** gồm 0,014240 settled và 0,003807 còn giữ
bound do usage chưa biết. Groq 0,015638; Jev 0,002409. Đây là local receipt
accounting, chưa đối soát hóa đơn. Shared logical arm costs không cộng ngang;
native Groq directional observations còn unknown không được báo thành 0 token.

Measured pipeline active time cộng theo arm: rules 146,656 giây, Jev 408,297
giây. Tổng observed provider-wrapper latency dedup 307,282 giây. Các số chịu
guard/journal, thứ tự chạy và shared stage cache; không phải phép đo causal
speedup hoặc cold end-to-end performance.

Audit riêng tái lập full source/pack replay, inference seal và native score;
đối soát 29 receipt với hai provider DB, joint index và năm unit indexes qua
SQLite read-only/query-only. Toàn inventory/native DB giữ nguyên hash trước/
sau audit; sáu RID và chi phí pilot cũ nguyên vẹn. Không gọi API hoặc đọc `.env`
trong audit. Private artifacts nằm ở
`artifacts/experiments/controller-study-2026-10-26/`:

- `matrix-audit.json` raw SHA `2a400cca007dbdf436d7c0a7073e3bf786875a44b71c4b16e7706f08f8d116f1`.
- `score.json` canonical SHA `a8b6defeb28464253ef8bd0b08b0f9274bc41746eebbfe9e78d057f6811d0295`.

Repo mẫu nhỏ, nhiều query không có yield và chỉ bốn completed pairs. Kết quả
chưa xác nhận semantic accuracy, hypothesis truth, developer benefit hoặc kiểm
người. Bước tiếp theo là fresh common automatic grounding trên bốn projected
inputs duy nhất của tám pack hoàn tất; ca nguồn lỗi vẫn nằm trong mẫu số năm.
Không dùng lại in-treatment critic làm kết quả chấm chung.
