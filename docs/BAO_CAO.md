# BÁO CÁO ĐỒ ÁN SOCKET PROGRAMMING — LAN CHAT & FILE SHARING

> **BẢN NHÁP.** Nội dung kỹ thuật bám sát code hiện có. Nhóm cần: (1) điền thông tin nhóm, (2) chèn ảnh Wireshark thật
> vào mục 9 (chỗ có `[CHÈN ẢNH ...]`), (3) sửa mục 7 cho đúng với những khó khăn thật của nhóm, (4) chuyển sang Word/PDF
> (cỡ chữ 12, giãn dòng 1.15 sẽ ra khoảng 7–9 trang).

**Môn học:** Mạng máy tính (CSC10008) · **Nhóm:** Đinh Ngọc Bích, … (Hà), … (Châu) · **Ngôn ngữ:** Python 3 (socket, threading)

---

## 1. Giới thiệu

Đồ án xây dựng một ứng dụng chat ngang hàng (peer-to-peer) chạy trên console trong mạng LAN. Mọi người dùng chạy cùng một
chương trình; chương trình tự tìm các máy khác, hiển thị ai đang online, cho phép chọn người để chat và gửi file.
Hai giao thức tầng giao vận được dùng đúng với thế mạnh của từng loại:

| Giao thức | Dùng cho | Lý do chọn |
|---|---|---|
| **UDP** (broadcast) | Phát hiện người dùng, trạng thái online/away | Không cần kết nối, broadcast được tới mọi máy, mất một gói không ảnh hưởng vì 3 giây sau có gói mới |
| **TCP** | Tin nhắn chat, truyền file | Tin cậy, đúng thứ tự, không mất dữ liệu |

**Chức năng đã cài:** discovery và danh sách online có timeout 10 giây; trạng thái online/away; chat TCP nhiều người cùng lúc
kèm bộ đếm tin chưa đọc; gửi/nhận file có phần trăm tiến độ, lưu trong `downloads/`; giao diện console vẽ lại dòng nhập khi có tin
đến; xử lý lỗi (đứt kết nối, người dùng offline, file không tồn tại, truyền đứt giữa chừng).

## 2. Kiến trúc tổng thể

```
                      ┌──────────────────────────  một tiến trình lanchat.py  ──────────────────────────┐
  bàn phím ──────────►│ Main thread: ui.read_line → lệnh /list /chat /send ... hoặc gửi tin             │
                      │        │                                                                        │
                      │        ├──► Node (node.py)      TCP: kết nối chat, gửi/nhận file, unread        │
                      │        └──► Discovery (discovery.py)   UDP: danh sách người online              │
                      │                                                                                 │
  màn hình ◄──────────│ ConsoleUI (ui.py): một lock cho mọi thao tác in; dòng nhập luôn ở cuối         │
                      └─────────────────────────────────────────────────────────────────────────────────┘
        UDP 9999  ◄──── HELLO|name|tcp_port|status ────►  các peer khác (broadcast mỗi 3 giây)
        TCP port  ◄──── HI / MSG / FILE / ACK / BYE ───►  các peer khác (kết nối trực tiếp)
```

| Module | Vai trò |
|---|---|
| `lanchat.py` | Điểm vào: đọc tham số, vòng lặp lệnh |
| `protocol.py` | Định dạng gói UDP/TCP, `FrameReader` đọc đủ N byte, hàm tiện ích (tên file an toàn, …) |
| `discovery.py` | Thread broadcast + thread listen, danh sách online, timeout |
| `node.py` | Thread acceptor, thread receiver mỗi peer, chat, gửi/nhận file, tin chưa đọc |
| `ui.py` | Giao diện console: đọc phím, vẽ lại dòng nhập, dòng tiến độ |

Chỉ dùng thư viện chuẩn của Python (`socket`, `threading`, `argparse`, …), đúng yêu cầu dùng Socket API gốc của ngôn ngữ.

## 3. Mô hình thread

