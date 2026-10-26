# V03 — pilot rules/Jev ngày 26/10/2026

Pilot thật đã hoàn tất một cặp trên source product đầu tiên đã đăng ký, từ
`pypa/sampleproject` issue #1. Chỉ `case-01` được chạy; bốn case còn lại chưa
được phép chạy. Đây là kiểm vận hành harness/controller, chưa nghiệm thu V03
hoặc chứng minh lợi ích của Jev.

Source code: `fa020eac7735061de2ee63b4362e36988efc3862`.
Study: `b49dcd3210b0447bfedcb11010661a451834a82f4fd94b882c268615e3dc073b`.
Registration SHA: `ac0cba0c710a9756d6fa51e10c99b69cac7f953951c3fbda2cce736e17c26846`.
Recipe/selection/budget: [CONTROLLER_STUDY.md](CONTROLLER_STUDY.md).

Tác giả cho phép đúng pilot: tối đa 18 Groq attempts, 4 Typesafe attempts,
0,387072 USD tổng và 0,010752 USD riêng Typesafe. Authority/question/answer
được lưu private; không suy thành quyền matrix, Holdout, Live hoặc freeze mới.

| Quan sát | Kết quả |
|---|---|
| Hoàn tất | 2/2 arm của một case; toàn study vẫn chỉ 1/5 case |
| Owned provider calls / native reservation IDs | 6 duy nhất: 2 Groq + 4 Jev |
| Receipt | Cả 6 settled; không failed/pending/unknown hoặc orphan |
| USD theo receipt local | Tổng 0,002837; Groq 0,002302; Jev 0,000535 |
| Quyết định Jev | 4 lựa chọn thật, 0 fallback: retrieve ×3, verify ×1 |
| So rules ở cùng state | 4/4 lựa chọn trùng rules; agreement không phải accuracy |
| Output mỗi arm | 14 evidence, 11 code files; 1 generation, 0 schema repair |
| Retrieval / dừng | 3 vòng không thêm evidence, `round_limit` |
| Native critic dùng chung | 2 supported, 1 unsupported; nhãn model chưa được kiểm độc lập |

Generation/critic được reuse từ stage hoàn tất đúng binding, giữ annotation
timestamp và mọi receipt. Logical cost rules 0,002302 USD và Jev 0,002837 USD
có phần Groq dùng chung: không cộng hai số thành physical spend. Đây là local
receipt accounting, chưa đối soát hóa đơn provider.

Measured pipeline elapsed là 17,062 giây cho rules và 35,422 giây cho Jev;
preparation chung 0,125 giây. Rules chạy trước; Jev hưởng cached stages và có
thêm controller calls. Một cặp với thứ tự/cache như vậy không đủ suy tác động
nhân quả lên latency hoặc hiệu quả thực tế. Trên case này chưa quan sát lợi
ích thêm từ các lựa chọn Jev so với rules.

Audit riêng replay đầy đủ native packs/stages/controller choices mà không gọi
provider, đọc `.env` hoặc dùng network. Sáu receipt khớp native SQLite mở
read-only/query-only; journal/index/ledger và toàn inventory giữ nguyên hash
trước/sau audit. Không có case/call ngoài pilot. Inference chưa sealed; scorer
toàn study, gold và common independent output judge chưa chạy.

Audit private: `artifacts/experiments/controller-study-2026-10-26/pilot-audit.json`,
raw SHA `099fbba70dffe61bdff5f1ee20f84b081ac5fba55a4d912aa1e343ab4f5fc761`.
Các artifact/model payload/receipt ở ignored local storage, không đưa vào Git.
Repo mẫu nhỏ và một case không đại diện project production, developer benefit
hoặc chất lượng chẩn đoán. Bước tiếp theo là quyền riêng cho bốn cặp còn lại,
rồi full seal/descriptive score và common output-quality assessment.
