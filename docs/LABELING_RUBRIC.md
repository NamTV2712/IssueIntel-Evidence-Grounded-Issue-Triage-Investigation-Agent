# Rubric annotation IssueIntel

Mỗi hypothesis có statement phân biệt được, ít nhất một support ID, counter IDs hoặc `not_found`, và falsification test chưa thực thi. Không dùng cùng evidence làm support và counter cho cùng hypothesis. Evidence phải mở lại đúng revision/span và kiểm hash.

## Correctness

- `unverified`: chưa được xét đúng/sai.
- `supported`: dữ liệu tham chiếu cụ thể hỗ trợ cơ chế nêu; ghi locators và provenance.
- `refuted`: dữ liệu tham chiếu cụ thể trái với cơ chế nêu.
- `uncertain`: tham chiếu thiếu, chỉ khớp file, hoặc nhiều cơ chế tương thích.

Gold judge tạo nhãn theo patch/test hunk được cung cấp; không biến vị trí patch thành chứng minh nguyên nhân. Không bắt buộc một hypothesis đúng và một sai nếu tham chiếu không đủ. Người hoặc model xét nhãn phải nêu rationale và các vị trí thực sự hỗ trợ kết luận.

## Answerability

`answerable`, `abstain`, `uncertain` phân biệt mức đủ bằng chứng để đưa một kết luận kỹ thuật có căn cứ. Khả năng đề xuất bước kiểm tra không đồng nghĩa đã biết nguyên nhân. Ordinal clarity 0/1/2/3 của SWE-bench giữ nguyên; không ánh xạ 1 sang abstain.

Synthetic evidence removal/conflict ghi operation, parent SHA, phần bị xóa/chèn và validation unreviewed. Không tính những ca này thành ví dụ mâu thuẫn tự nhiên hoặc coi target abstain là nhãn người.

## Blind review và agreement

`issueintel review` tạo CSV không kèm nhãn tự động. Reviewer điền identity/kind/label theo schema của CSV. `issueintel agreement left.csv right.csv` chỉ tính independent human agreement khi có hai người khác nhau và khai báo human; thiếu nhãn để pending, κ không xác định để null.

Người dùng hiện chọn dựa vào kiểm task gốc của SWE-bench. Vì vậy không tuyên bố đã có human check/IAA cho overlay mới. Có thể bổ sung người kiểm sau; file nhãn agent không được đổi provenance thành human.