| Thread | Chờ gì | Nhiệm vụ | Nơi cài đặt |
|---|---|---|---|
| Main | Bàn phím | Đọc input, chạy lệnh, gửi tin | `lanchat.run_commands` |
| UDP broadcaster | Timer 3 giây | Gửi gói HELLO | `Discovery._broadcast_loop` |
| UDP listener | Gói UDP (timeout 1 giây) | Cập nhật danh sách online; xoá peer quá 10 giây | `Discovery._listen_loop` |
| TCP acceptor | Kết nối mới | Mỗi kết nối tới → tạo một thread xử lý | `Node._accept_loop` |
| TCP receiver (mỗi peer) | Socket của peer đó | Hiển thị tin, nhận BYE | `Node._run_chat` |
| File receiver (mỗi file) | Kết nối file | Ghi file ra đĩa, gửi ACK | `Node._receive_file` |
| File sender (mỗi lần gửi) | — | Đọc file, gửi theo khối 64 KB | `Node._send_file_worker` |

**Dữ liệu dùng chung và khoá:**

| Dữ liệu | Khoá bảo vệ |
|---|---|
| Danh sách người online (`Discovery.peers`) | `Discovery.lock` |
| Kết nối, đối tác hiện tại, tin chưa đọc, lịch sử (`Node.conns/current/unread/history`) | `Node.lock` (RLock) |
| Ghi vào một socket (tránh hai thread gửi xen kẽ byte) | `Conn.send_lock` |
| Mọi thao tác in ra màn hình | `ConsoleUI.lock` (RLock) |

Thứ tự khoá luôn là `Node.lock` → `ConsoleUI.lock` (không bao giờ ngược lại), nên không xảy ra deadlock. Các callback của discovery
được gọi **ngoài** `Discovery.lock`.

## 4. Thiết kế giao thức

### 4.1 UDP — gói phát hiện (một datagram văn bản)

```
HELLO|<name>|<tcp_port>|<status>        ví dụ:  HELLO|Bich|5000|online      (status = online | away)
```

Gửi mỗi 3 giây tới `255.255.255.255`, địa chỉ broadcast của mạng con (`x.y.z.255`) và `127.255.255.255` (chạy nhiều bản trên một máy
/ không có mạng). Đổi trạng thái bằng `/status` gửi HELLO ngay lập tức. Gói sai định dạng bị bỏ qua (`parse_hello`).

### 4.2 TCP — khung (frame) có header

TCP là luồng byte, **không có ranh giới tin nhắn**: hai lần `send()` có thể đến trong một lần `recv()`, một lần `send()` có thể
bị chia làm nhiều lần `recv()`. Vì vậy mọi frame bắt đầu bằng header kết thúc bằng `|`, cho biết loại và độ dài:

| Frame | Định dạng | Ý nghĩa |
|---|---|---|
| `HI` | `HI\|<name>\|<tcp_port>\|<kind>\|` | Frame đầu tiên của mỗi kết nối; `kind` = `CHAT` hoặc `FILE` |
| `MSG` | `MSG\|<length>\|<văn bản UTF-8>` | Tin nhắn; `length` là số **byte** |
| `FILE` | `FILE\|<tên file>\|<size>\|<byte thô>` | File; `size` là số byte theo sau |
| `ACK` | `ACK\|<size>\|` | Bên nhận xác nhận đã lưu đủ file |
| `ERR` | `ERR\|<lý do>\|` | Bên nhận không lưu được file |
| `BYE` | `BYE\|` | Đóng chat êm |

Cách đọc (`protocol.FrameReader`): `read_until()` đọc tới dấu `|` kế tiếp; `read_exact(n)` và `read_chunks(n)` **lặp `recv()` cho đến khi
đủ đúng n byte**; dữ liệu thừa (đầu frame kế tiếp) được giữ lại trong bộ đệm. Dấu `|` (0x7C) không bao giờ xuất hiện bên trong một ký tự UTF-8
nhiều byte, nên tách theo byte là an toàn; còn nội dung file là dữ liệu nhị phân tuỳ ý (kể cả chứa `|`) vì được đọc theo độ dài chứ không theo dấu.
Giới hạn: header ≤ 512 byte, tin nhắn ≤ 1 MB — vượt thì coi là lỗi giao thức và đóng kết nối.

