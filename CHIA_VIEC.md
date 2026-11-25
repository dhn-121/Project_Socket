# Phân công kiểm tra & hoàn thiện — nhóm Bích · Hà · Châu

Code đã chạy được và có test tự động. Việc còn lại của nhóm là **hiểu code, kiểm tra trên máy thật, làm các
phần AI không làm thay được (ảnh Wireshark, chạy trên 2 máy thật, chỉnh báo cáo) và chuẩn bị trả lời khi demo**.
Mỗi người nhận khoảng 1/3 điểm của thang chấm, và phải giải thích được phần của mình trước thầy/cô.

| | Bích (nhóm trưởng) | Hà | Châu |
|---|---|---|---|
| **Module** | `protocol.py`, `node.py` (phần chat, kết nối, lỗi) | `discovery.py`, `ui.py` | `node.py` (phần file), `tests/`, README |
| **Điểm rubric phụ trách** | TCP chat 20 + Giao thức/framing 10 + Lỗi/ổn định 10 = **40** | UDP discovery/status 15 + Console UI 15 = **30** | Truyền file 15 + Báo cáo 10 + Demo/chất lượng code 5 = **30** |
| **Ảnh Wireshark** | TCP three-way handshake | UDP broadcast | (hỗ trợ ghép vào báo cáo) |
| **Phần báo cáo** | mục 4 Giao thức, mục 6 Lỗi | mục 3 Mô hình thread, mục 5.1/5.4 | mục 1, 2, 7, 8, 10 + ghép, định dạng, nộp |

## 0. Làm chung trước khi chia (15 phút, cả nhóm)

- [ ] Cài Python 3.8+ trên máy của mỗi người, giải nén project, chạy `python lanchat.py --name <tên>` thử một lần.
- [ ] **Windows Firewall**: khi hiện hộp thoại, chọn *Allow* cho cả mạng Private và Public. Không làm bước này thì sẽ không thấy nhau.
- [ ] Cả 3 máy cùng một Wi-Fi/LAN (tránh Wi-Fi trường có *client isolation*; dùng điện thoại phát Wi-Fi cho chắc).
- [ ] Chạy `python tests/run_tests.py` trên **một** máy → phải ra `47/47 checks passed`.
      Thất bại thì gửi nội dung lỗi cho người viết code, đừng sửa mò.

> **Chưa được kiểm chứng:** code đã được test trên Linux (3 tiến trình, terminal ảo). **Chưa chạy trên Windows** và
> **chưa chạy trên 2 máy thật**. Đây là việc đầu tiên cần làm; nếu có lỗi riêng của Windows (đặc biệt phần bàn phím
> trong `ui.py`, nhánh `msvcrt`) hãy báo lại để sửa.

---

## 1. Bích — Giao thức, TCP chat, xử lý lỗi

**Đọc hiểu** (theo thứ tự):
1. `protocol.py`: docstring đầu file (toàn bộ định dạng gói), `FrameReader` (`read_until`, `read_exact`, `read_chunks`), `read_header`.
2. `node.py`: `_accept_loop` → `_serve` → `_register` → `_run_chat` → `_drop`; `open_chat`, `send_message`, `leave`, `_on_message`.

**Kiểm tra trên máy thật:**
- [ ] A `/chat B`, gửi tin hai chiều; tin tiếng Việt có dấu hiển thị đúng.
- [ ] C nhắn B khi B đang chat với A → B thấy `New message from C (n unread)`; B `/chat C` thấy tin cũ.
- [ ] `/leave` → bên kia báo `closed the chat`. Rút dây mạng/tắt Wi-Fi một máy → máy kia báo `Connection ... lost`
      (có thể mất khoảng 25–60 giây nếu không có gói FIN, do TCP keep-alive).
- [ ] `/chat` tới người vừa tắt → báo lỗi, không crash. Gõ lệnh sai → hiện help ngắn.
- [ ] Chụp Wireshark: filter `tcp.port == <cổng B>`, A `/chat B` → 3 gói SYN / SYN,ACK / ACK rồi `HI|...`, `MSG|...`.

