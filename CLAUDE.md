# IssueIntel — hướng dẫn vào project

Đọc [AGENTS.md](AGENTS.md), [README.md](README.md), rồi [proposal.md](proposal.md).

Proposal hiện hành là proposal.md ở gốc. Đây là bản dựng lại trên SWE-bench; kết quả pytest cũ nằm trong ZIP phục hồi bên ngoài project.

Đọc docs/ARCHITECTURE.md và manifest dữ liệu để biết số lượng và phần đã hoàn thành. Import không đồng nghĩa annotation và temporal audit đã xong.

Demo và benchmark dùng chung pipeline chỉ đọc snapshot → graph → retrieval → hypotheses → verification. Giữ gold patch/tests ngoài đầu vào inference. Nhãn người từ SWE-bench không xác nhận nhãn tự động bổ sung.

Các giới hạn và cổng thực thi trong AGENTS.md áp dụng cho mọi thay đổi tiếp theo.