### 4.3 Trình tự

```
Chat:  Bich                                          Ha
        │──── SYN ──────────────────────────────────►│
        │◄─── SYN, ACK ─────────────────────────────│      bắt tay ba bước (do TCP)
        │──── ACK ──────────────────────────────────►│
        │──── HI|Bich|5000|CHAT| ───────────────────►│
        │──── MSG|5|hello ──────────────────────────►│
        │◄─── MSG|2|hi ─────────────────────────────│
        │──── BYE| ─────────────────────────────────►│      (/leave)

File:  (kết nối TCP MỚI, song song với kết nối chat)
        │──── HI|Bich|5000|FILE| ───────────────────►│
        │──── FILE|slides.pdf|2516582|<2516582 byte> ►│      bên nhận ghi vào slides.pdf.part
        │◄─── ACK|2516582| ─────────────────────────│      đủ byte → đổi tên .part thành tên thật
```

## 5. Chi tiết chức năng

### 5.1 Discovery và trạng thái (UDP)

Thread broadcaster gửi HELLO mỗi 3 giây. Thread listener bind `("", 9999)` với `SO_REUSEADDR`/`SO_REUSEPORT` (để nhiều bản cùng chạy trên một máy),
nhận gói, bỏ qua gói của chính mình (cùng tên), cập nhật `peers[name]` gồm IP (lấy từ địa chỉ nguồn của gói UDP), cổng TCP, trạng thái và `last_seen`.
Sau mỗi vòng (≤ 1 giây) kiểm tra `_expire`: peer quá 10 giây không có HELLO bị xoá và console in `[-] <tên> went offline`;
peer mới in `[+] <tên> is online`; đổi trạng thái in `[*] <tên> is now away`. Do thời gian tính từ gói cuối cùng nhận được,
người dùng tắt máy biến mất sau khoảng 7–11 giây (test đo được 8,2 giây).

Máy có nhiều card mạng (VPN, WSL, VirtualBox) có thể gửi HELLO từ nhiều IP; `Peer.note_ip` giữ cả danh sách, ưu tiên IP cùng mạng con, và
`open_chat` thử lần lượt từng IP.

### 5.2 Chat (TCP)

`/chat <tên|số>` tra người trong danh sách online, mở kết nối TCP tới `(IP, cổng TCP)`, gửi `HI`, đăng ký kết nối và mở thread receiver.
Tin nhắn nhập từ bàn phím được gửi bằng `MSG`. Tin nhận được: nếu từ **đối tác hiện tại** thì in ngay; nếu từ người khác thì lưu vào `unread[tên]` và in
`[*] New message from <tên> (n unread)`. Khi `/chat` sang người đó, các tin chưa đọc được in ra. Kết nối của nhiều peer sống song song.

Trường hợp hai người cùng `/chat` nhau gần như đồng thời sẽ có hai kết nối; mỗi kết nối ghi nhớ bên khởi tạo, và cả hai đầu cùng giữ
kết nối do bên có **tên nhỏ hơn** mở (`Node._register`), nên không bị đóng cả hai.

### 5.3 Gửi file (TCP)

`/send <file>` kiểm tra file tồn tại, rồi tạo thread gửi riêng. Thread mở **kết nối TCP mới** tới peer (kind = FILE), gửi header rồi đọc file từng khối
64 KB; sau mỗi khối cập nhật phần trăm. Bên nhận có thread riêng, ghi vào `downloads/<tên>.part`, xong thì `os.replace` thành tên thật và gửi `ACK`;
bên gửi chỉ báo `100% Done` sau khi nhận ACK đúng kích thước. Trùng tên → `tên (1).ext`. Vì file đi kênh riêng nên chat không bị chặn.

### 5.4 Giao diện console: gõ trong khi có tin đến