**Câu hỏi thầy/cô có thể hỏi:**
- *TCP không có ranh giới tin nhắn, làm sao tách?* → Mỗi frame có header `MSG|<length>|`; `FrameReader` lặp `recv()` đến khi đủ đúng `length` byte, giữ dữ liệu dư cho frame sau. Test `test_framing` gửi từng byte một và gộp 3 frame vào một lần gửi.
- *Hai người cùng `/chat` nhau một lúc thì sao?* → Có thể có 2 kết nối; `_register` giữ kết nối do bên có tên nhỏ hơn mở, cả hai đầu cùng quy tắc nên nhất quán.
- *Tại sao mỗi peer một thread?* → `recv()` chặn; một thread/peer để nhận bất kỳ lúc nào mà không làm kẹt bàn phím.
- *Vì sao có `HI`?* → Kết nối TCP đến từ cổng ngẫu nhiên, cần biết tên và cổng lắng nghe của người gọi, và đây là kết nối chat hay file.
- *Dữ liệu dùng chung bảo vệ thế nào?* → `Node.lock` (RLock) cho `conns`, `current`, `unread`, `history`; `Conn.send_lock` để hai thread không gửi xen kẽ byte.

## 2. Hà — UDP discovery, trạng thái, giao diện console

**Đọc hiểu:**
1. `discovery.py`: `Discovery.start`, `_broadcast_loop`, `_listen_loop`, `_handle`, `_expire`, `find`; `broadcast_targets`; `Peer.note_ip`.
2. `ui.py`: `_redraw` (cốt lõi của mục 4.3 trong đề), `print_line`, `read_line`, `set_footer`.

**Kiểm tra trên máy thật:**
- [ ] 3 máy mở lên → thấy `[+] ... is online` trong ≤ 3 giây; `/list` đúng tên, IP, trạng thái.
- [ ] `/status away` → máy khác thấy `is now away` ngay (không đợi chu kỳ 3 giây).
- [ ] Tắt một máy (đóng cửa sổ bằng nút X hoặc `/quit`; chương trình không gửi gói UDP tạm biệt) → ≤ ~10 giây sau các máy khác thấy `went offline`.
- [ ] **Gõ dở**: gõ `you can che` rồi nhờ bạn gửi tin → tin chèn lên trên, dòng nhập còn nguyên. Thử cả dòng dài hơn chiều rộng cửa sổ, và khi đang gửi file.
- [ ] Windows: kiểm tra Backspace, tiếng Việt có dấu khi gõ, Ctrl-C thoát sạch.
- [ ] Chụp Wireshark: filter `udp.port == 9999` → gói `HELLO|Ha|5001|online`, lặp mỗi 3 giây, đích là địa chỉ broadcast.

**Câu hỏi thầy/cô có thể hỏi:**
- *Vì sao discovery dùng UDP?* → Broadcast chỉ có ở UDP; mất một gói không sao vì 3 giây sau có gói mới; không cần kết nối tới từng máy.
- *Làm sao biết ai offline?* → Mỗi `Peer` có `last_seen`; `_expire` (chạy mỗi giây) xoá peer quá 10 giây không có HELLO. Vì tính từ gói cuối, người tắt máy biến mất sau khoảng 7–11 giây.
- *`SO_BROADCAST`, `SO_REUSEADDR` để làm gì?* → Cho phép gửi tới địa chỉ broadcast; cho phép nhiều tiến trình cùng bind cổng 9999 trên một máy (để test).
- *Vẽ lại dòng nhập thế nào?* → Dưới khoá `ui.lock`: `ESC[<n>A` lên các dòng đã vẽ, `\r` + `ESC[J` xoá đến cuối màn hình, in tin + `\n`, rồi in lại `> ` + chuỗi đang gõ (`_buf`). Bàn phím được đọc từng phím (`msvcrt.getwch` / `termios`).
- *Vì sao máy có nhiều card mạng hay sai IP?* → Gói UDP có thể tới từ nhiều IP; `Peer.note_ip` ưu tiên IP cùng mạng con, `open_chat` thử lần lượt.

