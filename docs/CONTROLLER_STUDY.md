# Chuẩn bị so sánh controller — V03

So sánh rules/Jev cần cùng Planned recipe, model sinh, snapshot, công cụ và
ngân sách gồm cả controller. Web hiện dùng rules v3 và Jev v2; không lấy hai
default này làm hai treatment. Các primitive dưới đây chuẩn bị một experiment
riêng, chưa phải kết quả Jev tốt hơn rules hoặc quyền gọi thêm Typesafe.

## Stage boundary cho product

`investigation.product_study` tạo `product_controller_study_policy_v1` bằng
`make_policy()`; `pipeline.investigate()` nhận cặp tham số tùy chọn
`product_study_policy`/`product_study_stage_executor`.

Chỉ nhận case GitHub issue public canonical, revision chính xác và snapshot
`benchmark_base`, không benchmark split/instance, prepared evidence, historical
proof, recovery hoặc continuation. Recipe cố định Planned v2, rules hoặc native
Typesafe Planned controller, Groq 120b low/4.096, top-k 12, graph expansion và
independent critic. Policy khóa full case/snapshot/graph/parser, native source
hashes, recipe và digest runtime/source do caller cung cấp. Nó kiểm tính nhất
quán, không tự chứng minh nguồn public đã được tải đúng hoặc cấu hình opaque
provider thực sự khớp digest khai báo.

Executor nhận stage name, exact binding và native `produce` callback, phải trả
full native payload. Retrieval được replay deterministic; generation vẫn qua
native Annotation/provenance/evidence validation; verifier kiểm structural
projection và strict critic schema. Guard trước/sau mỗi stage, model call kể cả
output repair, và decision boundary phát hiện thay đổi source/context.

Cache ở runner chỉ được dùng khi **toàn bộ** operation input/recipe khớp. Giữ
nguyên annotation timestamp và repair count: native planner tính logical calls
từ `1 + repair_count`, nên shared seed không được miễn lượt logical. Payload
của call HTTP riêng lẻ chưa phải một native stage đã hoàn tất. Cần giữ stage
owner, mọi call/receipt liên quan và reference của từng arm ngoài pack gốc.

Lối này tách khỏi strict Main Dev experiment gate và product recovery tapes.
Không thay defaults của Workbench/CLI, không mở đường đưa benchmark case sang
product, không bỏ kiểm của V01. Không cấu hình key hoặc khởi động API chỉ bằng
cách tạo policy.

## Ngân sách chung trước provider

`runtime.study_budget.StudyBudgetLedger` giữ policy finite theo study/provider/
model/config hash/price hash/ngày, cận attempt và USD của từng arm cùng tổng
paid owner. Caller phải xác minh giá có ngày và tính đúng cận native trước
`reserve()`; lớp này không tự lấy giá, tải cấu hình hoặc cấp quyền chi tiêu.

- `reserve(...)`: lưu intent và cận chi phí trước construction/HTTP. Request đã
  có owner không được reserve lại để retry; schema repair có request và ordinal
  riêng nhưng vẫn tiêu attempt.
- `publish_receipt(...)`: kiểm native Groq/Typesafe contract, model, bound và
  reservation ID duy nhất. Terminal receipt không bị viết lại. Breach được giữ
  và khóa call tiếp theo; thiếu receipt vẫn giữ nguyên conservative bound.
- `finish(...)`/`close_unknown(...)`: giữ outcome riêng với receipt. Chỉ
  completed có returned-compatible receipt và payload SHA; không suy thành công
  từ raw response, chi phí settled hoặc status của provider.
- `reference(...)`: phân bổ chi phí của call terminal sang arm khác, không gọi
  API và không cho phép dùng lại payload. Shared successful stage phải mang cả
  call lỗi trước output repair; không miễn chi phí đó cho arm thứ hai.
- `inspect()`: tổng paid owner dedup theo call và logical arm references riêng;
  không cộng ngang logical costs thành chi tiêu thực.

Groq receipt settled hợp lệ có thể không lưu observed input/output khi native
call không có context row. Giữ charged total và directional observations
unknown; không tự chia token. Native provider/account limits vẫn là cổng bổ sung.
Lỗi ledger là `StudyBudgetStop(OSError)`, để caller truyền ra ngoài fallback
controller. Provider wrapper còn phải bảo đảm lỗi callback bị native client bọc
lại không biến thành quyền gọi tiếp sau integrity stop.

Event files fsync và SQLite `BEGIN IMMEDIATE` kiểm cap atomically qua hai
provider, giữ exact inventory/hash chain/index. Crash sau fsynced event được
replay vào index, không tạo lại paid call. Đây là kiểm tính nhất quán local,
không chữ ký tác giả, hóa đơn hoặc bảo vệ trước coordinated rollback của cả
journal và index. Constructor/inspect có thể mở transaction để replay index;
không phải API chỉ đọc filesystem tuyệt đối.

## Cổng còn lại

V03 còn cần registration và selection từ captured product inputs, runner với
owned provider wrappers/shared-stage journal, pilot gate, terminal seal và
scorer chung. Trước call thật phải khóa exact model/config/prices, nguồn,
protocol, metric, cận toàn bộ và phase authority. Cận USD chung không thay quyền
Typesafe riêng; bốn smoke calls cũ đã hết quyền. Không sửa `.env` hoặc bật
Typesafe product defaults để ngầm chạy experiment.

Synthetic tests kiểm primitive boundaries và native compatibility, không đo
action accuracy, usefulness hay hiệu quả Jev. V01 inference giữ checkout đã
khóa riêng; Holdout/Live vẫn đóng.

Kiểm tra mốc này: 311 test shared pipeline/Planned/Typesafe/V01 ablation đạt.
Sau lần siết kiểu ledger cuối, 141 test của hai study boundaries đạt, gồm
actual native receipt, cross-provider cap race, crash/index replay, failed
repair cost sharing, unchanged default pack và source mutation trước repair.
Hai phạm vi có overlap; không cộng chúng thành số test độc lập. Review code
độc lập không phát hiện blocker; không có Typesafe call hoặc comparison run mới.