Console không có hai khung riêng nên chương trình mô phỏng: **dòng cuối luôn là dòng nhập, phía trên là lịch sử**. Chương trình đọc từng phím
(`msvcrt.getwch` trên Windows, `termios` chế độ cbreak trên Linux/macOS), giữ chuỗi đang gõ trong bộ đệm. Khi có tin đến, dưới một khoá duy nhất:
(1) xoá dòng nhập (`\r` + `ESC[J`), (2) in tin nhắn kèm xuống dòng, (3) in lại `> ` cùng chuỗi đã gõ. Hai chi tiết thêm: dòng nhập dài hơn chiều rộng
cửa sổ chiếm nhiều hàng nên khi vẽ lại phải lùi con trỏ đúng số hàng; dòng tiến độ file là một "chân trang" nằm giữa lịch sử và dòng nhập, được cập
nhật tại chỗ. Nội dung tin nhận được cũng bị lọc ký tự điều khiển (chẳng hạn ESC) để một peer không thể chèn mã điều khiển vào terminal người khác.

Ví dụ màn hình thật (lấy từ `docs/test_output_ui.txt`, terminal ảo 80×24) — người dùng đang gõ `you can che`, tin `[Ha] ok, which part?` đến:

```
 Trước:                                        Sau:
 [Bich] Hi Ha, are you ready for the demo?     [Bich] Hi Ha, are you ready for the demo?
 [Ha] Yes! Send me the slides                  [Ha] Yes! Send me the slides
 > you can che_                                [Ha] ok, which part?          <- chèn lên trên
                                               > you can che_                <- phần đã gõ được giữ nguyên
```

## 6. Xử lý lỗi

| Tình huống | Hành vi |
|---|---|
| Peer đóng kết nối / crash | Receiver nhận EOF → in `Connection with X was lost` (hoặc `X closed the chat` nếu có `BYE`), quay về prompt chính; tin chưa đọc vẫn giữ. TCP keep-alive phát hiện cả trường hợp mất mạng không có FIN (khoảng 25 giây trên Linux, lâu hơn trên Windows) |
| Kết nối tới người đã offline | `create_connection` lỗi → in `Cannot connect to X ...`, không crash |
| `/send` file không tồn tại | In `File not found` trước khi tạo thread |
| Truyền file bị đứt (sender chết, mạng đứt, timeout 30 giây) | Bên nhận xoá file `.part`, in `interrupted; partial file deleted`; bên gửi in lỗi |
| Hết dung lượng đĩa | Bên nhận từ chối bằng `ERR`, bên gửi báo lỗi |
| Lệnh sai / thiếu tham số | In thông báo lỗi và danh sách lệnh |
| Gói UDP rác, frame TCP sai, tên file độc hại (`../x`) | Bỏ qua gói UDP; đóng kết nối khi frame sai; `safe_filename` chỉ giữ tên cuối |
| Nhấn Ctrl-C / Ctrl-D | Gửi `BYE` cho các peer, đóng socket, khôi phục terminal |

## 7. Khó khăn và cách giải quyết  *(nhóm sửa lại theo trải nghiệm thật)*

| Khó khăn | Giải pháp |
|---|---|
| TCP không có ranh giới tin nhắn: một `recv()` có thể thiếu hoặc thừa | Header kèm độ dài, `FrameReader` lặp `recv()` đến đủ byte và giữ phần dư. Test: gửi từng byte một và gộp nhiều frame |
| Tin đến làm hỏng dòng đang gõ | Đọc từng phím + bộ đệm + vẽ lại dưới một khoá; xử lý dòng dài nhiều hàng và dòng tiến độ |
| Nhiều thread cùng in ra màn hình | Mọi `print` đi qua `ConsoleUI.print_line` (một RLock) |
| Gửi file làm kẹt chat | File đi trên kết nối TCP riêng, thread riêng |
| Hai người `/chat` nhau cùng lúc sinh hai kết nối | Quy tắc giữ kết nối do bên có tên nhỏ hơn mở, hai đầu nhất quán |
| Chạy nhiều bản trên một máy cùng bind cổng UDP 9999 | `SO_REUSEADDR` / `SO_REUSEPORT`, gửi thêm tới `127.255.255.255` |
| Máy nhiều card mạng báo sai IP | Lưu mọi IP nguồn, ưu tiên cùng mạng con, thử lần lượt khi kết nối |
| Firewall Windows chặn broadcast | Cho phép Python trong Windows Defender Firewall (ghi trong README) |
| Peer tắt đột ngột khi đang truyền file | `.part` + timeout + xoá file dở; bên gửi chờ `ACK` mới báo thành công |