## 3. Châu — Truyền file, test, README, báo cáo, demo

**Đọc hiểu:**
1. `node.py`: `send_file`, `_send_file_worker`, `_receive_file`, `_reserve_file`.
2. `tests/run_tests.py` (đọc các mục `section(...)` để biết đang test gì).

**Kiểm tra trên máy thật:**
- [ ] Gửi file ~5–50 MB giữa 2 máy (thêm `--throttle 512` ở bên gửi để thấy thanh % chạy từ từ). Trong lúc truyền vẫn chat được.
- [ ] So sánh file nhận và file gốc: Windows `certutil -hashfile <file> SHA256`, Linux/macOS `sha256sum <file>` → hai giá trị phải giống nhau.
- [ ] Gửi file trùng tên → `ten (1).ext`. Gửi file có dấu cách trong tên: `/send "bao cao.pdf"`.
- [ ] `/send` file không tồn tại → báo lỗi. Đang gửi mà tắt bên gửi → bên nhận báo `interrupted; partial file deleted`, thư mục `downloads/` không còn file `.part`.
- [ ] Chạy `python tests/run_tests.py` và `python tests/test_ui_redraw.py`, lưu kết quả (đã có mẫu trong `docs/`).

**Câu hỏi thầy/cô có thể hỏi:**
- *Vì sao file đi trên kết nối TCP riêng?* → Nếu gửi chung kết nối chat, file lớn chiếm đường truyền, tin nhắn phải chờ. Kết nối riêng + thread riêng giúp chat không bị chặn (đề bắt buộc).
- *Gửi file lớn có tốn RAM không?* → Không: đọc/ghi từng khối 64 KB (`protocol.CHUNK`).
- *Biết file nhận đủ bằng cách nào?* → Header `FILE|name|size|` cho biết `size`; bên nhận đọc đúng `size` byte (`read_chunks`), ghi vào `.part` rồi đổi tên; gửi `ACK|size|` để bên gửi biết đã lưu xong.
- *Truyền đứt giữa chừng?* → Bên nhận bắt được lỗi/timeout 30 giây, xoá `.part`, báo lỗi (đúng mục 5.3 của đề).
- *Tên file độc hại như `../../x`?* → `safe_filename` chỉ giữ tên cuối, bỏ ký tự đặc biệt.

**Việc của Châu khi nộp bài:** ghép báo cáo (từ `docs/BAO_CAO.md` sang Word/PDF, 5–10 trang, chèn ảnh Wireshark của Bích và Hà), nén
source + README + báo cáo, kiểm tra lại đúng yêu cầu mục 7 của đề.

---

## 4. Kịch bản demo 10 phút (cần ≥ 2 máy; tốt nhất 3 máy, mỗi người một máy)

| Phút | Ai | Làm gì |
|---|---|---|
| 0–1 | Bích | Giới thiệu: UDP cho discovery/status, TCP cho chat/file; mở sơ đồ thread trong báo cáo |
| 1–3 | Hà | Mở 3 bản → `[+] ... online`, `/list`, `/status away`; tắt cứng một máy → vài giây sau `went offline` |
| 3–6 | Bích | `/chat`, nhắn hai chiều; người thứ ba nhắn xen vào → unread; `/chat` chuyển qua lại |
| 6–7 | Hà | Gõ dở `you can che`, bạn gửi tin → dòng nhập vẫn nguyên |
| 7–9 | Châu | `/send` file lớn (có `--throttle`) → thấy %, vừa gửi vừa chat; mở `downloads/`, so SHA-256 |
| 9–10 | Cả nhóm | Lỗi: `/send` file không có, `/chat` người đã tắt; mở ảnh Wireshark; trả lời câu hỏi |

Chuẩn bị sẵn: file demo 5–20 MB, 3 cửa sổ terminal đã đặt cỡ chữ to, hotspot dự phòng, và **đã chạy thử toàn bộ kịch bản một lần**.
