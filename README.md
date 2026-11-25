# LAN Chat & File Sharing (UDP + TCP, Python)

Ứng dụng chat ngang hàng (peer-to-peer) chạy trên console, dùng **UDP** để phát hiện người dùng / trạng thái
và **TCP** để chat và gửi file. Chỉ dùng thư viện chuẩn của Python — **không cần cài thêm gì**.

## 1. Yêu cầu

- Python **3.8 trở lên** (kiểm tra: `python --version`; trên Windows có thể là `py --version`)
- 2+ máy cùng mạng LAN/Wi-Fi, **hoặc** nhiều cửa sổ terminal trên một máy
- Windows 10+ (cmd / PowerShell / Windows Terminal), Linux, macOS

## 2. Chạy

```bash
# máy 1
python lanchat.py --name Bich
# máy 2
python lanchat.py --name Ha
# máy 3
python lanchat.py --name Chau
```

Mỗi người phải có **tên khác nhau**. Chương trình tự chọn cổng TCP trống đầu tiên từ 5000
(hoặc chỉ định bằng `--port 5001`).

### Chạy nhiều bản trên MỘT máy (để test)

Mở 3 cửa sổ terminal, chạy lần lượt (không cần `--port`, mỗi bản tự lấy cổng khác nhau):

```bash
python lanchat.py --name Bich
python lanchat.py --name Ha
python lanchat.py --name Chau
```

Các bản chạy chung thư mục sẽ dùng chung `downloads/`; nếu trùng tên file, file đến sau được lưu thành `ten (1).ext`.
Muốn tách riêng: thêm `--downloads dl_bich`.

### Tuỳ chọn dòng lệnh

| Tuỳ chọn | Ý nghĩa | Mặc định |
|---|---|---|
| `--name` | Tên hiển thị (duy nhất trong mạng) | hỏi khi chạy |
| `--port` | Cổng TCP lắng nghe | cổng trống đầu tiên từ 5000 |
| `--udp-port` | Cổng UDP để discovery | 9999 |
| `--downloads` | Thư mục lưu file nhận | `downloads/` |
| `--broadcast ADDR` | Thêm địa chỉ broadcast (vd `192.168.1.255`) | tự tính |
| `--throttle KBPS` | Giới hạn tốc độ gửi file — để **thấy rõ thanh % khi demo** | tắt |
| `--plain` | Nhập theo dòng, không vẽ lại (chỉ dùng cho test tự động) | tắt |

## 3. Lệnh trong chương trình

| Lệnh | Mô tả |
|---|---|
| `/list` | Danh sách người online: số, tên, IP, trạng thái (kèm số tin chưa đọc) |
| `/chat <tên\|số>` | Mở chat (kết nối TCP) với người đó; nếu đã có chat thì chuyển sang |
| `<văn bản>` | Gửi tin cho người đang chat (gõ `//abc` để gửi tin bắt đầu bằng `/`) |
| `/send <file>` | Gửi file cho người đang chat (chạy thread riêng, có % tiến độ) |
| `/leave` | Đóng chat hiện tại |
| `/status <online\|away>` | Đổi trạng thái (gửi qua UDP) |
| `/help` | Xem lệnh |
| `/quit` | Thoát (cũng có thể Ctrl-C hoặc Ctrl-D) |

Phím khi gõ: `Backspace` xoá ký tự, `Ctrl-U` xoá cả dòng. File nhận được lưu trong `downloads/`.

### Ví dụ phiên làm việc

```
$ python lanchat.py --name Bich
[*] Listening on TCP port 5000
[*] Broadcasting presence on UDP port 9999
[+] Ha is online (192.168.1.12, online)
> /list
1. Ha (192.168.1.12) online
> /chat Ha
[*] Connected to Ha (192.168.1.12:5001)
[Bich] Hi Ha, are you ready for the demo?
[Ha] Yes! Send me the slides
> /send slides.pdf
[*] Sending slides.pdf (2.4 MB) to Ha ...
[*] Sending slides.pdf to Ha ... 63%        <- dòng này cập nhật tại chỗ
[*] Sent slides.pdf (2.4 MB) to Ha ... 100% Done.
```

## 4. Kiểm thử

### 4.1 Test tự động (khuyến nghị chạy trước khi demo)

```bash
python tests/run_tests.py          # 47 kiểm tra: framing, discovery, status, chat, file, lỗi, crash (~25 giây)
pip install pyte                   # chỉ cần cho test giao diện, chỉ Linux/macOS
python tests/test_ui_redraw.py     # 20 kiểm tra: gõ dở mà có tin đến, thanh %, dòng dài, Ctrl-C (~15 giây)
```

`run_tests.py` mở 3 tiến trình thật (Bich, Ha, Chau) và điều khiển như 3 người dùng; kết quả mẫu nằm trong
`docs/test_output_integration.txt` và `docs/test_output_ui.txt`.

> Khi chạy test, đừng mở sẵn bản lanchat khác có tên Bich/Ha/Chau.

### 4.2 Test bằng tay (kịch bản demo)

