window.IssueIntelMessages = {
  "Dùng Jev qua Typesafe để chọn bước điều tra": {"vi":"Dùng Jev qua Typesafe để chọn bước điều tra","en":"Use Jev through Typesafe to choose investigation steps"},
  "Đang kiểm cấu hình Typesafe trên máy…": {"vi":"Đang kiểm cấu hình Typesafe trên máy…","en":"Checking local Typesafe configuration…"},
  "Chưa đọc được cấu hình Typesafe. Tải lại trang sau khi kiểm server local.": {"vi":"Chưa đọc được cấu hình Typesafe. Tải lại trang sau khi kiểm server local.","en":"Typesafe configuration could not be read. Check the local server, then reload."},
  "Chưa có API key Typesafe trên máy.": {"vi":"Chưa có API key Typesafe trên máy.","en":"No Typesafe API key is configured locally."},
  "Typesafe chưa được bật trong cấu hình. Cần bật và đặt ngân sách riêng trước khi dùng.": {"vi":"Typesafe chưa được bật trong cấu hình. Cần bật và đặt ngân sách riêng trước khi dùng.","en":"Typesafe is disabled in configuration. Enable it and set its separate budget before use."},
  "Typesafe chưa sẵn sàng. Kiểm ngân sách request, USD/ngày, USD/tháng và bảng giá trước khi dùng.": {"vi":"Typesafe chưa sẵn sàng. Kiểm ngân sách request, USD/ngày, USD/tháng và bảng giá trước khi dùng.","en":"Typesafe is not ready. Check its request, daily USD and monthly USD budgets and dated prices before use."},
  "Typesafe sẵn sàng. Bật Groq trước rồi chủ động chọn Jev cho phiên này.": {"vi":"Typesafe sẵn sàng. Bật Groq trước rồi chủ động chọn Jev cho phiên này.","en":"Typesafe is ready. Enable Groq, then explicitly select Jev for this run."},
  "Typesafe sẵn sàng; chỉ gọi sau khi bạn chọn Jev và bắt đầu điều tra.": {"vi":"Typesafe sẵn sàng; chỉ gọi sau khi bạn chọn Jev và bắt đầu điều tra.","en":"Typesafe is ready; calls begin only after you select Jev and start the investigation."},
  "Khi chọn, issue, giả thuyết và trích đoạn code được gửi tới Typesafe để chọn tìm thêm, kiểm chứng hoặc dừng. Tối đa 4 call quyết định tính phí riêng ngoài call Groq; chỉ bắt đầu khi bạn bấm Điều tra issue. Lỗi quyết định dùng fallback theo luật.": {"vi":"Khi chọn, issue, giả thuyết và trích đoạn code được gửi tới Typesafe để chọn tìm thêm, kiểm chứng hoặc dừng. Tối đa 4 call quyết định tính phí riêng ngoài call Groq; chỉ bắt đầu khi bạn bấm Điều tra issue. Lỗi quyết định dùng fallback theo luật.","en":"When selected, the issue, hypotheses and code excerpts are sent to Typesafe to choose retrieval, verification or stopping. Up to 4 separately billed decision calls are additional to Groq calls; they start only when you click Investigate issue. Decision failures fall back to rules."},
  "Call sinh và verifier đã ghi nhận": {"vi":"Call sinh và verifier đã ghi nhận","en":"Recorded generation/verifier calls"},
  "Giới hạn sinh/verifier: {calls} call, tối đa 3 truy vấn/6 trích đoạn mới mỗi vòng và 18 trích đoạn mới tổng cộng. Lượt sinh có thể kèm một lần sửa schema. Call Jev/Typesafe tính riêng; xem Token và chi phí để đọc receipt.": {"vi":"Giới hạn sinh/verifier: {calls} call, tối đa 3 truy vấn/6 trích đoạn mới mỗi vòng và 18 trích đoạn mới tổng cộng. Lượt sinh có thể kèm một lần sửa schema. Call Jev/Typesafe tính riêng; xem Token và chi phí để đọc receipt.","en":"Generation/verifier limit: {calls} calls, up to 3 queries/6 new excerpts per round and 18 new excerpts overall. Each generation may include one schema repair. Jev/Typesafe calls are counted separately; see Tokens and cost for receipts."},
  "Quyết định Jev / Typesafe": {"vi":"Quyết định Jev / Typesafe","en":"Jev / Typesafe decisions"},
  "Không thể gắn quyết định Typesafe với phiên và kế hoạch này. Xem JSON gốc để kiểm tra.": {"vi":"Không thể gắn quyết định Typesafe với phiên và kế hoạch này. Xem JSON gốc để kiểm tra.","en":"Typesafe decisions cannot be bound to this run and plan. Inspect the original JSON for details."},
  "{count} / {limit} lượt quyết định đã ghi · model {model}": {"vi":"{count} / {limit} lượt quyết định đã ghi · model {model}","en":"{count} / {limit} recorded decision attempts · model {model}"},
  "Các lượt này tính riêng ngoài call sinh giả thuyết/verifier. Quyết định chọn bước không xác minh chất lượng bằng chứng hoặc nguyên nhân.": {"vi":"Các lượt này tính riêng ngoài call sinh giả thuyết/verifier. Quyết định chọn bước không xác minh chất lượng bằng chứng hoặc nguyên nhân.","en":"These calls are separate from generation/verifier calls. Choosing a step does not validate evidence quality or a cause."},
  "Không gọi Typesafe trong phiên này.": {"vi":"Không gọi Typesafe trong phiên này.","en":"No Typesafe call was made in this run."},
  "Quyết định {number}: {action}": {"vi":"Quyết định {number}: {action}","en":"Decision {number}: {action}"},
  "Fallback theo luật: {fallback}": {"vi":"Fallback theo luật: {fallback}","en":"Rule fallback: {fallback}"},
  "Nguồn quyết định": {"vi":"Nguồn quyết định","en":"Decision provenance"},
  "Đã ghi nhận usage": {"vi":"Đã ghi nhận usage","en":"Usage recorded"},
  "Lời gọi lỗi; giữ cận chi phí": {"vi":"Lời gọi lỗi; giữ cận chi phí","en":"Call failed; cost bound retained"},
  "Thiếu usage; giữ cận chi phí": {"vi":"Thiếu usage; giữ cận chi phí","en":"Usage missing; cost bound retained"},
  "Usage vượt cận chi phí": {"vi":"Usage vượt cận chi phí","en":"Usage exceeded the cost bound"},
  "Đã chặn trước khi gọi": {"vi":"Đã chặn trước khi gọi","en":"Blocked before calling"},
  "Chưa có receipt hợp lệ": {"vi":"Chưa có receipt hợp lệ","en":"No valid receipt available"},
  "USD ghi nhận/giữ chỗ: {usd}; chưa phải hóa đơn.": {"vi":"USD ghi nhận/giữ chỗ: {usd}; chưa phải hóa đơn.","en":"Recorded/reserved USD: {usd}; not a provider invoice."},
  "Chi phí quyết định Jev / Typesafe": {"vi":"Chi phí quyết định Jev / Typesafe","en":"Jev / Typesafe decision costs"},
  "Chi phí sinh giả thuyết / verifier": {"vi":"Chi phí sinh giả thuyết / verifier","en":"Generation / verifier costs"},
  "Receipt Typesafe được tính riêng với Groq; số tiền ghi nhận/giữ chỗ chưa phải hóa đơn.": {"vi":"Receipt Typesafe được tính riêng với Groq; số tiền ghi nhận/giữ chỗ chưa phải hóa đơn.","en":"Typesafe receipts are separate from Groq; recorded/reserved amounts are not a provider invoice."},
  "Chưa có receipt Typesafe; chưa coi chi phí là 0. Các lượt bị chặn có thể chưa gửi API.": {"vi":"Chưa có receipt Typesafe; chưa coi chi phí là 0. Các lượt bị chặn có thể chưa gửi API.","en":"No Typesafe receipt is available; cost is not assumed to be zero. Blocked attempts may not have reached the API."},
  "Chưa kiểm được receipt Typesafe của phiên này; chi phí chưa biết. Không cộng vào chi phí Groq.": {"vi":"Chưa kiểm được receipt Typesafe của phiên này; chi phí chưa biết. Không cộng vào chi phí Groq.","en":"Typesafe receipts for this run could not be checked; cost is unknown. It is not added to Groq costs."},
  "{count} receipt Typesafe · USD ghi nhận/giữ chỗ {usd}": {"vi":"{count} receipt Typesafe · USD ghi nhận/giữ chỗ {usd}","en":"{count} Typesafe receipts · recorded/reserved USD {usd}"},
  "{count} lượt quyết định dùng fallback theo luật": {"vi":"{count} lượt quyết định dùng fallback theo luật","en":"{count} decisions used rule fallback"},
  "Quyết định {number}": {"vi":"Quyết định {number}","en":"Decision {number}"},
  "Đang giữ chỗ chi phí": {"vi":"Đang giữ chỗ chi phí","en":"Cost reserved"},
  "Token Typesafe: vào {input}, ra {output}": {"vi":"Token Typesafe: vào {input}, ra {output}","en":"Typesafe tokens: input {input}, output {output}"},
  "Usage Typesafe vượt cận; cần kiểm ledger trước khi gọi tiếp.": {"vi":"Usage Typesafe vượt cận; cần kiểm ledger trước khi gọi tiếp.","en":"Typesafe usage exceeded its bound; inspect the ledger before further calls."},
  "Xuất receipt Typesafe JSON": {"vi":"Xuất receipt Typesafe JSON","en":"Export Typesafe receipt JSON"},
  "Có": {"vi":"Có","en":"Yes"},
  "Không": {"vi":"Không","en":"No"},
  "Planned · điều tra nhiều bước": {"vi":"Planned · điều tra nhiều bước","en":"Planned · bounded investigation"},
  "Planned dùng tín hiệu quan sát để giới hạn 1–3 vòng tìm thêm, mỗi vòng tối đa 3 truy vấn/6 trích đoạn mới (tổng 18). Khi bật Groq: tối đa 4 lượt sinh + 1 verifier; mỗi lượt sinh có thể sửa schema một lần (tối đa 9 call). Có thể dừng sớm; ledger và hủy tác vụ vẫn áp dụng. Đây chưa phải đánh giá độ khó hoặc chất lượng.": {"vi":"Planned dùng tín hiệu quan sát để giới hạn 1–3 vòng tìm thêm, mỗi vòng tối đa 3 truy vấn/6 trích đoạn mới (tổng 18). Khi bật Groq: tối đa 4 lượt sinh + 1 verifier; mỗi lượt sinh có thể sửa schema một lần (tối đa 9 call). Có thể dừng sớm; ledger và hủy tác vụ vẫn áp dụng. Đây chưa phải đánh giá độ khó hoặc chất lượng.","en":"Planned uses observable cues to bound 1–3 search rounds, each with up to 3 queries/6 new excerpts (18 total). With Groq: up to 4 generation passes + 1 verifier; each generation may repair its schema once (up to 9 calls). It may stop earlier; ledger and cancellation still apply. This is not an assessed difficulty or quality level."},
  "Điều phối điều tra": {"vi":"Điều phối điều tra","en":"Investigation workflow"},
  "Báo cáo này dùng luồng Quick/Deep cũ, không có kế hoạch Planned đã lưu. Trace gốc bên dưới giữ nguyên; không suy diễn các vòng chưa ghi nhận.": {"vi":"Báo cáo này dùng luồng Quick/Deep cũ, không có kế hoạch Planned đã lưu. Trace gốc bên dưới giữ nguyên; không suy diễn các vòng chưa ghi nhận.","en":"This report uses the existing Quick/Deep workflow and has no saved Planned workflow. Its original trace remains below; unrecorded rounds are not inferred."},
  "Không thể gắn kế hoạch với query và snapshot của báo cáo này. Citation đã ẩn; có thể xem JSON gốc để kiểm tra.": {"vi":"Không thể gắn kế hoạch với query và snapshot của báo cáo này. Citation đã ẩn; có thể xem JSON gốc để kiểm tra.","en":"This plan cannot be bound to this report's query and snapshot. Citations are hidden; inspect the original JSON for details."},
  "Kế hoạch hữu hạn theo tín hiệu quan sát; chưa đánh giá độ khó hoặc chất lượng. Nguồn tìm thêm chưa phải phản bác đã xác nhận.": {"vi":"Kế hoạch hữu hạn theo tín hiệu quan sát; chưa đánh giá độ khó hoặc chất lượng. Nguồn tìm thêm chưa phải phản bác đã xác nhận.","en":"A bounded plan based on observable cues; difficulty and quality have not been evaluated. Additional retrieved sources are not confirmed counter-evidence."},
  "Vì sao có giới hạn này?": {"vi":"Vì sao có giới hạn này?","en":"Why this limit?"},
  "Một file hoặc chưa có cue cần mở rộng": {"vi":"Một file hoặc chưa có cue cần mở rộng","en":"One file or no cue requiring broader search"},
  "Bằng chứng nằm ở nhiều file": {"vi":"Bằng chứng nằm ở nhiều file","en":"Retrieved evidence spans multiple files"},
  "Giả thuyết còn thiếu nguồn phản bác": {"vi":"Giả thuyết còn thiếu nguồn phản bác","en":"Hypotheses are missing counter-evidence"},
  "Snapshot có phần không đọc được": {"vi":"Snapshot có phần không đọc được","en":"The snapshot contains unreadable parts"},
  "Nhiều file và thiếu nguồn phản bác": {"vi":"Nhiều file và thiếu nguồn phản bác","en":"Multiple files and missing counter-evidence"},
  "{files} file code · {missing} giả thuyết thiếu phản bác · {parse} lỗi parse · {skipped} file bị bỏ qua": {"vi":"{files} file code · {missing} giả thuyết thiếu phản bác · {parse} lỗi parse · {skipped} file bị bỏ qua","en":"{files} code files · {missing} hypotheses missing counters · {parse} parse errors · {skipped} skipped files"},
  "Vòng tìm thêm": {"vi":"Vòng tìm thêm","en":"Search rounds"},
  "Lượt sinh giả thuyết": {"vi":"Lượt sinh giả thuyết","en":"Generation passes"},
  "Lượt verifier riêng": {"vi":"Lượt verifier riêng","en":"Separate verifier passes"},
  "Call provider đã ghi nhận": {"vi":"Call provider đã ghi nhận","en":"Recorded provider calls"},
  "Giới hạn: {calls} call provider, tối đa 3 truy vấn/6 trích đoạn mới mỗi vòng và 18 trích đoạn mới tổng cộng. Lượt sinh có thể kèm một lần sửa schema. Chi phí thực tế xem ở Token và chi phí.": {"vi":"Giới hạn: {calls} call provider, tối đa 3 truy vấn/6 trích đoạn mới mỗi vòng và 18 trích đoạn mới tổng cộng. Lượt sinh có thể kèm một lần sửa schema. Chi phí thực tế xem ở Token và chi phí.","en":"Limit: {calls} provider calls, up to 3 queries/6 new excerpts per round and 18 new excerpts overall. Each generation may include one schema repair. See Tokens and cost for actual accounting."},
  "Bắt đầu với {count} nguồn bằng chứng": {"vi":"Bắt đầu với {count} nguồn bằng chứng","en":"Started with {count} evidence sources"},
  "Bước {round}: {action}": {"vi":"Bước {round}: {action}","en":"Step {round}: {action}"},
  "Tìm thêm bằng chứng": {"vi":"Tìm thêm bằng chứng","en":"Retrieve more evidence"},
  "Chuyển sang verifier": {"vi":"Chuyển sang verifier","en":"Proceed to verifier"},
  "Dừng điều tra": {"vi":"Dừng điều tra","en":"Stop investigation"},
  "{queries} truy vấn đã chạy · {added} trích đoạn mới": {"vi":"{queries} truy vấn đã chạy · {added} trích đoạn mới","en":"{queries} executed queries · {added} new excerpts"},
  "Xem quyết định và truy vấn đã lưu": {"vi":"Xem quyết định và truy vấn đã lưu","en":"Inspect saved decision and queries"},
  "Lý do: {reason}": {"vi":"Lý do: {reason}","en":"Reason: {reason}"},
  "Controller: {controller} · fallback theo luật: {fallback}": {"vi":"Controller: {controller} · fallback theo luật: {fallback}","en":"Controller: {controller} · rule fallback: {fallback}"},
  "Giả thuyết đầu vào: {id}": {"vi":"Giả thuyết đầu vào: {id}","en":"Input hypothesis: {id}"},
  "{count} truy vấn trùng đã bỏ qua; chưa chạy lại.": {"vi":"{count} truy vấn trùng đã bỏ qua; chưa chạy lại.","en":"{count} repeated queries skipped; they were not executed again."},
  "Chưa bật provider; dừng ở bằng chứng": {"vi":"Chưa bật provider; dừng ở bằng chứng","en":"Provider not enabled; stopped at evidence"},
  "Chưa đủ bằng chứng để sinh giả thuyết": {"vi":"Chưa đủ bằng chứng để sinh giả thuyết","en":"Insufficient evidence to generate hypotheses"},
  "Không còn truy vấn mới trong giới hạn": {"vi":"Không còn truy vấn mới trong giới hạn","en":"No new queries within the search limit"},
  "Không tìm thêm bằng chứng mới": {"vi":"Không tìm thêm bằng chứng mới","en":"No new evidence found"},
  "Đã chạm giới hạn vòng tìm thêm": {"vi":"Đã chạm giới hạn vòng tìm thêm","en":"Search round limit reached"},
  "Controller chọn dừng": {"vi":"Controller chọn dừng","en":"Controller selected stop"},
  "Controller chuyển thẳng sang verifier": {"vi":"Controller chuyển thẳng sang verifier","en":"Controller proceeded directly to verifier"},
  "Dừng tìm kiếm không xác nhận nguyên nhân hoặc bản sửa. Verifier là nhận xét model riêng, chưa phải kiểm người.": {"vi":"Dừng tìm kiếm không xác nhận nguyên nhân hoặc bản sửa. Verifier là nhận xét model riêng, chưa phải kiểm người.","en":"Stopping search does not confirm a cause or a fix. The verifier is a separate model assessment, not human validation."},
  "Nguồn kế hoạch": {"vi":"Nguồn kế hoạch","en":"Workflow provenance"},
  "planned_input": {"vi":"Đầu vào kế hoạch điều tra","en":"Planned investigation input"},
  "planned_retrieval_1": {"vi":"Tìm thêm bằng chứng · vòng 1","en":"Evidence search · round 1"},
  "planned_retrieval_2": {"vi":"Tìm thêm bằng chứng · vòng 2","en":"Evidence search · round 2"},
  "planned_retrieval_3": {"vi":"Tìm thêm bằng chứng · vòng 3","en":"Evidence search · round 3"},
  "planned_generation_1": {"vi":"Sinh lại giả thuyết · vòng 1","en":"Hypothesis regeneration · round 1"},
  "planned_generation_2": {"vi":"Sinh lại giả thuyết · vòng 2","en":"Hypothesis regeneration · round 2"},
  "planned_generation_3": {"vi":"Sinh lại giả thuyết · vòng 3","en":"Hypothesis regeneration · round 3"},
  "planned_complete": {"vi":"Kế hoạch đã kết thúc","en":"Planned investigation complete"},
  "Mở phiên nguồn của câu hỏi": {"vi":"Mở phiên nguồn của câu hỏi","en":"Open the question source run"},
  "Giả thuyết và citation của câu hỏi thuộc phiên nguồn: {id}": {"vi":"Giả thuyết và citation của câu hỏi thuộc phiên nguồn: {id}","en":"Question hypotheses and citations belong to source run: {id}"},
  "IssueIntel · Điều tra issue": {
    "vi": "IssueIntel · Điều tra issue",
    "en": "IssueIntel · Issue investigation"
  },
  "Local workspace": {
    "vi": "Không gian làm việc",
    "en": "Local workspace"
  },
  "Ngôn ngữ": {
    "vi": "Ngôn ngữ",
    "en": "Language"
  },
  "Giao diện": {
    "vi": "Giao diện",
    "en": "Appearance"
  },
  "Sáng": {
    "vi": "Sáng",
    "en": "Light"
  },
  "Tối": {
    "vi": "Tối",
    "en": "Dark"
  },
  "Theo hệ thống": {
    "vi": "Theo hệ thống",
    "en": "System"
  },
  "EVIDENCE-GROUNDED INVESTIGATION": {
    "vi": "ĐIỀU TRA DỰA TRÊN BẰNG CHỨNG",
    "en": "EVIDENCE-GROUNDED INVESTIGATION"
  },
  "Hiểu issue. Lần theo bằng chứng.": {
    "vi": "Hiểu issue. Lần theo bằng chứng.",
    "en": "Understand issues. Follow the evidence."
  },
  "Định vị code, đối chiếu giả thuyết và giữ lại những điều chưa biết.": {
    "vi": "Định vị code, đối chiếu giả thuyết và giữ lại những điều chưa biết.",
    "en": "Locate code, compare hypotheses, and keep unknowns visible."
  },
  "Main Dev đã qua strict": {
    "vi": "Main Dev đã qua strict",
    "en": "Strict Main Dev cases"
  },
  "Đang đọc dữ liệu cục bộ…": {
    "vi": "Đang đọc dữ liệu cục bộ…",
    "en": "Loading local data…"
  },
  "Bắt đầu điều tra": {
    "vi": "Bắt đầu điều tra",
    "en": "Start an investigation"
  },
  "Chọn dữ liệu đã kiểm hoặc nhập issue của bạn.": {
    "vi": "Chọn dữ liệu đã kiểm hoặc nhập issue của bạn.",
    "en": "Choose an audited case or enter your own issue."
  },
  "Nguồn đầu vào": {
    "vi": "Nguồn đầu vào",
    "en": "Input source"
  },
  "Main Dev · phiên bản lúc mở issue": {
    "vi": "Main Dev · phiên bản lúc mở issue",
    "en": "Main Dev · opening issue version"
  },
  "GitHub issue URL": {
    "vi": "GitHub issue URL",
    "en": "GitHub issue URL"
  },
  "Issue của bạn": {
    "vi": "Issue của bạn",
    "en": "Your issue"
  },
  "Ví dụ tổng hợp · không cần tải dữ liệu": {
    "vi": "Ví dụ tổng hợp · không cần tải dữ liệu",
    "en": "Synthetic example · no dataset required"
  },
  "Revision Git": {
    "vi": "Revision Git",
    "en": "Git revision"
  },
  "HEAD hoặc commit SHA": {
    "vi": "HEAD hoặc commit SHA",
    "en": "HEAD or commit SHA"
  },
  "HEAD lấy code hiện tại. Mô tả GitHub hiện tại chưa được xác minh là phiên bản ban đầu.": {
    "vi": "HEAD lấy code hiện tại. Mô tả GitHub hiện tại chưa được xác minh là phiên bản ban đầu.",
    "en": "HEAD uses current code. The current GitHub description is not verified as the opening version."
  },
  "Tìm theo repository hoặc mã ca": {
    "vi": "Tìm theo repository hoặc mã ca",
    "en": "Search by repository or case ID"
  },
  "Ví dụ: requests, sympy…": {
    "vi": "Ví dụ: requests, sympy…",
    "en": "For example: requests, sympy…"
  },
  "Ca Main Dev": {
    "vi": "Ca Main Dev",
    "en": "Main Dev case"
  },
  "Đang tải…": {
    "vi": "Đang tải…",
    "en": "Loading…"
  },
  "Query và snapshot được kiểm lại trước khi điều tra.": {
    "vi": "Query và snapshot được kiểm lại trước khi điều tra.",
    "en": "The query and snapshot are rechecked before investigation."
  },
  "Repository": {
    "vi": "Repository",
    "en": "Repository"
  },
  "owner/repo hoặc đường dẫn Git trên máy": {
    "vi": "owner/repo hoặc đường dẫn Git trên máy",
    "en": "owner/repo or a local Git path"
  },
  "Revision": {
    "vi": "Revision",
    "en": "Revision"
  },
  "Commit SHA hoặc ref cụ thể": {
    "vi": "Commit SHA hoặc ref cụ thể",
    "en": "Commit SHA or a specific ref"
  },
  "Mô tả issue": {
    "vi": "Mô tả issue",
    "en": "Issue description"
  },
  "Triệu chứng, traceback, ví dụ và hành vi mong đợi…": {
    "vi": "Triệu chứng, traceback, ví dụ và hành vi mong đợi…",
    "en": "Symptoms, traceback, examples, and expected behavior…"
  },
  "Revision bạn chọn được ghi trong báo cáo. Thời gian lịch sử của đầu vào này chưa được xác minh.": {
    "vi": "Revision bạn chọn được ghi trong báo cáo. Thời gian lịch sử của đầu vào này chưa được xác minh.",
    "en": "Your revision is recorded in the report. Historical availability of this input is unverified."
  },
  "Ví dụ Python nhỏ được tạo trên máy. Đây là dữ liệu tổng hợp để thử luồng sản phẩm.": {
    "vi": "Ví dụ Python nhỏ được tạo trên máy. Đây là dữ liệu tổng hợp để thử luồng sản phẩm.",
    "en": "A small Python example is created locally. This is synthetic data for trying the workflow."
  },
  "Mức điều tra": {
    "vi": "Mức điều tra",
    "en": "Investigation depth"
  },
  "Quick · tập bằng chứng gọn": {
    "vi": "Quick · tập bằng chứng gọn",
    "en": "Quick · focused evidence"
  },
  "Deep · mở rộng code liên quan": {
    "vi": "Deep · mở rộng code liên quan",
    "en": "Deep · broader code context"
  },
  "Sinh giả thuyết và chạy verifier bằng Groq": {
    "vi": "Sinh giả thuyết và chạy verifier bằng Groq",
    "en": "Generate hypotheses and run a verifier with Groq"
  },
  "Khi bật, issue, code excerpts và nguồn repository liên quan đã chọn được gửi đến Groq; chi phí theo cấu hình API của bạn. Khi tắt, chỉ truy xuất bằng chứng.": {
    "vi": "Khi bật, issue, code excerpts và nguồn repository liên quan đã chọn được gửi đến Groq; chi phí theo cấu hình API của bạn. Khi tắt, chỉ truy xuất bằng chứng.",
    "en": "When enabled, the issue, code excerpts and selected repository sources are sent to Groq using your API configuration. Otherwise, only evidence is retrieved."
  },
  "Điều tra issue": {
    "vi": "Điều tra issue",
    "en": "Investigate issue"
  },
  "Hủy tác vụ": {
    "vi": "Hủy tác vụ",
    "en": "Cancel job"
  },
  "Kết quả điều tra": {
    "vi": "Kết quả điều tra",
    "en": "Investigation results"
  },
  "INVESTIGATION PACK": {
    "vi": "BÁO CÁO ĐIỀU TRA",
    "en": "INVESTIGATION PACK"
  },
  "Bằng chứng trước, kết luận sau.": {
    "vi": "Bằng chứng trước, kết luận sau.",
    "en": "Evidence before conclusions."
  },
  "Markdown ↓": {
    "vi": "Markdown ↓",
    "en": "Markdown ↓"
  },
  "JSON ↓": {
    "vi": "JSON ↓",
    "en": "JSON ↓"
  },
  "Mỗi nhận định cần một nguồn.": {
    "vi": "Mỗi nhận định cần một nguồn.",
    "en": "Every claim needs a source."
  },
  "Chạy một ca để xem snapshot, code liên quan, các giả thuyết và bước kiểm tra tiếp theo.": {
    "vi": "Chạy một ca để xem snapshot, code liên quan, các giả thuyết và bước kiểm tra tiếp theo.",
    "en": "Run a case to inspect its snapshot, relevant code, hypotheses, and next checks."
  },
  "Thử ví dụ tổng hợp →": {
    "vi": "Thử ví dụ tổng hợp →",
    "en": "Try the synthetic example →"
  },
  "Nội dung báo cáo": {
    "vi": "Nội dung báo cáo",
    "en": "Report contents"
  },
  "Bằng chứng": {
    "vi": "Bằng chứng",
    "en": "Evidence"
  },
  "Giả thuyết": {
    "vi": "Giả thuyết",
    "en": "Hypotheses"
  },
  "Kiểm chứng": {
    "vi": "Kiểm chứng",
    "en": "Verification"
  },
  "Tiến trình": {
    "vi": "Tiến trình",
    "en": "Trace"
  },
  "GitHub": {
    "vi": "GitHub",
    "en": "GitHub"
  },
  "Lịch sử điều tra": {
    "vi": "Lịch sử điều tra",
    "en": "Investigation history"
  },
  "Kết quả lưu cục bộ. Mở lại báo cáo không gọi model.": {
    "vi": "Kết quả lưu cục bộ. Mở lại báo cáo không gọi model.",
    "en": "Results stay local. Reopening a report does not call a model."
  },
  "So sánh hai phiên điều tra": {
    "vi": "So sánh hai phiên điều tra",
    "en": "Compare two investigations"
  },
  "Chọn hai kết quả của cùng issue. So sánh không gọi model và không xác nhận chất lượng hay bản sửa.": {
    "vi": "Chọn hai kết quả của cùng issue. So sánh không gọi model và không xác nhận chất lượng hay bản sửa.",
    "en": "Choose two results for the same issue. Comparing does not call a model or verify quality or a fix."
  },
  "Phiên trước": {
    "vi": "Phiên trước",
    "en": "Earlier run"
  },
  "Phiên sau": {
    "vi": "Phiên sau",
    "en": "Later run"
  },
  "So sánh": {
    "vi": "So sánh",
    "en": "Compare"
  },
  "IssueIntel · Snapshot Git chỉ đọc · Nhãn tự động chưa được người kiểm độc lập": {
    "vi": "IssueIntel · Snapshot Git chỉ đọc · Nhãn tự động chưa được người kiểm độc lập",
    "en": "IssueIntel · Read-only Git snapshots · Automatic labels have no independent human review"
  },
  "Không có ca phù hợp": {
    "vi": "Không có ca phù hợp",
    "en": "No matching cases"
  },
  "Không đọc được kết quả.": {
    "vi": "Không đọc được kết quả.",
    "en": "Could not read the result."
  },
  "Đang chuẩn bị snapshot và bằng chứng…": {
    "vi": "Đang chuẩn bị snapshot và bằng chứng…",
    "en": "Preparing the snapshot and evidence…"
  },
  "Đã lưu investigation pack cục bộ.": {
    "vi": "Đã lưu investigation pack cục bộ.",
    "en": "Investigation pack saved locally."
  },
  "Tác vụ chưa hoàn tất.": {
    "vi": "Tác vụ chưa hoàn tất.",
    "en": "The job did not complete."
  },
  "Đang kiểm query và snapshot…": {
    "vi": "Đang kiểm query và snapshot…",
    "en": "Checking the query and snapshot…"
  },
  "Đang truy xuất, điều tra và kiểm chứng…": {
    "vi": "Đang truy xuất, điều tra và kiểm chứng…",
    "en": "Retrieving, investigating, and verifying…"
  },
  "Snapshot": {
    "vi": "Snapshot",
    "en": "Snapshot"
  },
  "{count} nguồn": {
    "vi": "{count} nguồn",
    "en": "{count} sources"
  },
  "Thời gian": {
    "vi": "Thời gian",
    "en": "Time basis"
  },
  "Strict issue-time": {
    "vi": "Strict issue-time",
    "en": "Strict issue-time"
  },
  "Chưa xác minh lịch sử": {
    "vi": "Chưa xác minh lịch sử",
    "en": "Historical availability unverified"
  },
  "Chưa gọi model để sinh giả thuyết. Bạn có thể đọc bằng chứng hiện có hoặc bật Groq khi điều tra.": {
    "vi": "Chưa gọi model để sinh giả thuyết. Bạn có thể đọc bằng chứng hiện có hoặc bật Groq khi điều tra.",
    "en": "No model has generated hypotheses. Read the evidence or enable Groq for a new investigation."
  },
  "Bằng chứng hỗ trợ:": {
    "vi": "Bằng chứng hỗ trợ:",
    "en": "Supporting evidence:"
  },
  "Bằng chứng phản bác: ": {
    "vi": "Bằng chứng phản bác: ",
    "en": "Counter-evidence: "
  },
  "chưa tìm thấy": {
    "vi": "chưa tìm thấy",
    "en": "not found"
  },
  "Cách kiểm tra: ": {
    "vi": "Cách kiểm tra: ",
    "en": "How to test: "
  },
  "Chưa xác minh nguyên nhân": {
    "vi": "Chưa xác minh nguyên nhân",
    "en": "Cause unverified"
  },
  "Citation hợp lệ không đồng nghĩa đã chứng minh nguyên nhân. Critique bằng model được báo riêng với kiểm người.": {
    "vi": "Citation hợp lệ không đồng nghĩa đã chứng minh nguyên nhân. Critique bằng model được báo riêng với kiểm người.",
    "en": "Valid citations do not prove causality. Model critique is reported separately from human review."
  },
  "Chưa có verifier pass cho lần điều tra này.": {
    "vi": "Chưa có verifier pass cho lần điều tra này.",
    "en": "No verifier pass is available for this run."
  },
  "Bước kiểm tra tiếp theo": {
    "vi": "Bước kiểm tra tiếp theo",
    "en": "Next checks"
  },
  "Tạo bản nháp comment để duyệt": {
    "vi": "Tạo bản nháp comment để duyệt",
    "en": "Prepare a comment for review"
  },
  "Bản nháp chứa snapshot, code links và kết quả điều tra. Chỉ đăng sau khi bạn xem nội dung và xác nhận đúng issue.": {
    "vi": "Bản nháp chứa snapshot, code links và kết quả điều tra. Chỉ đăng sau khi bạn xem nội dung và xác nhận đúng issue.",
    "en": "The draft contains the snapshot, code links, and results. Publish only after reviewing the content and target issue."
  },
  "Issue đích: ": {
    "vi": "Issue đích: ",
    "en": "Target issue: "
  },
  "Tôi đã duyệt nội dung này và đồng ý đăng lên issue đích.": {
    "vi": "Tôi đã duyệt nội dung này và đồng ý đăng lên issue đích.",
    "en": "I reviewed this content and agree to publish it to the target issue."
  },
  "Đăng comment đã duyệt": {
    "vi": "Đăng comment đã duyệt",
    "en": "Publish reviewed comment"
  },
  "Đã đăng: ": {
    "vi": "Đã đăng: ",
    "en": "Published: "
  },
  "Cần một lần điều tra bằng GitHub issue URL đã hoàn tất để tạo bản nháp.": {
    "vi": "Cần một lần điều tra bằng GitHub issue URL đã hoàn tất để tạo bản nháp.",
    "en": "A completed investigation from a GitHub issue URL is required to prepare a draft."
  },
  "Ví dụ tổng hợp": {
    "vi": "Ví dụ tổng hợp",
    "en": "Synthetic example"
  },
  "Main Dev": {
    "vi": "Main Dev",
    "en": "Main Dev"
  },
  "Chưa có lần điều tra nào.": {
    "vi": "Chưa có lần điều tra nào.",
    "en": "No investigations yet."
  },
  "Cần ít nhất hai phiên đã hoàn tất.": {
    "vi": "Cần ít nhất hai phiên đã hoàn tất.",
    "en": "At least two completed runs are required."
  },
  "Đang kiểm báo cáo và đối chiếu…": {
    "vi": "Đang kiểm báo cáo và đối chiếu…",
    "en": "Checking and comparing reports…"
  },
  "Đã so sánh hai báo cáo đã lưu.": {
    "vi": "Đã so sánh hai báo cáo đã lưu.",
    "en": "Compared the two saved reports."
  },
  "Cùng query và snapshot": {
    "vi": "Cùng query và snapshot",
    "en": "Same query and snapshot"
  },
  "Context đã thay đổi · cần gắn lại citation": {
    "vi": "Context đã thay đổi · cần gắn lại citation",
    "en": "Context changed · citations need rebinding"
  },
  "Trước: ": {
    "vi": "Trước: ",
    "en": "Before: "
  },
  "Sau: ": {
    "vi": "Sau: ",
    "en": "After: "
  },
  "Thay đổi: ": {
    "vi": "Thay đổi: ",
    "en": "Changes: "
  },
  "Khác biệt giữa đầu ra không chứng minh cải thiện chất lượng hoặc đã sửa lỗi.": {
    "vi": "Khác biệt giữa đầu ra không chứng minh cải thiện chất lượng hoặc đã sửa lỗi.",
    "en": "Output differences do not prove better quality or a verified fix."
  },
  "Giữ nguyên": {
    "vi": "Giữ nguyên",
    "en": "Unchanged"
  },
  "Thêm": {
    "vi": "Thêm",
    "en": "Added"
  },
  "Bỏ": {
    "vi": "Bỏ",
    "en": "Removed"
  },
  "Nội dung đổi": {
    "vi": "Nội dung đổi",
    "en": "Content changed"
  },
  "Cùng nội dung · cần gắn lại citation": {
    "vi": "Cùng nội dung · cần gắn lại citation",
    "en": "Same content · rebinding required"
  },
  "Nguồn": {
    "vi": "Nguồn",
    "en": "Source"
  },
  "Khác biệt": {
    "vi": "Khác biệt",
    "en": "Difference"
  },
  "SHA256 trước → sau": {
    "vi": "SHA256 trước → sau",
    "en": "SHA256 before → after"
  },
  "Giả thuyết: {same} giữ nguyên · {added} thêm · {removed} bỏ": {
    "vi": "Giả thuyết: {same} giữ nguyên · {added} thêm · {removed} bỏ",
    "en": "Hypotheses: {same} unchanged · {added} added · {removed} removed"
  },
  "Nguồn báo cáo và giới hạn": {
    "vi": "Nguồn báo cáo và giới hạn",
    "en": "Report sources and limitations"
  },
  "Không thể so sánh: chọn hai phiên hoàn tất của cùng issue và repository, với file còn nguyên vẹn.": {
    "vi": "Không thể so sánh: chọn hai phiên hoàn tất của cùng issue và repository, với file còn nguyên vẹn.",
    "en": "Cannot compare: choose two completed runs of the same issue and repository with intact files."
  },
  "Không xuất được báo cáo.": {
    "vi": "Không xuất được báo cáo.",
    "en": "Could not export the report."
  },
  "Đã yêu cầu hủy; lời gọi API đang xử lý có thể vẫn tính phí.": {
    "vi": "Đã yêu cầu hủy; lời gọi API đang xử lý có thể vẫn tính phí.",
    "en": "Cancellation requested; an in-flight API call may still be charged."
  },
  "{count} pending · Holdout giữ riêng": {
    "vi": "{count} pending · Holdout giữ riêng",
    "en": "{count} pending · Holdout kept separate"
  },
  "Chưa có dataset cục bộ": {
    "vi": "Chưa có dataset cục bộ",
    "en": "No local dataset"
  },
  "Không đọc được dữ liệu": {
    "vi": "Không đọc được dữ liệu",
    "en": "Could not load data"
  },
  "queued": {
    "vi": "Đang chờ",
    "en": "Queued"
  },
  "running": {
    "vi": "Đang chạy",
    "en": "Running"
  },
  "completed": {
    "vi": "Hoàn tất",
    "en": "Completed"
  },
  "failed": {
    "vi": "Thất bại",
    "en": "Failed"
  },
  "cancelled": {
    "vi": "Đã hủy",
    "en": "Cancelled"
  },
  "interrupted": {
    "vi": "Bị gián đoạn",
    "en": "Interrupted"
  },
  "supported": {
    "vi": "Được hỗ trợ",
    "en": "Supported"
  },
  "unsupported": {
    "vi": "Chưa được hỗ trợ",
    "en": "Unsupported"
  },
  "contradicted": {
    "vi": "Bị phản bác",
    "en": "Contradicted"
  },
  "uncertain": {
    "vi": "Chưa chắc chắn",
    "en": "Uncertain"
  },
  "not_found": {
    "vi": "Chưa tìm thấy",
    "en": "Not found"
  },
  "Lỗi yêu cầu. Kiểm tra đầu vào hoặc dữ liệu cục bộ.": {
    "vi": "Lỗi yêu cầu. Kiểm tra đầu vào hoặc dữ liệu cục bộ.",
    "en": "Request failed. Check the input or local data."
  },
  "Không kết nối được server local.": {
    "vi": "Không kết nối được server local.",
    "en": "Could not connect to the local server."
  },
  "Tác vụ không hoàn tất. Kiểm tra đầu vào, dữ liệu hoặc cấu hình provider; không tự thử lại.": {
    "vi": "Tác vụ không hoàn tất. Kiểm tra đầu vào, dữ liệu hoặc cấu hình provider; không tự thử lại.",
    "en": "The job did not complete. Check inputs, data, or provider configuration; it will not retry automatically."
  },
  "Thông tin đầu vào": {
    "vi": "Thông tin đầu vào",
    "en": "Intake"
  },
  "Bổ sung dữ kiện": {
    "vi": "Bổ sung dữ kiện",
    "en": "Add context"
  },
  "Phiên nguồn: ": {
    "vi": "Phiên nguồn: ",
    "en": "Source run: "
  },
  "Tạo phiên mới ở cùng commit. Dữ kiện bổ sung chưa xác minh và không kế thừa chứng nhận lịch sử.": {
    "vi": "Tạo phiên mới ở cùng commit. Dữ kiện bổ sung chưa xác minh và không kế thừa chứng nhận lịch sử.",
    "en": "Create a new run at the same commit. Added context is unverified and does not inherit historical certification."
  },
  "Dữ kiện mới hoặc trả lời thông tin còn thiếu": {
    "vi": "Dữ kiện mới hoặc trả lời thông tin còn thiếu",
    "en": "New context or answers to missing information"
  },
  "Điều tra với dữ kiện mới": {
    "vi": "Điều tra với dữ kiện mới",
    "en": "Investigate with new context"
  },
  "Loại issue: ": {
    "vi": "Loại issue: ",
    "en": "Issue type: "
  },
  "Phân loại dựa trên từ khóa, chưa xác minh. Có nhắc thông tin không có nghĩa là thông tin đầy đủ.": {
    "vi": "Phân loại dựa trên từ khóa, chưa xác minh. Có nhắc thông tin không có nghĩa là thông tin đầy đủ.",
    "en": "Keyword-based classification is unverified. Mentioning information does not establish completeness."
  },
  "Dòng {line}: ": {
    "vi": "Dòng {line}: ",
    "en": "Line {line}: "
  },
  "Cần xem lại hoặc bổ sung mục này.": {
    "vi": "Cần xem lại hoặc bổ sung mục này.",
    "en": "Review or provide this information."
  },
  "Dữ kiện bổ sung chưa xác minh; không kế thừa chứng nhận query lịch sử.": {
    "vi": "Dữ kiện bổ sung chưa xác minh; không kế thừa chứng nhận query lịch sử.",
    "en": "Added context is unverified; it does not inherit historical query certification."
  },
  "observed_behavior": {
    "vi": "Hành vi thực tế",
    "en": "Observed behavior"
  },
  "expected_behavior": {
    "vi": "Hành vi mong đợi",
    "en": "Expected behavior"
  },
  "reproduction": {
    "vi": "Cách tái hiện",
    "en": "Reproduction"
  },
  "environment": {
    "vi": "Môi trường và phiên bản",
    "en": "Environment and version"
  },
  "bug_report": {
    "vi": "Báo lỗi",
    "en": "Bug report"
  },
  "feature_request": {
    "vi": "Đề nghị tính năng",
    "en": "Feature request"
  },
  "question": {
    "vi": "Câu hỏi sử dụng",
    "en": "Usage question"
  },
  "unknown": {
    "vi": "Chưa xác định",
    "en": "Unknown"
  },
  "mentioned": {
    "vi": "Có nhắc",
    "en": "Mentioned"
  },
  "Issue liên quan": {
    "vi": "Issue liên quan",
    "en": "Related issues"
  },
  "Tìm issue liên quan trong lịch sử local": {
    "vi": "Tìm issue liên quan trong lịch sử local",
    "en": "Find related issues in local history"
  },
  "Chỉ tìm trong 50 phiên local gần nhất, cùng repository. Tương đồng chưa chứng minh duplicate; không đưa vào inference strict.": {
    "vi": "Chỉ tìm trong 50 phiên local gần nhất, cùng repository. Tương đồng chưa chứng minh duplicate; không đưa vào inference strict.",
    "en": "Search only the latest 50 local runs in the same repository. Similarity does not establish duplication; results do not enter strict inference."
  },
  "Đã đọc {scanned} phiên; {issues} issue khác; bỏ qua {skipped} nguồn lỗi.": {
    "vi": "Đã đọc {scanned} phiên; {issues} issue khác; bỏ qua {skipped} nguồn lỗi.",
    "en": "Runs read: {scanned} · Other issues: {issues} · Invalid sources skipped: {skipped}"
  },
  "Chưa tìm thấy ứng viên phù hợp trong phạm vi này.": {
    "vi": "Chưa tìm thấy ứng viên phù hợp trong phạm vi này.",
    "en": "No matching candidates found within this scope."
  },
  "Ứng viên liên quan · chưa xác nhận duplicate": {
    "vi": "Ứng viên liên quan · chưa xác nhận duplicate",
    "en": "Related candidate · duplicate status unverified"
  },
  "Điểm lexical: {score}; không phải xác suất.": {
    "vi": "Điểm lexical: {score}; không phải xác suất.",
    "en": "Lexical score: {score}; not a probability."
  },
  "Từ khớp: ": {
    "vi": "Từ khớp: ",
    "en": "Matched terms: "
  },
  "Mở báo cáo nguồn": {
    "vi": "Mở báo cáo nguồn",
    "en": "Open source report"
  },
  "Kế hoạch kiểm tra": {
    "vi": "Kế hoạch kiểm tra",
    "en": "Check plan"
  },
  "Tải lại kế hoạch": {
    "vi": "Tải lại kế hoạch",
    "en": "Reload plan"
  },
  "Xuất kế hoạch JSON": {
    "vi": "Xuất kế hoạch JSON",
    "en": "Export plan JSON"
  },
  "Phiên bản kế hoạch: {revision}": {
    "vi": "Phiên bản kế hoạch: {revision}",
    "en": "Plan revision: {revision}"
  },
  "Trạng thái và ghi chú do người dùng tự báo cáo; hệ thống không chạy code và chưa xác minh bản sửa.": {
    "vi": "Trạng thái và ghi chú do người dùng tự báo cáo; hệ thống không chạy code và chưa xác minh bản sửa.",
    "en": "Statuses and notes are user-reported; the system does not execute code or verify a fix."
  },
  "Trạng thái bước kiểm tra": {
    "vi": "Trạng thái bước kiểm tra",
    "en": "Check status"
  },
  "Ghi chú hoặc quan sát": {
    "vi": "Ghi chú hoặc quan sát",
    "en": "Notes or observations"
  },
  "Lưu bước kiểm tra": {
    "vi": "Lưu bước kiểm tra",
    "en": "Save check"
  },
  "Đã lưu phiên bản kế hoạch mới.": {
    "vi": "Đã lưu phiên bản kế hoạch mới.",
    "en": "New plan revision saved."
  },
  "Chưa lưu được. Kiểm tra ghi chú hoặc tải lại kế hoạch nếu phiên bản đã đổi.": {
    "vi": "Chưa lưu được. Kiểm tra ghi chú hoặc tải lại kế hoạch nếu phiên bản đã đổi.",
    "en": "Not saved. Check the note or reload the plan if its revision changed."
  },
  "Lịch sử thay đổi kế hoạch": {
    "vi": "Lịch sử thay đổi kế hoạch",
    "en": "Plan change history"
  },
  "todo": {
    "vi": "Chưa làm",
    "en": "To do"
  },
  "in_progress": {
    "vi": "Đang làm",
    "en": "In progress"
  },
  "blocked": {
    "vi": "Đang vướng",
    "en": "Blocked"
  },
  "reported_done": {
    "vi": "Đã làm · tự báo cáo",
    "en": "Done · self-reported"
  },
  "Chưa đọc được metadata; báo cáo nguồn vẫn có thể xuất.": {
    "vi": "Chưa đọc được metadata; báo cáo nguồn vẫn có thể xuất.",
    "en": "Metadata could not be loaded; source reports can still be exported."
  },
  "Provide a traceback, relevant component or reproducible example": {
    "vi": "Bổ sung traceback, thành phần liên quan hoặc ví dụ tái hiện",
    "en": "Provide a traceback, relevant component or reproducible example"
  },
  "Các bước đã lưu": {
    "vi": "Các bước đã lưu",
    "en": "Saved stages"
  },
  "Checkpoint là đầu ra từng bước, chưa phải báo cáo hoàn tất. Mở hoặc tải không gọi lại model.": {
    "vi": "Checkpoint là đầu ra từng bước, chưa phải báo cáo hoàn tất. Mở hoặc tải không gọi lại model.",
    "en": "Checkpoints are stage outputs, not a completed report. Opening or downloading never calls the model again."
  },
  "Tải checkpoint: {stage}": {
    "vi": "Tải checkpoint: {stage}",
    "en": "Download checkpoint: {stage}"
  },
  "Vòng truy xuất bổ sung: {rounds}/1 · lượt sinh: {passes}": {
    "vi": "Vòng truy xuất bổ sung: {rounds}/1 · lượt sinh: {passes}",
    "en": "Additional retrieval rounds: {rounds}/1 · generation passes: {passes}"
  },
  "Deep với Groq có tối đa một vòng tìm thêm evidence và một lượt sinh bổ sung; mọi call vẫn qua ledger.": {
    "vi": "Deep với Groq có tối đa một vòng tìm thêm evidence và một lượt sinh bổ sung; mọi call vẫn qua ledger.",
    "en": "Deep with Groq can add one retrieval round and one generation pass; every call still uses the budget ledger."
  },
  "prepared_input": {
    "vi": "Đầu vào đã chuẩn bị",
    "en": "Prepared input"
  },
  "seed_generation": {
    "vi": "Giả thuyết ban đầu",
    "en": "Initial hypotheses"
  },
  "focused_retrieval": {
    "vi": "Truy xuất theo giả thuyết",
    "en": "Hypothesis-focused retrieval"
  },
  "refined_generation": {
    "vi": "Giả thuyết cập nhật",
    "en": "Updated hypotheses"
  },
  "verification": {
    "vi": "Kiểm chứng",
    "en": "Verification"
  },
  "Đang xem phiên: {id}": {
    "vi": "Đang xem phiên: {id}",
    "en": "Viewing run: {id}"
  },
  "Context repository hiện tại": {"vi":"Context repository hiện tại","en":"Current repository context"},
  "Nguồn liên quan chưa xác nhận duplicate hay bản sửa; không phải proof lịch sử.": {"vi":"Nguồn liên quan chưa xác nhận duplicate hay bản sửa; không phải proof lịch sử.","en":"Related sources do not confirm duplicates or fixes and are not historical proof."},
  "Kết nối và tự đồng bộ": {"vi":"Kết nối và tự đồng bộ","en":"Connect and auto-sync"},
  "Tạm dừng tự động": {"vi":"Tạm dừng tự động","en":"Pause auto-sync"},
  "Kết nối tự chạy đến hết các trang và nhận webhook sau đó. Giới hạn GET ở trên chỉ áp dụng cho lượt đồng bộ thủ công.": {"vi":"Kết nối tự chạy đến hết các trang và nhận webhook sau đó. Giới hạn GET ở trên chỉ áp dụng cho lượt đồng bộ thủ công.","en":"Connect traverses all pages automatically, then receives webhooks. The GET limit above applies only to a manual sync run."},
  "Đồng bộ tự động đang tạm dừng.": {"vi":"Đồng bộ tự động đang tạm dừng.","en":"Auto-sync is paused."},
  "Đồng bộ tự động đã bật; không cần mở trang để tiếp tục.": {"vi":"Đồng bộ tự động đã bật; không cần mở trang để tiếp tục.","en":"Auto-sync is enabled; this page can be closed while the local server continues."},
  "Trạng thái kết nối: ": {"vi":"Trạng thái kết nối: ","en":"Connection status: "},
  "Webhook đang chờ: {count}": {"vi":"Webhook đang chờ: {count}","en":"Pending webhooks: {count}"},
  "Đã lưu kết nối và checkpoint; hệ thống hiển thị thời gian chờ hoặc trạng thái tạm dừng.": {"vi":"Đã lưu kết nối và checkpoint; hệ thống hiển thị thời gian chờ hoặc trạng thái tạm dừng.","en":"Connection and checkpoints saved. Check the cooldown or pause status."},
  "Repository đã sẵn sàng; cập nhật tiếp theo sẽ qua webhook.": {"vi":"Repository đã sẵn sàng; cập nhật tiếp theo sẽ qua webhook.","en":"Repository is ready; subsequent updates arrive through the configured webhook."},
  "Đang tự đồng bộ repository; có thể đóng trang, server vẫn tiếp tục.": {"vi":"Đang tự đồng bộ repository; có thể đóng trang, server vẫn tiếp tục.","en":"Auto-syncing the repository. You can close this page; the local server continues."},
  "Webhook chưa cấu hình; cần thiết lập kết nối GitHub một lần để nhận sự kiện.": {"vi":"Webhook chưa cấu hình; cần thiết lập kết nối GitHub một lần để nhận sự kiện.","en":"Webhook is not configured. Complete GitHub connection setup once to receive events."},
  "Bộ nhớ repository GitHub": {"vi":"Bộ nhớ repository GitHub","en":"GitHub repository memory"},
  "Kết nối repo public một lần để tự lập chỉ mục code và đồng bộ lịch sử issue/PR trên máy; không gọi LLM.": {"vi":"Kết nối repo public một lần để tự lập chỉ mục code và đồng bộ lịch sử issue/PR trên máy; không gọi LLM.","en":"Connect a public repo once to automatically index code and sync issue/PR history locally; no LLM calls."},
  "Repository public": {"vi":"Repository public","en":"Public repository"},
  "GET tối đa mỗi lượt": {"vi":"GET tối đa mỗi lượt","en":"Max GETs per run"},
  "Quét đối soát mới sau khi scan trước đã hoàn tất": {"vi":"Quét đối soát mới sau khi scan trước đã hoàn tất","en":"Start a reconciliation scan after the previous scan completes"},
  "Đồng bộ / tiếp tục": {"vi":"Đồng bộ / tiếp tục","en":"Sync / resume"},
  "Xem trạng thái local": {"vi":"Xem trạng thái local","en":"View local status"},
  "Dừng sau trang hiện tại": {"vi":"Dừng sau trang hiện tại","en":"Stop after current page"},
  "Tìm trong repository đã lưu": {"vi":"Tìm trong repository đã lưu","en":"Search the saved repository"},
  "Phạm vi tìm kiếm": {"vi":"Phạm vi tìm kiếm","en":"Search scope"},
  "Issue, PR và bình luận": {"vi":"Issue, PR và bình luận","en":"Issues, PRs and comments"},
  "Code tại snapshot": {"vi":"Code tại snapshot","en":"Code at snapshot"},
  "Tìm cục bộ": {"vi":"Tìm cục bộ","en":"Search locally"},
  "Context hiện tại có thể đã chỉnh sửa; không phải proof lịch sử và không đi vào benchmark strict. Điểm tìm kiếm không xác nhận duplicate hay bản sửa.": {"vi":"Context hiện tại có thể đã chỉnh sửa; không phải proof lịch sử và không đi vào benchmark strict. Điểm tìm kiếm không xác nhận duplicate hay bản sửa.","en":"Current context may contain edits; it is not historical proof and stays outside strict benchmarks. Search scores do not confirm duplicates or fixes."},
  "Lịch sử đồng bộ": {"vi":"Lịch sử đồng bộ","en":"Sync history"},
  "Repository chưa được đồng bộ.": {"vi":"Repository chưa được đồng bộ.","en":"This repository has not been indexed yet."},
  "Đã đi hết các trang API trong scan.": {"vi":"Đã đi hết các trang API trong scan.","en":"All API pages in this scan have been traversed."},
  "Scan chưa hoàn tất; bấm tiếp tục để lấy các trang còn lại.": {"vi":"Scan chưa hoàn tất; bấm tiếp tục để lấy các trang còn lại.","en":"This scan is incomplete. Resume to fetch the remaining pages."},
  "File code": {"vi":"File code","en":"Code files"},
  "Issue": {"vi":"Issue","en":"Issues"},
  "Bình luận": {"vi":"Bình luận","en":"Comments"},
  "Trang đã lưu: {pages} · tác vụ còn lại: {pending}": {"vi":"Trang đã lưu: {pages} · tác vụ còn lại: {pending}","en":"Saved pages: {pages} · pending tasks: {pending}"},
  "Snapshot đã bỏ qua {count} file theo giới hạn.": {"vi":"Snapshot đã bỏ qua {count} file theo giới hạn.","en":"The snapshot skipped {count} files according to its bounds."},
  "Đã chạm giới hạn API ở một số PR; xem coverage trước khi kết luận đầy đủ.": {"vi":"Đã chạm giới hạn API ở một số PR; xem coverage trước khi kết luận đầy đủ.","en":"Some PRs reached API caps. Inspect coverage before claiming completeness."},
  "Nguồn, phạm vi và giới hạn": {"vi":"Nguồn, phạm vi và giới hạn","en":"Sources, coverage and limitations"},
  "Không có kết quả trong phạm vi đã lưu.": {"vi":"Không có kết quả trong phạm vi đã lưu.","en":"No results in the saved scope."},
  "Đọc lúc: ": {"vi":"Đọc lúc: ","en":"Fetched at: "},
  "Nguồn từ scan trước; chưa thấy lại trong scan hiện tại.": {"vi":"Nguồn từ scan trước; chưa thấy lại trong scan hiện tại.","en":"Source from an earlier scan; not seen again in the current scan."},
  "Xem nội dung và nguồn đã lưu": {"vi":"Xem nội dung và nguồn đã lưu","en":"Read saved content and provenance"},
  "Mở nguồn GitHub": {"vi":"Mở nguồn GitHub","en":"Open GitHub source"},
  "Điền issue này vào form điều tra": {"vi":"Điền issue này vào form điều tra","en":"Use this issue in the investigation form"},
  "Chưa có lượt đồng bộ.": {"vi":"Chưa có lượt đồng bộ.","en":"No sync runs yet."},
  "Không đọc được index; kiểm tra đầu vào, nguồn và hash.": {"vi":"Không đọc được index; kiểm tra đầu vào, nguồn và hash.","en":"Cannot read the index. Check the input, sources and hashes."},
  "Đang xem checkpoint của lượt đồng bộ đã chọn.": {"vi":"Đang xem checkpoint của lượt đồng bộ đã chọn.","en":"Viewing the selected sync checkpoint."},
  "Lượt đồng bộ chưa có checkpoint; xem trạng thái local để tiếp tục.": {"vi":"Lượt đồng bộ chưa có checkpoint; xem trạng thái local để tiếp tục.","en":"This sync has no checkpoint yet. Check local status before resuming."},
  "Lượt đồng bộ đã hoàn tất.": {"vi":"Lượt đồng bộ đã hoàn tất.","en":"The sync run has finished."},
  "Đã lưu checkpoint; scan còn thiếu trang. Bấm tiếp tục khi cần.": {"vi":"Đã lưu checkpoint; scan còn thiếu trang. Bấm tiếp tục khi cần.","en":"Checkpoint saved; the scan has more pages. Resume when needed."},
  "Đồng bộ dừng; các trang đã lưu được giữ lại. Kiểm tra trạng thái và tiếp tục chủ động.": {"vi":"Đồng bộ dừng; các trang đã lưu được giữ lại. Kiểm tra trạng thái và tiếp tục chủ động.","en":"Sync stopped; saved pages are retained. Check status and resume explicitly."},
  "Đang đọc GitHub và lưu checkpoint; không gọi LLM.": {"vi":"Đang đọc GitHub và lưu checkpoint; không gọi LLM.","en":"Reading GitHub and saving checkpoints; no LLM calls."},
  "Mất kết nối theo dõi. Xem lịch sử đồng bộ trước khi tạo lượt mới.": {"vi":"Mất kết nối theo dõi. Xem lịch sử đồng bộ trước khi tạo lượt mới.","en":"Monitoring connection lost. Inspect sync history before starting another run."},
  "Không bắt đầu được: kiểm tra repo, revision, giới hạn hoặc hàng đợi.": {"vi":"Không bắt đầu được: kiểm tra repo, revision, giới hạn hoặc hàng đợi.","en":"Cannot start. Check the repository, revision, bounds or queue."},
  "Đã yêu cầu dừng sau trang hiện tại; checkpoint được giữ lại.": {"vi":"Đã yêu cầu dừng sau trang hiện tại; checkpoint được giữ lại.","en":"Requested a stop after the current page; checkpoints are retained."},
  "Đã đọc trạng thái local; không gọi GitHub hoặc LLM.": {"vi":"Đã đọc trạng thái local; không gọi GitHub hoặc LLM.","en":"Loaded local status without calling GitHub or an LLM."},
  "Checkpoint của phiên: {id}": {
    "vi": "Checkpoint của phiên: {id}",
    "en": "Checkpoints for run: {id}"
  },
  "Đối chiếu phiên bản code": {"vi": "Đối chiếu phiên bản code", "en": "Compare code revisions"},
  "So sánh commit/tag khác với snapshot của báo cáo đang xem. Chưa xác minh bản sửa hoặc bản phát hành.": {"vi": "So sánh commit/tag khác với snapshot của báo cáo đang xem. Chưa xác minh bản sửa hoặc bản phát hành.", "en": "Compare another commit/tag with this report’s snapshot. Fix and release status remain unverified."},
  "Commit hoặc tag đích": {"vi": "Commit hoặc tag đích", "en": "Target commit or tag"},
  "Đối chiếu code": {"vi": "Đối chiếu code", "en": "Compare code"},
  "Hủy đối chiếu": {"vi": "Hủy đối chiếu", "en": "Cancel comparison"},
  "Thay đổi code chưa chứng minh lỗi đã được sửa. Không chạy code/test hoặc gọi LLM.": {"vi": "Thay đổi code chưa chứng minh lỗi đã được sửa. Không chạy code/test hoặc gọi LLM.", "en": "Code changes do not prove a fix. No target code/tests or LLM calls are run."},
  "Ngoài phạm vi: {before} file nguồn, {after} file đích. Bỏ bớt {omitted} mục do giới hạn hiển thị.": {"vi": "Ngoài phạm vi: {before} file nguồn, {after} file đích. Bỏ bớt {omitted} mục do giới hạn hiển thị.", "en": "Outside scope: {before} source files, {after} target files. {omitted} entries omitted by display bounds."},
  "Xuất đối chiếu JSON": {"vi": "Xuất đối chiếu JSON", "en": "Export comparison JSON"},
  "Trong bằng chứng nguồn": {"vi": "Trong bằng chứng nguồn", "en": "In source evidence"},
  "File ngoài phạm vi đọc; không kết luận đã xóa.": {"vi": "File ngoài phạm vi đọc; không kết luận đã xóa.", "en": "File unavailable within read bounds; deletion is not established."},
  "Diff bị giới hạn; xem metadata trước khi kết luận.": {"vi": "Diff bị giới hạn; xem metadata trước khi kết luận.", "en": "Diff is bounded; inspect metadata before drawing conclusions."},
  "Mở đối chiếu: {id} · {status}": {"vi": "Mở đối chiếu: {id} · {status}", "en": "Open comparison: {id} · {status}"},
  "Không đọc được đối chiếu; kiểm tra nguồn và hash.": {"vi": "Không đọc được đối chiếu; kiểm tra nguồn và hash.", "en": "Cannot read comparison; check sources and hashes."},
  "Đang đọc hai snapshot; không gọi LLM.": {"vi": "Đang đọc hai snapshot; không gọi LLM.", "en": "Reading two snapshots; no LLM calls."},
  "Đã lưu đối chiếu phiên bản; tình trạng bản sửa chưa xác minh.": {"vi": "Đã lưu đối chiếu phiên bản; tình trạng bản sửa chưa xác minh.", "en": "Saved revision comparison; fix status remains unverified."},
  "Đối chiếu chưa hoàn tất; kiểm tra revision và dữ liệu nguồn.": {"vi": "Đối chiếu chưa hoàn tất; kiểm tra revision và dữ liệu nguồn.", "en": "Comparison did not complete; check the revision and source data."},
  "added": {"vi": "Thêm", "en": "Added"},
  "removed": {"vi": "Xóa", "en": "Removed"},
  "modified": {"vi": "Thay đổi", "en": "Modified"},
  "unchanged": {"vi": "Giữ nguyên", "en": "Unchanged"},
  "unavailable": {"vi": "Ngoài phạm vi", "en": "Unavailable"},
  "compare_versions": {"vi": "Đối chiếu phiên bản", "en": "Compare revisions"},
  "Đồ thị bằng chứng": {"vi": "Đồ thị bằng chứng", "en": "Evidence graph"},
  "Đang đọc đồ thị đã lưu…": {"vi": "Đang đọc đồ thị đã lưu…", "en": "Loading saved graph…"},
  "Không đọc được đồ thị; kiểm tra nguồn và citation.": {"vi": "Không đọc được đồ thị; kiểm tra nguồn và citation.", "en": "Cannot read graph; check sources and citations."},
  "Đường nối mô tả truy xuất và citation đã lưu, chưa chứng minh nguyên nhân. Verifier bằng model không phải kiểm người.": {"vi": "Đường nối mô tả truy xuất và citation đã lưu, chưa chứng minh nguyên nhân. Verifier bằng model không phải kiểm người.", "en": "Edges describe saved retrieval and citations, not proven causes. Model verification is not human review."},
  "Chưa có giả thuyết; đồ thị chỉ hiển thị nguồn đã truy xuất. Mở đồ thị không gọi LLM.": {"vi": "Chưa có giả thuyết; đồ thị chỉ hiển thị nguồn đã truy xuất. Mở đồ thị không gọi LLM.", "en": "No hypotheses yet; only retrieved sources are shown. Opening the graph does not call an LLM."},
  "Đã giới hạn hiển thị: {nodes} nút và {edges} cạnh bị lược bỏ.": {"vi": "Đã giới hạn hiển thị: {nodes} nút và {edges} cạnh bị lược bỏ.", "en": "Display bounded: {nodes} nodes and {edges} edges omitted."},
  "Loại quan hệ": {"vi": "Loại quan hệ", "en": "Relation type"},
  "Bỏ chọn nút": {"vi": "Bỏ chọn nút", "en": "Clear selection"},
  "Xuất đồ thị JSON": {"vi": "Xuất đồ thị JSON", "en": "Export graph JSON"},
  "Mọi quan hệ": {"vi": "Mọi quan hệ", "en": "All relations"},
  "Đồ thị bằng chứng; dùng Tab để chọn nút": {"vi": "Đồ thị bằng chứng; dùng Tab để chọn nút", "en": "Evidence graph; use Tab to select nodes"},
  "Truy vấn và giả thuyết": {"vi": "Truy vấn và giả thuyết", "en": "Query and hypotheses"},
  "Nguồn bằng chứng": {"vi": "Nguồn bằng chứng", "en": "Evidence sources"},
  "Nhận xét model": {"vi": "Nhận xét model", "en": "Model review"},
  "Chi tiết nút đã chọn": {"vi": "Chi tiết nút đã chọn", "en": "Selected node details"},
  "Chọn một nút để xem nội dung, hash và các quan hệ. Nét đứt là citation phản bác; nét chấm là nguồn truy xuất.": {"vi": "Chọn một nút để xem nội dung, hash và các quan hệ. Nét đứt là citation phản bác; nét chấm là nguồn truy xuất.", "en": "Select a node for its content, hash and relations. Dashed lines are counter-citations; dotted lines are retrieved context."},
  "Trích đoạn bị giới hạn; mở citation trong báo cáo để xem nguồn.": {"vi": "Trích đoạn bị giới hạn; mở citation trong báo cáo để xem nguồn.", "en": "Excerpt bounded; open the citation in the report to inspect its source."},
  "Mở citation trong báo cáo": {"vi": "Mở citation trong báo cáo", "en": "Open citation in report"},
  "retrieved_context": {"vi": "Nguồn truy xuất", "en": "Retrieved for query"},
  "claimed_support": {"vi": "Citation hỗ trợ", "en": "Claimed support"},
  "claimed_counter": {"vi": "Citation phản bác", "en": "Claimed counter-evidence"},
  "model_assessment": {"vi": "Nhận xét model", "en": "Model assessment"},
  "critic_citation": {"vi": "Citation của verifier", "en": "Critic citation"},
  "query": {"vi": "Truy vấn", "en": "Query"},
  "hypothesis": {"vi": "Giả thuyết", "en": "Hypothesis"},
  "model_review": {"vi": "Nhận xét model", "en": "Model review"},
  "code": {"vi": "Code", "en": "Code"},
  "repository_context": {"vi": "Context repository hiện tại", "en": "Current repository context"},
  "Lần theo từng giả thuyết": {"vi": "Lần theo từng giả thuyết", "en": "Follow each hypothesis"},
  "Nguồn → giả thuyết → nhận xét. Chọn một nút để kiểm tra liên kết.": {"vi": "Nguồn → giả thuyết → nhận xét. Chọn một nút để kiểm tra liên kết.", "en": "Sources → hypotheses → reviews. Select a node to inspect its connections."},
  "Chế độ đồ thị": {"vi": "Chế độ đồ thị", "en": "Graph view"},
  "Theo nút": {"vi": "Theo nút", "en": "Focus"},
  "Toàn bộ": {"vi": "Toàn bộ", "en": "Overview"},
  "Phóng rộng": {"vi": "Phóng rộng", "en": "Expand"},
  "Thu gọn": {"vi": "Thu gọn", "en": "Collapse"},
  "Chọn tâm đồ thị": {"vi": "Chọn tâm đồ thị", "en": "Choose graph focus"},
  "Chưa có liên kết trong phạm vi này": {"vi": "Chưa có liên kết trong phạm vi này", "en": "No connections in this scope"},
  "Đang xem {nodes} nút · {edges} liên kết": {"vi": "Đang xem {nodes} nút · {edges} liên kết", "en": "Showing {nodes} nodes · {edges} connections"},
  "Citation chưa chứng minh nguyên nhân. Nhận xét model không phải kiểm người.": {"vi": "Citation chưa chứng minh nguyên nhân. Nhận xét model không phải kiểm người.", "en": "Citations do not establish a cause. Model critique is not human review."},
  "Nguồn và hash đồ thị": {"vi": "Nguồn và hash đồ thị", "en": "Graph sources and hashes"},
  "Chi phí phiên đã chọn": {"vi": "Chi phí phiên đã chọn", "en": "Selected run accounting"},
  "Token và chi phí": {"vi": "Token và chi phí", "en": "Tokens and cost"},
  "Chưa đọc được metadata chi phí.": {"vi": "Chưa đọc được metadata chi phí.", "en": "Accounting metadata unavailable."},
  "Chi phí của phiên: {id}": {"vi": "Chi phí của phiên: {id}", "en": "Accounting for run: {id}"},
  "Phiên cũ hoặc provider chưa ghi receipt; chi phí chưa biết, không coi là 0.": {"vi": "Phiên cũ hoặc provider chưa ghi receipt; chi phí chưa biết, không coi là 0.", "en": "Legacy run or provider without receipts; cost is unknown, not zero."},
  "Lượt gọi/giữ chỗ": {"vi": "Lượt gọi/giữ chỗ", "en": "Calls / reservations"},
  "Token ghi nhận/giữ chỗ": {"vi": "Token ghi nhận/giữ chỗ", "en": "Tokens charged / reserved"},
  "USD ghi nhận/giữ chỗ": {"vi": "USD ghi nhận/giữ chỗ", "en": "USD charged / reserved"},
  "Có {count} lượt còn giữ cận trên; đây chưa phải hóa đơn thực tế.": {"vi": "Có {count} lượt còn giữ cận trên; đây chưa phải hóa đơn thực tế.", "en": "{count} calls retain upper bounds; this is not a provider invoice."},
  "Các giá trị lấy từ receipt cục bộ; không thay thế hóa đơn provider.": {"vi": "Các giá trị lấy từ receipt cục bộ; không thay thế hóa đơn provider.", "en": "Values come from local receipts; they do not replace a provider invoice."},
  "Cận token: vào {input}, ra {output}": {"vi": "Cận token: vào {input}, ra {output}", "en": "Token bounds: {input} input, {output} output"},
  "Ngày bảng giá: {date}": {"vi": "Ngày bảng giá: {date}", "en": "Price table date: {date}"},
  "Xuất receipt chi phí JSON": {"vi": "Xuất receipt chi phí JSON", "en": "Export accounting receipts JSON"},
  "reserved": {"vi": "Đang giữ chỗ", "en": "Reserved"},
  "settled": {"vi": "Đã đối soát", "en": "Settled"},
  "failed_bound": {"vi": "Lỗi, giữ cận trên", "en": "Failed, bound retained"},
  "missing_usage_bound": {"vi": "Thiếu usage, giữ cận trên", "en": "Missing usage, bound retained"},
  "breach": {"vi": "Usage bất thường, ledger bị khóa", "en": "Usage anomaly, ledger locked"},
  "Usage vượt cận hoặc bất nhất; ledger đã khóa lời gọi tiếp theo để kiểm tra.": {"vi": "Usage vượt cận hoặc bất nhất; ledger đã khóa lời gọi tiếp theo để kiểm tra.", "en": "Usage exceeded bounds or was inconsistent; the ledger blocked further calls for review."},
  "Tiếp tục verifier từ checkpoint": {"vi": "Tiếp tục verifier từ checkpoint", "en": "Continue verifier from checkpoint"},
  "Kiểm tra checkpoint để tiếp tục": {"vi": "Kiểm tra checkpoint để tiếp tục", "en": "Check checkpoint to continue"},
  "Chưa có checkpoint verifier hợp lệ cho phiên này.": {"vi": "Chưa có checkpoint verifier hợp lệ cho phiên này.", "en": "No valid verifier checkpoint is available for this run."},
  "Dùng lại {count} giả thuyết đã lưu; chỉ tiếp tục verifier.": {"vi": "Dùng lại {count} giả thuyết đã lưu; chỉ tiếp tục verifier.", "en": "Reuse {count} saved hypotheses; continue only the verifier."},
  "Cận output: {tokens} token": {"vi": "Cận output: {tokens} token", "en": "Output bound: {tokens} tokens"},
  "Bước tiếp theo gọi API và có thể tính phí. Lượt trước bị lỗi hoặc gián đoạn vẫn có thể đã bị tính phí.": {"vi": "Bước tiếp theo gọi API và có thể tính phí. Lượt trước bị lỗi hoặc gián đoạn vẫn có thể đã bị tính phí.", "en": "The next step calls the API and may incur charges. A prior failed or interrupted call may already have been charged."},
  "Tôi đồng ý tiếp tục verifier với chi phí API theo cấu hình hiện tại.": {"vi": "Tôi đồng ý tiếp tục verifier với chi phí API theo cấu hình hiện tại.", "en": "I agree to continue the verifier with API costs under the current configuration."},
  "Tiếp tục verifier": {"vi": "Tiếp tục verifier", "en": "Continue verifier"},
  "Đang theo dõi tác vụ khác; kiểm tra lịch sử trước khi tiếp tục.": {"vi": "Đang theo dõi tác vụ khác; kiểm tra lịch sử trước khi tiếp tục.", "en": "Another job is being monitored; inspect history before continuing."},
  "Đã mở tác vụ tiếp tục; không sinh lại giả thuyết.": {"vi": "Đã mở tác vụ tiếp tục; không sinh lại giả thuyết.", "en": "Opened continuation job; hypotheses are reused."},
  "Chưa tiếp tục được; kiểm tra checkpoint, model hoặc hàng đợi.": {"vi": "Chưa tiếp tục được; kiểm tra checkpoint, model hoặc hàng đợi.", "en": "Cannot continue; inspect the checkpoint, model or queue."},
  "verification_input": {"vi": "Đầu vào verifier đã lưu", "en": "Ready for verification"},
  "generation_reused": {"vi": "Tái sử dụng giả thuyết", "en": "Reused hypotheses"},
  "Commit tham chiếu (tùy chọn)": {"vi": "Commit tham chiếu (tùy chọn)", "en": "Reference commit (optional)"},
  "Nhập commit bạn muốn kiểm, chẳng hạn bản sửa ứng viên. Hệ thống chỉ kiểm quan hệ Git, không xác nhận sửa đúng lỗi.": {"vi": "Nhập commit bạn muốn kiểm, chẳng hạn bản sửa ứng viên. Hệ thống chỉ kiểm quan hệ Git, không xác nhận sửa đúng lỗi.", "en": "Enter a commit to check, such as a candidate fix. This checks Git history only; it does not confirm the issue is fixed."},
  "Quan hệ lịch sử Git": {"vi": "Quan hệ lịch sử Git", "en": "Git history relationship"},
  "Nguồn và đích là cùng commit.": {"vi": "Nguồn và đích là cùng commit.", "en": "Source and target are the same commit."},
  "Commit nguồn là tổ tiên của commit đích.": {"vi": "Commit nguồn là tổ tiên của commit đích.", "en": "The source commit is an ancestor of the target."},
  "Commit đích là tổ tiên của commit nguồn.": {"vi": "Commit đích là tổ tiên của commit nguồn.", "en": "The target commit is an ancestor of the source."},
  "Nguồn và đích nằm trên các nhánh lịch sử khác nhau.": {"vi": "Nguồn và đích nằm trên các nhánh lịch sử khác nhau.", "en": "Source and target have diverged histories."},
  "Chưa xác định được quan hệ lịch sử Git.": {"vi": "Chưa xác định được quan hệ lịch sử Git.", "en": "The Git history relationship is unknown."},
  "Đích chứa commit tham chiếu trong lịch sử Git.": {"vi": "Đích chứa commit tham chiếu trong lịch sử Git.", "en": "The target contains the reference commit in Git history."},
  "Đích không chứa commit tham chiếu trong lịch sử Git đã kiểm.": {"vi": "Đích không chứa commit tham chiếu trong lịch sử Git đã kiểm.", "en": "The target does not contain the reference commit in the checked Git history."},
  "Chưa xác định được đích có chứa commit tham chiếu.": {"vi": "Chưa xác định được đích có chứa commit tham chiếu.", "en": "Whether the target contains the reference commit is unknown."},
  "Lý do: {reason}": {"vi": "Lý do: {reason}", "en": "Reason: {reason}"},
  "Lịch sử Git chưa đầy đủ; kết quả thiếu bằng chứng giữ trạng thái chưa biết.": {"vi": "Lịch sử Git chưa đầy đủ; kết quả thiếu bằng chứng giữ trạng thái chưa biết.", "en": "Git history is incomplete; results without enough evidence remain unknown."},
  "Commit tham chiếu": {"vi": "Commit tham chiếu", "en": "Reference commit"},
  "Ref đã nhập: {revision}": {"vi": "Ref đã nhập: {revision}", "en": "Requested ref: {revision}"},
  "Commit đã resolve: {sha}": {"vi": "Commit đã resolve: {sha}", "en": "Resolved commit: {sha}"},
  "Chưa resolve được": {"vi": "Chưa resolve được", "en": "Unresolved"},
  "Tag local chứa commit tham chiếu": {"vi": "Tag local chứa commit tham chiếu", "en": "Local tags containing the reference commit"},
  "Đã quét {scanned}/{total} tag local; giới hạn {limit}.": {"vi": "Đã quét {scanned}/{total} tag local; giới hạn {limit}.", "en": "Scanned {scanned}/{total} local tags; limit {limit}."},
  "Danh sách tag bị giới hạn; các tag chưa quét vẫn chưa biết.": {"vi": "Danh sách tag bị giới hạn; các tag chưa quét vẫn chưa biết.", "en": "The tag list is bounded; unscanned tags remain unknown."},
  "Có tag không kiểm được; xem chi tiết trong JSON xuất ra.": {"vi": "Có tag không kiểm được; xem chi tiết trong JSON xuất ra.", "en": "Some tags could not be checked; inspect the exported JSON for details."},
  "Chưa tìm thấy tag chứa commit trong phạm vi đã kiểm.": {"vi": "Chưa tìm thấy tag chứa commit trong phạm vi đã kiểm.", "en": "No containing tags were found within the checked scope."},
  "Đây là tag trong Git local, chưa xác minh bản phát hành đã công bố hoặc lỗi đã được sửa.": {"vi": "Đây là tag trong Git local, chưa xác minh bản phát hành đã công bố hoặc lỗi đã được sửa.", "en": "These are local Git tags; published releases and whether the issue is fixed remain unverified."},
  "identical_commit": {"vi": "Hai SHA trỏ đến cùng commit", "en": "Both SHAs identify the same commit"},
  "source_reaches_target": {"vi": "Đã xác nhận đường tổ tiên từ nguồn đến đích", "en": "An ancestry path from source to target was confirmed"},
  "target_reaches_source": {"vi": "Đã xác nhận đường tổ tiên từ đích đến nguồn", "en": "An ancestry path from target to source was confirmed"},
  "no_ancestor_path": {"vi": "Không có đường tổ tiên trong lịch sử đã kiểm đầy đủ", "en": "No ancestry path exists in the fully checked history"},
  "reference_equals_target": {"vi": "Commit tham chiếu trùng commit đích", "en": "The reference commit equals the target"},
  "reference_ancestor": {"vi": "Commit tham chiếu là tổ tiên của đích", "en": "The reference commit is an ancestor of the target"},
  "shallow_history": {"vi": "Repository chỉ có lịch sử Git rút gọn", "en": "The repository has shallow Git history"},
  "altered_history": {"vi": "Lịch sử Git có cơ chế thay thế hoặc điều chỉnh", "en": "Git history has replacement or alteration mechanisms"},
  "history_incomplete": {"vi": "Lịch sử Git chưa đầy đủ", "en": "Git history is incomplete"},
  "inspection_timeout": {"vi": "Kiểm tra Git vượt giới hạn thời gian", "en": "Git inspection exceeded its time bound"},
  "Chọn nguồn cho điều tra": {"vi":"Chọn nguồn cho điều tra","en":"Choose investigation sources"},
  "Tìm trong lịch sử repository đã lưu, xem trích đoạn rồi chọn nguồn cho phiên mới ở cùng commit. Không tự đồng bộ hoặc gọi LLM khi tìm.": {"vi":"Tìm trong lịch sử repository đã lưu, xem trích đoạn rồi chọn nguồn cho phiên mới ở cùng commit. Không tự đồng bộ hoặc gọi LLM khi tìm.","en":"Search saved repository history, review excerpts and choose sources for a new run at the same commit. Searching does not sync or call an LLM."},
  "Đây là context GitHub hiện tại, chưa xác minh lịch sử. Nguồn liên quan chưa xác nhận duplicate hoặc bản sửa; không dùng cho benchmark strict.": {"vi":"Đây là context GitHub hiện tại, chưa xác minh lịch sử. Nguồn liên quan chưa xác nhận duplicate hoặc bản sửa; không dùng cho benchmark strict.","en":"This is current GitHub context with unverified history. Related sources do not confirm duplicates or fixes; they are excluded from strict benchmarks."},
  "Từ khóa tìm nguồn liên quan": {"vi":"Từ khóa tìm nguồn liên quan","en":"Keywords for related sources"},
  "Traceback, tên hàm, triệu chứng…": {"vi":"Traceback, tên hàm, triệu chứng…","en":"Traceback, function name, symptom…"},
  "Tìm nguồn local": {"vi":"Tìm nguồn local","en":"Search local sources"},
  "Điều tra với nguồn đã chọn": {"vi":"Điều tra với nguồn đã chọn","en":"Investigate with selected sources"},
  "Đã chọn {count}/6 nguồn": {"vi":"Đã chọn {count}/6 nguồn","en":"Selected {count}/6 sources"},
  "SHA256 báo cáo nguồn: {sha}": {"vi":"SHA256 báo cáo nguồn: {sha}","en":"Source report SHA256: {sha}"},
  "Lịch sử repository chưa quét xong; kết quả chỉ bao phủ dữ liệu đã lưu.": {"vi":"Lịch sử repository chưa quét xong; kết quả chỉ bao phủ dữ liệu đã lưu.","en":"Repository history scanning is incomplete; results cover saved data only."},
  "Có cập nhật repository đang chờ; nguồn có thể đổi trước khi bắt đầu điều tra.": {"vi":"Có cập nhật repository đang chờ; nguồn có thể đổi trước khi bắt đầu điều tra.","en":"Repository updates are pending; sources may change before the investigation starts."},
  "Chưa tìm thấy nguồn phù hợp trong bộ nhớ local. Thử từ khóa khác hoặc đồng bộ repository.": {"vi":"Chưa tìm thấy nguồn phù hợp trong bộ nhớ local. Thử từ khóa khác hoặc đồng bộ repository.","en":"No matching sources found in local memory. Try other keywords or sync the repository."},
  "SHA256 kết quả tìm kiếm: {sha}": {"vi":"SHA256 kết quả tìm kiếm: {sha}","en":"Search result SHA256: {sha}"},
  "Nhập từ khóa để tìm nguồn liên quan.": {"vi":"Nhập từ khóa để tìm nguồn liên quan.","en":"Enter keywords to find related sources."},
  "Đang tìm nguồn trong bộ nhớ local; không gọi LLM.": {"vi":"Đang tìm nguồn trong bộ nhớ local; không gọi LLM.","en":"Searching local memory; no LLM calls."},
  "Xem trích đoạn và hash rồi chọn tối đa 6 nguồn cho phiên điều tra mới.": {"vi":"Xem trích đoạn và hash rồi chọn tối đa 6 nguồn cho phiên điều tra mới.","en":"Review excerpts and hashes, then select up to 6 sources for a new investigation."},
  "Chưa tìm được nguồn cho phiên này. Kiểm tra repository đã đồng bộ và thử lại.": {"vi":"Chưa tìm được nguồn cho phiên này. Kiểm tra repository đã đồng bộ và thử lại.","en":"Sources unavailable for this run. Check that the repository is synced and try again."},
  "Đang mở phiên điều tra với các nguồn đã chọn…": {"vi":"Đang mở phiên điều tra với các nguồn đã chọn…","en":"Opening an investigation with the selected sources…"},
  "Đã mở phiên mới; báo cáo nguồn được giữ nguyên.": {"vi":"Đã mở phiên mới; báo cáo nguồn được giữ nguyên.","en":"Opened a new run; the source report is preserved."},
  "Chưa tạo được phiên. Nguồn có thể đã thay đổi; tìm lại và chọn lại trước khi gửi.": {"vi":"Chưa tạo được phiên. Nguồn có thể đã thay đổi; tìm lại và chọn lại trước khi gửi.","en":"Could not create the run. Sources may have changed; search again and reselect before submitting."},
  "Trả lời câu hỏi gợi ý": {"vi": "Trả lời câu hỏi gợi ý", "en": "Answer suggested questions"},
  "Câu hỏi dựa trên dấu hiệu từ ngữ chưa tìm thấy trong issue, không khẳng định thông tin thực sự còn thiếu. Không dùng LLM để tạo câu hỏi.": {"vi": "Câu hỏi dựa trên dấu hiệu từ ngữ chưa tìm thấy trong issue, không khẳng định thông tin thực sự còn thiếu. Không dùng LLM để tạo câu hỏi.", "en": "Questions reflect wording cues not found in the issue; they do not establish that information is missing. Questions are generated without an LLM."},
  "Trả lời những câu bạn biết; có thể ghi chưa biết hoặc bỏ trống. Câu trả lời là dữ kiện do người dùng cung cấp, chưa xác minh.": {"vi": "Trả lời những câu bạn biết; có thể ghi chưa biết hoặc bỏ trống. Câu trả lời là dữ kiện do người dùng cung cấp, chưa xác minh.", "en": "Answer what you know; you can write unknown or leave questions blank. Answers are unverified user-provided information."},
  "Khi bật, issue, câu trả lời và code excerpts được gửi đến Groq; chi phí theo cấu hình API của bạn. Khi tắt, chỉ truy xuất bằng chứng.": {"vi": "Khi bật, issue, câu trả lời và code excerpts được gửi đến Groq; chi phí theo cấu hình API của bạn. Khi tắt, chỉ truy xuất bằng chứng.", "en": "When enabled, the issue, your answers and code excerpts are sent to Groq, with costs under your API configuration. When disabled, only evidence is retrieved."},
  "Điều tra với câu trả lời": {"vi": "Điều tra với câu trả lời", "en": "Investigate with answers"},
  "Không có câu hỏi từ bộ dấu hiệu hiện tại; độ đầy đủ của issue vẫn chưa được xác minh.": {"vi": "Không có câu hỏi từ bộ dấu hiệu hiện tại; độ đầy đủ của issue vẫn chưa được xác minh.", "en": "No questions arise from the current cues; issue completeness remains unverified."},
  "Bổ sung dữ kiện tự do bên dưới": {"vi": "Bổ sung dữ kiện tự do bên dưới", "en": "Add freeform context below"},
  "Nguồn và hash câu hỏi": {"vi": "Nguồn và hash câu hỏi", "en": "Question sources and hashes"},
  "Đã trả lời {count}/{total} câu": {"vi": "Đã trả lời {count}/{total} câu", "en": "Answered {count}/{total} questions"},
  "SHA256 bộ câu hỏi: {sha}": {"vi": "SHA256 bộ câu hỏi: {sha}", "en": "Question bundle SHA256: {sha}"},
  "Chưa đọc được bộ câu hỏi có nguồn. Mở lại phiên từ lịch sử để kiểm tra.": {"vi": "Chưa đọc được bộ câu hỏi có nguồn. Mở lại phiên từ lịch sử để kiểm tra.", "en": "The source-bound questions are unavailable. Reopen the run from history to check."},
  "Trả lời ít nhất một câu, hoặc dùng phần bổ sung dữ kiện tự do.": {"vi": "Trả lời ít nhất một câu, hoặc dùng phần bổ sung dữ kiện tự do.", "en": "Answer at least one question, or use the freeform context section."},
  "Mỗi câu trả lời tối đa 4.000 ký tự và không chứa ký tự rỗng.": {"vi": "Mỗi câu trả lời tối đa 4.000 ký tự và không chứa ký tự rỗng.", "en": "Each answer is limited to 4,000 characters and must not contain null characters."},
  "Đang tạo phiên điều tra với câu trả lời của bạn…": {"vi": "Đang tạo phiên điều tra với câu trả lời của bạn…", "en": "Creating an investigation with your answers…"},
  "Chưa tạo được phiên. Câu trả lời được giữ lại; mở lại báo cáo nguồn từ lịch sử nếu hash đã đổi, rồi chủ động gửi lại.": {"vi": "Chưa tạo được phiên. Câu trả lời được giữ lại; mở lại báo cáo nguồn từ lịch sử nếu hash đã đổi, rồi chủ động gửi lại.", "en": "Could not create the run. Your answers are preserved; reopen the source report from history if its hash changed, then submit again explicitly."},
  "Phiên mới truy xuất code lại ở cùng commit; các nguồn repository đã chọn ở phiên trước không tự đi theo.": {"vi": "Phiên mới truy xuất code lại ở cùng commit; các nguồn repository đã chọn ở phiên trước không tự đi theo.", "en": "The new run retrieves code again at the same commit; repository sources selected in the previous run are not carried over automatically."},
  "Các dòng bên dưới thuộc bản nội dung người dùng đã loại câu hỏi tự sinh; câu trả lời vẫn chưa xác minh.": {"vi": "Các dòng bên dưới thuộc bản nội dung người dùng đã loại câu hỏi tự sinh; câu trả lời vẫn chưa xác minh.", "en": "The lines below come from user-provided content with generated questions removed; answers remain unverified."},
  "Nội dung dùng để kiểm tra từ khóa": {"vi": "Nội dung dùng để kiểm tra từ khóa", "en": "Content used for wording-cue checks"},
  "Câu trả lời đã lưu (chưa xác minh)": {"vi": "Câu trả lời đã lưu (chưa xác minh)", "en": "Saved answers (unverified)"},
  "Câu hỏi theo giả thuyết và code": {"vi":"Câu hỏi theo giả thuyết và code","en":"Questions grounded in hypotheses and code"},
  "Đặt câu hỏi riêng cho issue để phân biệt các giả thuyết đang có, dựa trên bằng chứng ở đúng snapshot. Model đề xuất câu hỏi, không xác minh nguyên nhân.": {"vi":"Đặt câu hỏi riêng cho issue để phân biệt các giả thuyết đang có, dựa trên bằng chứng ở đúng snapshot. Model đề xuất câu hỏi, không xác minh nguyên nhân.","en":"Ask issue-specific questions to distinguish existing hypotheses using evidence at the saved snapshot. The model proposes questions; it does not verify causes."},
  "Cần ít nhất hai giả thuyết đã lưu để tạo câu hỏi phân biệt. Bạn vẫn có thể trả lời câu hỏi theo mẫu hoặc bổ sung dữ kiện tự do.": {"vi":"Cần ít nhất hai giả thuyết đã lưu để tạo câu hỏi phân biệt. Bạn vẫn có thể trả lời câu hỏi theo mẫu hoặc bổ sung dữ kiện tự do.","en":"At least two saved hypotheses are needed to generate distinguishing questions. You can still answer template questions or add freeform context."},
  "Mở danh sách câu hỏi đã lưu": {"vi":"Mở danh sách câu hỏi đã lưu","en":"Open saved question jobs"},
  "Chỉ khi bạn bấm tạo, issue, các giả thuyết và code excerpts đã lưu mới được gửi tới Groq. Một lượt tạo cho mỗi báo cáo nguồn; chi phí theo cấu hình API, kể cả khi kết quả lỗi.": {"vi":"Chỉ khi bạn bấm tạo, issue, các giả thuyết và code excerpts đã lưu mới được gửi tới Groq. Một lượt tạo cho mỗi báo cáo nguồn; chi phí theo cấu hình API, kể cả khi kết quả lỗi.","en":"Only when you choose to generate are the issue, saved hypotheses and code excerpts sent to Groq. One generation attempt per source report; API costs may apply even if it fails."},
  "Tôi đồng ý gửi nội dung trên tới Groq để tạo câu hỏi và chịu chi phí API.": {"vi":"Tôi đồng ý gửi nội dung trên tới Groq để tạo câu hỏi và chịu chi phí API.","en":"I agree to send the content above to Groq to generate questions with API costs."},
  "Tạo câu hỏi từ giả thuyết": {"vi":"Tạo câu hỏi từ giả thuyết","en":"Generate questions from hypotheses"},
  "Hủy tác vụ câu hỏi": {"vi":"Hủy tác vụ câu hỏi","en":"Cancel question job"},
  "Chưa có bộ câu hỏi riêng cho phiên nguồn này.": {"vi":"Chưa có bộ câu hỏi riêng cho phiên nguồn này.","en":"No question bundle has been saved for this source run."},
  "Mở tác vụ câu hỏi {id}": {"vi":"Mở tác vụ câu hỏi {id}","en":"Open question job {id}"},
  "Tác vụ câu hỏi: {id} · {status}": {"vi":"Tác vụ câu hỏi: {id} · {status}","en":"Question job: {id} · {status}"},
  "Tác vụ chưa tạo được bộ câu hỏi hợp lệ; receipt được giữ lại. Mỗi báo cáo nguồn chỉ có một lượt tạo câu hỏi. Muốn thử lượt mới, cần tạo phiên điều tra nguồn mới.": {"vi":"Tác vụ chưa tạo được bộ câu hỏi hợp lệ; receipt được giữ lại. Mỗi báo cáo nguồn chỉ có một lượt tạo câu hỏi. Muốn thử lượt mới, cần tạo phiên điều tra nguồn mới.","en":"This job did not produce a valid question bundle; receipts are retained. Each source report has one question-generation attempt. A new attempt requires a new source investigation."},
  "Vì sao cần hỏi: ": {"vi":"Vì sao cần hỏi: ","en":"Why ask: "},
  "Câu trả lời giúp phân biệt: ": {"vi":"Câu trả lời giúp phân biệt: ","en":"How answers distinguish hypotheses: "},
  "Giả thuyết và bằng chứng liên quan": {"vi":"Giả thuyết và bằng chứng liên quan","en":"Related hypotheses and evidence"},
  "Câu hỏi do model đề xuất, chưa được kiểm người. Trả lời những gì bạn biết hoặc ghi chưa biết.": {"vi":"Câu hỏi do model đề xuất, chưa được kiểm người. Trả lời những gì bạn biết hoặc ghi chưa biết.","en":"These model-proposed questions have not been reviewed by a human. Answer what you know or write unknown."},
  "Chưa đọc được tác vụ hoặc bộ câu hỏi có nguồn. Mở tác vụ đã lưu để kiểm tra; không tự gọi lại model.": {"vi":"Chưa đọc được tác vụ hoặc bộ câu hỏi có nguồn. Mở tác vụ đã lưu để kiểm tra; không tự gọi lại model.","en":"Could not read the job or its source-bound questions. Open the saved job to check; the model will not be called again automatically."},
  "Chưa đọc được danh sách câu hỏi đã lưu. Không có lời gọi model mới.": {"vi":"Chưa đọc được danh sách câu hỏi đã lưu. Không có lời gọi model mới.","en":"Could not read saved question jobs. No new model call was made."},
  "Đang tạo câu hỏi từ issue, giả thuyết và bằng chứng đã lưu…": {"vi":"Đang tạo câu hỏi từ issue, giả thuyết và bằng chứng đã lưu…","en":"Generating questions from the saved issue, hypotheses and evidence…"},
  "Chưa xác nhận được tác vụ câu hỏi. Mở danh sách đã lưu và kiểm tra receipt; gửi lại cùng nguồn chỉ nhận lại tác vụ cũ, không tạo lượt gọi mới.": {"vi":"Chưa xác nhận được tác vụ câu hỏi. Mở danh sách đã lưu và kiểm tra receipt; gửi lại cùng nguồn chỉ nhận lại tác vụ cũ, không tạo lượt gọi mới.","en":"Could not confirm the question job. Open saved jobs and check receipts; resubmitting the same source returns its existing job without a new model call."},
  "Đã yêu cầu hủy; lời gọi đang gửi có thể vẫn tính phí.": {"vi":"Đã yêu cầu hủy; lời gọi đang gửi có thể vẫn tính phí.","en":"Cancellation requested; an in-flight call may still incur costs."},
  "Chưa gửi được yêu cầu hủy; mở lại tác vụ để kiểm tra trạng thái.": {"vi":"Chưa gửi được yêu cầu hủy; mở lại tác vụ để kiểm tra trạng thái.","en":"Could not request cancellation; reopen the job to check its status."},
  "hypothesis_check": {"vi":"Kiểm tra giả thuyết","en":"Hypothesis check"},
  "Xuất bộ câu hỏi JSON": {"vi":"Xuất bộ câu hỏi JSON","en":"Export question bundle JSON"},
  "Xem bằng chứng {id}": {"vi": "Xem bằng chứng {id}", "en": "View evidence {id}"},
  "Code tại commit này": {"vi": "Code tại commit này", "en": "Code at this commit"},
  "Nguồn model dùng để hỗ trợ": {"vi": "Nguồn model dùng để hỗ trợ", "en": "Sources the model cites as support"},
  "Nguồn model dùng để phản bác": {"vi": "Nguồn model dùng để phản bác", "en": "Sources the model cites as counter-evidence"},
  "Chưa có nguồn phản bác trong kết quả này; không chứng minh giả thuyết đúng.": {"vi": "Chưa có nguồn phản bác trong kết quả này; không chứng minh giả thuyết đúng.", "en": "No counter-evidence is recorded here; this does not prove the hypothesis."},
  "Citation, nhận xét model và kiểm người": {"vi": "Citation, nhận xét model và kiểm người", "en": "Citation checks, model assessment and human review"},
  "Tính toàn vẹn citation: ": {"vi": "Tính toàn vẹn citation: ", "en": "Citation integrity: "},
  "Ý nghĩa bằng chứng: ": {"vi": "Ý nghĩa bằng chứng: ", "en": "Semantic support: "},
  "Nhận xét model: ": {"vi": "Nhận xét model: ", "en": "Model assessment: "},
  "Chưa có nhận xét model": {"vi": "Chưa có nhận xét model", "en": "No model assessment recorded"},
  "Chưa có kiểm người độc lập cho kết luận này.": {"vi": "Chưa có kiểm người độc lập cho kết luận này.", "en": "No independent human review of this conclusion."},
  "Chưa đọc được báo cáo tổng quan của phiên này. Các tab bằng chứng và bản JSON pack gốc vẫn dùng được.": {"vi": "Chưa đọc được báo cáo tổng quan của phiên này. Các tab bằng chứng và bản JSON pack gốc vẫn dùng được.", "en": "The overview report could not be read for this run. Evidence tabs and the original pack JSON remain available."},
  "Lượt phân tích đã hoàn tất": {"vi": "Lượt phân tích đã hoàn tất", "en": "Analysis run completed"},
  "Đã thu thập bằng chứng · chưa có giả thuyết": {"vi": "Đã thu thập bằng chứng · chưa có giả thuyết", "en": "Evidence collected · no hypotheses yet"},
  "Cần xác minh thêm": {"vi": "Cần xác minh thêm", "en": "Further verification needed"},
  "Commit đã phân tích: ": {"vi": "Commit đã phân tích: ", "en": "Analyzed commit: "},
  "Issue mô tả điều gì?": {"vi": "Issue mô tả điều gì?", "en": "What does the issue report?"},
  "Trích nội dung người báo issue; chưa xác minh hành vi được mô tả.": {"vi": "Trích nội dung người báo issue; chưa xác minh hành vi được mô tả.", "en": "Excerpt from the issue reporter; the described behavior is unverified."},
  "Trích đoạn đã rút gọn. Mở bằng chứng issue để xem nguồn đã lưu.": {"vi": "Trích đoạn đã rút gọn. Mở bằng chứng issue để xem nguồn đã lưu.", "en": "This excerpt is shortened. Open the issue evidence to inspect the saved source."},
  "Giả thuyết cần kiểm tra": {"vi": "Giả thuyết cần kiểm tra", "en": "Hypotheses to investigate"},
  "Thứ tự theo báo cáo nguồn, không phải xếp hạng độ tin cậy.": {"vi": "Thứ tự theo báo cáo nguồn, không phải xếp hạng độ tin cậy.", "en": "Source report order; this is not a confidence ranking."},
  "Phiên này chỉ truy xuất bằng chứng; chưa sinh giả thuyết.": {"vi": "Phiên này chỉ truy xuất bằng chứng; chưa sinh giả thuyết.", "en": "This run retrieved evidence only; it did not generate hypotheses."},
  "Bước tiếp theo": {"vi": "Bước tiếp theo", "en": "Next checks"},
  "Các bước được đề xuất; IssueIntel chưa chạy code hoặc test để xác nhận kết quả.": {"vi": "Các bước được đề xuất; IssueIntel chưa chạy code hoặc test để xác nhận kết quả.", "en": "Proposed checks; IssueIntel has not run code or tests to confirm their outcome."},
  "Liên quan giả thuyết: ": {"vi": "Liên quan giả thuyết: ", "en": "Related hypotheses: "},
  "Chưa có bước kiểm tra trong báo cáo nguồn.": {"vi": "Chưa có bước kiểm tra trong báo cáo nguồn.", "en": "No next checks are recorded in the source report."},
  "Vị trí code nên xem": {"vi": "Vị trí code nên xem", "en": "Code locations to inspect"},
  "Nguồn được truy xuất, chưa xác nhận là vị trí gây lỗi hoặc cần sửa.": {"vi": "Nguồn được truy xuất, chưa xác nhận là vị trí gây lỗi hoặc cần sửa.", "en": "Retrieved locations; not confirmed as faulty or in need of a change."},
  "Chưa có vị trí code trong phạm vi bằng chứng này.": {"vi": "Chưa có vị trí code trong phạm vi bằng chứng này.", "en": "No code locations are present in this evidence scope."},
  "Điều chưa biết và giới hạn": {"vi": "Điều chưa biết và giới hạn", "en": "Unknowns and limitations"},
  "Nguồn của báo cáo tổng quan": {"vi": "Nguồn của báo cáo tổng quan", "en": "Overview report provenance"},
  "Báo cáo được dựng từ pack đã lưu, không gọi lại model.": {"vi": "Báo cáo được dựng từ pack đã lưu, không gọi lại model.", "en": "This report is derived from the saved pack without another model call."},
  "Báo cáo Markdown ↓": {"vi": "Báo cáo Markdown ↓", "en": "Report Markdown ↓"},
  "Báo cáo JSON ↓": {"vi": "Báo cáo JSON ↓", "en": "Report JSON ↓"},
  "Pack gốc JSON ↓": {"vi": "Pack gốc JSON ↓", "en": "Original pack JSON ↓"},
  "Tổng quan": {"vi": "Tổng quan", "en": "Overview"},
  "Xem thêm {count} giả thuyết": {"vi": "Xem thêm {count} giả thuyết", "en": "Show {count} more hypotheses"},
  "Xem thêm {count} bước kiểm tra": {"vi": "Xem thêm {count} bước kiểm tra", "en": "Show {count} more checks"},
  "Xem thêm {count} vị trí": {"vi": "Xem thêm {count} vị trí", "en": "Show {count} more locations"},
  "Nguyên nhân chưa được xác minh.": {"vi": "Nguyên nhân chưa được xác minh.", "en": "The cause has not been verified."},
  "Chưa đánh giá mức độ ảnh hưởng.": {"vi": "Chưa đánh giá mức độ ảnh hưởng.", "en": "Impact severity has not been assessed."},
  "Chưa có xác suất tin cậy đã hiệu chỉnh.": {"vi": "Chưa có xác suất tin cậy đã hiệu chỉnh.", "en": "No calibrated confidence probability is available."},
  "IssueIntel chưa chạy các bước kiểm tra được đề xuất.": {"vi": "IssueIntel chưa chạy các bước kiểm tra được đề xuất.", "en": "IssueIntel has not executed the proposed checks."},
  "Chưa tìm thấy phản chứng trong phạm vi đã tra.": {"vi": "Chưa tìm thấy phản chứng trong phạm vi đã tra.", "en": "No counter-evidence found within the searched scope."},
  "Báo cáo nguồn giữ kết luận ở trạng thái chưa đủ căn cứ.": {"vi": "Báo cáo nguồn giữ kết luận ở trạng thái chưa đủ căn cứ.", "en": "The source report abstains from a definitive conclusion."},
  "valid": {"vi": "Khớp nguồn", "en": "Source matched"},
  "unverified": {"vi": "Chưa xác minh", "en": "Unverified"},
  "Giới hạn kết luận do model nêu": {"vi": "Giới hạn kết luận do model nêu", "en": "Model-reported limits on the conclusion"},
  "Lý do kết thúc: ": {"vi": "Lý do kết thúc: ", "en": "Stop reason: "},
  "Tóm tắt là trích nguyên văn issue, chưa phải quan sát đã được xác minh độc lập.": {"vi": "Tóm tắt là trích nguyên văn issue, chưa phải quan sát đã được xác minh độc lập.", "en": "The summary is an exact issue excerpt, not an independently verified observation."},
  "Vị trí ứng viên là code được truy xuất, chưa xác nhận là file bị ảnh hưởng.": {"vi": "Vị trí ứng viên là code được truy xuất, chưa xác nhận là file bị ảnh hưởng.", "en": "Candidate locations are retrieved code, not confirmed affected files."},
  "Citation khớp nguồn và nhận xét model chưa chứng minh nguyên nhân.": {"vi": "Citation khớp nguồn và nhận xét model chưa chứng minh nguyên nhân.", "en": "Citation integrity and model critique do not establish causal truth."},
  "Chưa có xác nhận kiểm người độc lập.": {"vi": "Chưa có xác nhận kiểm người độc lập.", "en": "No independent human validation is claimed."},
  "IssueIntel chưa chạy các bước kiểm tra được đề xuất; trạng thái checklist do người dùng tự báo cáo được lưu riêng.": {"vi": "IssueIntel chưa chạy các bước kiểm tra được đề xuất; trạng thái checklist do người dùng tự báo cáo được lưu riêng.", "en": "Suggested checks were not executed by IssueIntel; user checklist states are separate."},
  "Mức độ ảnh hưởng chưa được đánh giá; xác suất chưa hiệu chỉnh được giữ ở null.": {"vi": "Mức độ ảnh hưởng chưa được đánh giá; xác suất chưa hiệu chỉnh được giữ ở null.", "en": "Unknown severity and uncalibrated probability remain unassessed/null."},
  "Chưa biết": {"vi":"Chưa biết","en":"Unknown"},
  "Kiểm tra bản phát hành GitHub": {"vi":"Kiểm tra bản phát hành GitHub","en":"Check a GitHub release"},
  "Chọn đúng một tag và commit tham chiếu. Khi bấm kiểm tra, hệ thống đọc GitHub API; không gọi model hoặc chạy code/test.": {"vi":"Chọn đúng một tag và commit tham chiếu. Khi bấm kiểm tra, hệ thống đọc GitHub API; không gọi model hoặc chạy code/test.","en":"Choose one tag and a reference commit. Checking reads the GitHub API; it does not call a model or run code/tests."},
  "Đây là quan sát hiện tại. Chứa commit chưa chứng minh lỗi đã sửa; tag hiện tại chưa chứng minh nội dung tại ngày công bố.": {"vi":"Đây là quan sát hiện tại. Chứa commit chưa chứng minh lỗi đã sửa; tag hiện tại chưa chứng minh nội dung tại ngày công bố.","en":"This is a current observation. Commit containment does not establish a fix; the current tag does not establish its contents on the publication date."},
  "Tag phát hành cần kiểm": {"vi":"Tag phát hành cần kiểm","en":"Release tag to check"},
  "Commit tham chiếu đầy đủ": {"vi":"Commit tham chiếu đầy đủ","en":"Full reference commit"},
  "Đọc bản phát hành từ GitHub": {"vi":"Đọc bản phát hành từ GitHub","en":"Read release from GitHub"},
  "Hủy kiểm tra phát hành": {"vi":"Hủy kiểm tra phát hành","en":"Cancel release check"},
  "Quan sát phát hành đã lưu": {"vi":"Quan sát phát hành đã lưu","en":"Saved release observations"},
  "Quan sát bản phát hành đã lưu": {"vi":"Quan sát bản phát hành đã lưu","en":"Saved release observation"},
  "Tag đã yêu cầu: {tag}": {"vi":"Tag đã yêu cầu: {tag}","en":"Requested tag: {tag}"},
  "Commit tham chiếu đã kiểm: {sha}": {"vi":"Commit tham chiếu đã kiểm: {sha}","en":"Checked reference commit: {sha}"},
  "Thời điểm quan sát: {time}": {"vi":"Thời điểm quan sát: {time}","en":"Observed at: {time}"},
  "Đã công bố trên GitHub tại lúc quan sát.": {"vi":"Đã công bố trên GitHub tại lúc quan sát.","en":"Published on GitHub at the time of observation."},
  "Metadata chưa cho thấy bản phát hành đã công bố.": {"vi":"Metadata chưa cho thấy bản phát hành đã công bố.","en":"Metadata does not establish a published release."},
  "Bản nháp · chưa công bố": {"vi":"Bản nháp · chưa công bố","en":"Draft · unpublished"},
  "Bản phát hành thử (prerelease)": {"vi":"Bản phát hành thử (prerelease)","en":"Prerelease"},
  "Ngày công bố theo GitHub: {time}": {"vi":"Ngày công bố theo GitHub: {time}","en":"GitHub publication date: {time}"},
  "Mở bản phát hành trên GitHub": {"vi":"Mở bản phát hành trên GitHub","en":"Open release on GitHub"},
  "Chưa có metadata bản phát hành đủ để kết luận việc công bố.": {"vi":"Chưa có metadata bản phát hành đủ để kết luận việc công bố.","en":"Release metadata is insufficient to determine publication."},
  "Commit tag hiện tại: {sha}": {"vi":"Commit tag hiện tại: {sha}","en":"Current tag commit: {sha}"},
  "Bản phát hành đang công bố chứa commit tham chiếu trong lịch sử Git hiện được quan sát.": {"vi":"Bản phát hành đang công bố chứa commit tham chiếu trong lịch sử Git hiện được quan sát.","en":"The currently published release contains the reference commit in the observed Git history."},
  "Tag của bản phát hành hiện không chứa commit tham chiếu trong lịch sử đã kiểm.": {"vi":"Tag của bản phát hành hiện không chứa commit tham chiếu trong lịch sử đã kiểm.","en":"The release tag currently does not contain the reference commit in the checked history."},
  "Chưa đủ bằng chứng để kết luận bản phát hành đã công bố chứa commit tham chiếu.": {"vi":"Chưa đủ bằng chứng để kết luận bản phát hành đã công bố chứa commit tham chiếu.","en":"Evidence is insufficient to conclude that a published release contains the reference commit."},
  "Quan hệ commit không chứng minh lỗi đã sửa. Tag hiện tại không chứng minh nội dung tag tại ngày công bố.": {"vi":"Quan hệ commit không chứng minh lỗi đã sửa. Tag hiện tại không chứng minh nội dung tag tại ngày công bố.","en":"Commit ancestry does not establish a fix. The current tag does not establish its contents at publication."},
  "Nguồn và giới hạn quan sát": {"vi":"Nguồn và giới hạn quan sát","en":"Observation sources and limits"},
  "Commit nguồn đã lưu: {sha}": {"vi":"Commit nguồn đã lưu: {sha}","en":"Saved source commit: {sha}"},
  "Không gọi model, chạy test hoặc đánh giá benchmark trong lượt kiểm này.": {"vi":"Không gọi model, chạy test hoặc đánh giá benchmark trong lượt kiểm này.","en":"This check made no model calls, ran no tests and performed no benchmark evaluation."},
  "Xuất quan sát phát hành JSON": {"vi":"Xuất quan sát phát hành JSON","en":"Export release observation JSON"},
  "Chưa có quan sát phát hành đã lưu cho phiên này.": {"vi":"Chưa có quan sát phát hành đã lưu cho phiên này.","en":"No release observations are saved for this run."},
  "Mở quan sát phát hành: {id} · {status}": {"vi":"Mở quan sát phát hành: {id} · {status}","en":"Open release observation: {id} · {status}"},
  "Đang đọc quan sát đã lưu; không gọi GitHub hoặc model.": {"vi":"Đang đọc quan sát đã lưu; không gọi GitHub hoặc model.","en":"Reading a saved observation; no GitHub or model call."},
  "Không đọc được quan sát phát hành; kiểm tra nguồn và hash.": {"vi":"Không đọc được quan sát phát hành; kiểm tra nguồn và hash.","en":"Could not read the release observation; check its source and hash."},
  "Nhập một tag hợp lệ và commit đầy đủ 40 hoặc 64 ký tự hex viết thường.": {"vi":"Nhập một tag hợp lệ và commit đầy đủ 40 hoặc 64 ký tự hex viết thường.","en":"Enter a valid tag and a full commit with 40 or 64 lowercase hexadecimal characters."},
  "Đang đọc GitHub API cho tag đã chọn; không gọi model.": {"vi":"Đang đọc GitHub API cho tag đã chọn; không gọi model.","en":"Reading the GitHub API for the selected tag; no model call."},
  "Đã lưu quan sát phát hành; tình trạng bản sửa vẫn chưa xác minh.": {"vi":"Đã lưu quan sát phát hành; tình trạng bản sửa vẫn chưa xác minh.","en":"Release observation saved; fix status remains unverified."},
  "Kiểm tra phát hành chưa hoàn tất; dữ liệu nguồn và receipt vẫn được giữ.": {"vi":"Kiểm tra phát hành chưa hoàn tất; dữ liệu nguồn và receipt vẫn được giữ.","en":"The release check did not complete; source data and receipts are preserved."},
  "Chưa gửi được yêu cầu hủy; tác vụ có thể vẫn đang chạy.": {"vi":"Chưa gửi được yêu cầu hủy; tác vụ có thể vẫn đang chạy.","en":"Could not send cancellation; the job may still be running."},
  "Một lượt kiểm của phiên trước vẫn đang hoàn tất; chờ trước khi tạo lượt mới.": {"vi":"Một lượt kiểm của phiên trước vẫn đang hoàn tất; chờ trước khi tạo lượt mới.","en":"A check for the previous source is still finishing; wait before starting another."},
  "release_unavailable": {"vi":"Chưa truy cập được metadata bản phát hành.","en":"Release metadata is unavailable."},
  "release_not_published": {"vi":"Metadata chưa xác nhận bản phát hành đã công bố.","en":"Metadata does not establish a published release."},
  "tag_ref_unavailable": {"vi":"Chưa truy cập được ref của tag.","en":"The tag ref is unavailable."},
  "tag_cycle": {"vi":"Chuỗi object của tag có vòng lặp.","en":"The tag object chain contains a cycle."},
  "tag_depth_limit": {"vi":"Chuỗi object của tag vượt giới hạn đọc.","en":"The tag object chain exceeds the read limit."},
  "tag_object_unavailable": {"vi":"Chưa truy cập được object của tag.","en":"The tag object is unavailable."},
  "tag_target_not_commit": {"vi":"Đích của tag chưa phải commit.","en":"The tag target is not a commit."},
  "commit_unavailable": {"vi":"Chưa truy cập được commit.","en":"The commit is unavailable."},
  "reference_commit_unavailable": {"vi":"Chưa truy cập được commit tham chiếu.","en":"The reference commit is unavailable."},
  "release_commit_unavailable": {"vi":"Chưa truy cập được commit của tag phát hành.","en":"The release tag commit is unavailable."},
  "comparison_unavailable": {"vi":"Chưa có kết quả đối chiếu Git đủ nguồn.","en":"A source-bound Git comparison is unavailable."},
  "reference_is_ancestor_or_identical": {"vi":"Commit tham chiếu trùng hoặc là tổ tiên của commit tag.","en":"The reference commit is identical to or an ancestor of the tag commit."},
  "reference_not_ancestor": {"vi":"Commit tham chiếu không là tổ tiên của commit tag theo đối chiếu đã kiểm.","en":"The checked comparison shows that the reference commit is not an ancestor of the tag commit."},
  "Current GitHub REST observations are not proof of tag contents at publication time.": {"vi":"Quan sát GitHub REST hiện tại chưa chứng minh nội dung tag tại thời điểm công bố.","en":"Current GitHub REST observations are not proof of tag contents at publication time."},
  "Repository, release and tag are checked before and after; this is not an atomic snapshot and transient moves may be unobserved.": {"vi":"Repository, bản phát hành và tag được kiểm trước/sau; đây không phải snapshot nguyên tử và có thể bỏ lỡ thay đổi thoáng qua.","en":"Repository, release and tag are checked before and after; this is not an atomic snapshot and transient moves may be unobserved."},
  "Containment describes commit ancestry only; it does not establish that a commit fixes the issue or that tests passed.": {"vi":"Chứa commit chỉ mô tả quan hệ tổ tiên Git; chưa chứng minh commit sửa issue hoặc test đã pass.","en":"Containment describes commit ancestry only; it does not establish that a commit fixes the issue or that tests passed."},
  "Only the explicitly selected tag is inspected; no release enumeration, compare pagination or Git history acquisition is performed.": {"vi":"Chỉ kiểm tag đã chọn; không liệt kê toàn bộ bản phát hành, đọc các trang đối chiếu tiếp theo hoặc tải lịch sử Git.","en":"Only the explicitly selected tag is inspected; no release enumeration, compare pagination or Git history acquisition is performed."},
  "Receipt JSON has a replayable canonical hash; recorded transport-byte hashes cannot be recomputed from parsed JSON and do not authenticate the server.": {"vi":"Receipt JSON có hash chuẩn để replay; hash bytes truyền tải không thể tính lại từ JSON đã parse và không xác thực máy chủ.","en":"Receipt JSON has a replayable canonical hash; recorded transport-byte hashes cannot be recomputed from parsed JSON and do not authenticate the server."},
  "Missing or inaccessible resources remain unknown; absence does not prove that a release never existed.": {"vi":"Nguồn thiếu hoặc không truy cập được giữ ở trạng thái chưa biết; thiếu nguồn chưa chứng minh bản phát hành chưa từng tồn tại.","en":"Missing or inaccessible resources remain unknown; absence does not prove that a release never existed."},
  "No model, target code, tests, benchmark split or evaluation is executed.": {"vi":"Không gọi model, chạy code/test đích, mở split benchmark hoặc thực hiện evaluation.","en":"No model, target code, tests, benchmark split or evaluation is executed."}
};