## 8. Kiểm thử

Hai bộ test tự động nằm trong `tests/` (kết quả mẫu trong `docs/test_output_*.txt`):

| Bộ test | Cách chạy | Số kiểm tra | Nội dung chính |
|---|---|---|---|
| `run_tests.py` | 3 tiến trình thật (Bich, Ha, Chau) điều khiển qua stdin/stdout | 47 / 47 đạt | framing (tách byte, gộp frame, `\|` trong file), discovery, status, chat hai chiều, unread, nhiều peer, gửi file (SHA-256 khớp, chat xen lúc truyền, trùng tên, file rỗng), lỗi, crash, timeout 10 giây, truyền đứt |
| `test_ui_redraw.py` | 1 tiến trình trong terminal ảo 80×24 (thư viện `pyte`) | 20 / 20 đạt | gõ dở rồi có tin đến, Backspace, Ctrl-U, tiếng Việt, dòng nhập dài hơn 80 cột, thanh % khi đang gõ, Ctrl-C |

Hạn chế của phần kiểm thử: các test chạy trên Linux và trên một máy. Nhóm đã kiểm tra thủ công thêm trên **… máy thật / Windows** *(điền kết quả thật)*.

## 9. Ảnh Wireshark

**9.1 UDP broadcast** — filter `udp.port == 9999`.
`[CHÈN ẢNH: gói HELLO|<tên>|<cổng>|online, IP đích là địa chỉ broadcast, lặp mỗi 3 giây]`
*Giải thích:* UDP không thiết lập kết nối; gói được gửi thẳng tới địa chỉ broadcast nên mọi máy trong mạng con đều nhận. Phần *Data* chứa văn bản `HELLO|...`.
Các gói cách nhau 3 giây; khi đổi `/status` có một gói gửi ngay.

**9.2 TCP three-way handshake** — filter `tcp.port == <cổng TCP của người nhận>`.
`[CHÈN ẢNH: 3 gói [SYN] → [SYN, ACK] → [ACK], tiếp theo là gói [PSH, ACK] chứa HI|... và MSG|...]`
*Giải thích:* trước khi gửi dữ liệu, A gửi SYN, B trả SYN+ACK, A trả ACK (ba bước) để hai bên thống nhất số thứ tự; sau đó mới có gói chứa `HI|...` và `MSG|<length>|...`.
Có thể dùng *Follow → TCP Stream* để thấy toàn bộ khung dữ liệu.

## 10. Phân công

| Thành viên | Phụ trách |
|---|---|
| Đinh Ngọc Bích | `protocol.py`, TCP chat/kết nối/lỗi, ảnh Wireshark TCP |
| … (Hà) | UDP discovery/status, giao diện console, ảnh Wireshark UDP |
| … (Châu) | Truyền file, kiểm thử, README, ghép báo cáo, kịch bản demo |

## 11. Kết luận và hướng phát triển

Ứng dụng đáp ứng đủ các yêu cầu của đề: dùng UDP cho phát hiện/trạng thái và TCP cho chat/file, có khung giao thức tự thiết kế, đa luồng với dữ liệu
dùng chung được bảo vệ bằng khoá, giao diện console không bị phá vỡ khi có tin đến, và xử lý lỗi. Hạn chế: tên phải duy nhất; chưa hỗ trợ các phím mũi tên khi chỉnh dòng nhập;
không mã hoá nội dung; chỉ IPv4. Hướng phát triển: mã hoá (TLS), nhóm chat, tiếp tục truyền file dở, GUI.

## Tài liệu tham khảo

1. Brian Hall, *Beej's Guide to Network Programming*.
2. Python Software Foundation, tài liệu module `socket` và `threading`.
3. RFC 768 (UDP), RFC 793 / RFC 9293 (TCP).