1. Mở 2–3 bản → thấy `[+] ... is online` ở các bản kia sau ≤ 3 giây. `/list` hiện đủ.
2. `/status away` ở một bản → các bản khác thấy `... is now away`, `/list` hiện `away`.
3. A `/chat B`, gửi vài dòng → B thấy `New message from A (n unread)`; B `/chat A` → thấy các tin chưa đọc; B trả lời.
4. C `/chat B` gửi tin khi B đang chat với A → B thấy thông báo unread của C, không bị lẫn vào khung chat với A.
5. **Gõ dở**: ở máy A gõ `you can che` (đừng Enter), nhờ B gửi tin → tin hiện lên trên, dòng `> you can che` được vẽ lại.
6. `/send` một file vài MB (thêm `--throttle 512` để chậm lại) → thấy `%` chạy, trong lúc đó vẫn chat được; mở `downloads/` kiểm tra file.
7. Lỗi: `/send khong_co.pdf`; `/chat NguoiLa`; tắt cứng một bản (đóng cửa sổ) → bản kia báo mất kết nối, ≤ ~10 giây sau thì `went offline`.

## 5. Chụp Wireshark (cho báo cáo)

1. Mở Wireshark, chọn đúng card mạng (Wi-Fi/Ethernet). Chạy 2 bản lanchat **trên 2 máy khác nhau** để thấy gói rõ nhất
   (chạy cùng một máy thì chọn card *Loopback* / *Adapter for loopback traffic capture*).
2. **UDP broadcast** — display filter: `udp.port == 9999`. Chụp một gói `HELLO|Bich|5000|online`
   (đích `255.255.255.255` hoặc `x.y.z.255`); mở phần *Data* để thấy nội dung. Gói lặp lại mỗi 3 giây.
3. **TCP three-way handshake** — filter: `tcp.port == 5000` (cổng TCP của người nhận), rồi A gõ `/chat B`.
   Chụp 3 gói đầu: `[SYN]` → `[SYN, ACK]` → `[ACK]`, sau đó gói `PSH,ACK` chứa `HI|...` và `MSG|...`.
   Có thể dùng *Follow → TCP Stream* để thấy khung `MSG|<length>|<text>`.
4. Kèm giải thích ngắn: UDP không có handshake, gửi broadcast tới mọi máy; TCP cần 3 bước trước khi gửi dữ liệu.

## 6. Lỗi thường gặp

| Hiện tượng | Cách xử lý |
|---|---|
| Không thấy máy khác (`/list` trống) | **Windows Firewall**: khi chạy lần đầu chọn *Allow* cho Python ở cả mạng *Private* và *Public* (hoặc Windows Defender Firewall → *Allow an app* → thêm `python.exe`). Hai máy phải cùng Wi-Fi/mạng LAN. |
| Vẫn không thấy dù đã tắt tường lửa | Wi-Fi trường/quán cà phê thường bật *client isolation* (chặn các máy nói chuyện với nhau) → dùng điện thoại phát Wi-Fi hoặc router riêng. Có thể thêm `--broadcast 192.168.x.255` (x theo mạng của bạn). |
| `cannot bind UDP port 9999` | Một chương trình khác đang chiếm cổng; đổi `--udp-port 9998` ở **tất cả** các máy. |
| `Cannot connect to ...: Connection refused` | Người kia vừa tắt; chờ họ biến mất khỏi `/list`. |
| `another user is also called '...'` | Hai người trùng tên — đổi tên. |
| Có VPN / WSL / VirtualBox | Chương trình tự thử lần lượt các IP của đối phương (ưu tiên IP cùng mạng con). Nếu vẫn lỗi, tạm tắt card ảo. |
| Chữ tiếng Việt bị lỗi trên console cũ | Dùng Windows Terminal, hoặc chạy `chcp 65001` trước. |

## 7. Cấu trúc thư mục

```
lan_chat/
├── lanchat.py        # điểm vào: đọc tham số, vòng lặp lệnh (/list, /chat, ...)
├── protocol.py       # định dạng gói UDP/TCP, đọc đủ N byte (framing), tiện ích
├── discovery.py      # UDP: thread broadcast + thread listen, danh sách online, timeout 10 s
├── node.py           # TCP: acceptor, receiver mỗi peer, chat, gửi/nhận file, unread
├── ui.py             # giao diện console: dòng nhập ở cuối, vẽ lại khi có tin đến, thanh %
├── tests/
│   ├── run_tests.py       # test tích hợp (3 tiến trình)
│   └── test_ui_redraw.py  # test giao diện trong terminal ảo (cần pyte)
├── docs/
│   ├── BAO_CAO.md              # bản nháp báo cáo
│   ├── test_output_*.txt       # kết quả chạy test mẫu
└── CHIA_VIEC.md      # phân công kiểm tra cho 3 thành viên
```

## 8. Tóm tắt kỹ thuật

- **Giao thức** (xem `protocol.py` và `docs/BAO_CAO.md`):
  UDP `HELLO|<name>|<tcp_port>|<status>`; TCP `HI|…`, `MSG|<length>|<text>`, `FILE|<name>|<size>|<bytes>`, `ACK|`, `ERR|`, `BYE|`.
- **Mô hình thread**: Main (bàn phím) · UDP broadcaster · UDP listener · TCP acceptor · TCP receiver (mỗi peer) ·
  File sender / File receiver (mỗi lần truyền, trên kết nối TCP riêng nên chat không bị chặn).
- **Dữ liệu dùng chung** (danh sách online, kết nối, unread, lịch sử) được bảo vệ bằng lock; mọi thao tác in màn hình đi qua một lock.
- **Giới hạn đã biết**: cần tên duy nhất; mũi tên ← → / Home / End chưa hỗ trợ (chỉ Backspace, Ctrl-U); không mã hoá; IPv4 only.
