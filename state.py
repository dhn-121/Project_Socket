from __future__ import annotations
import socket
import threading # da luong
import time # thoi gian 

# CAU HINH TRANG THAI BAN THAN -----------------------------

my_name : str = ""

my_tcp_port : int = 1234

my_status : str = "online" # hoac away

bcast_address : str = "255.255.255.255"

running : bool = True # Dat thanh false khi go /quit (dung cac luong)

# DU LIEU CHUNG -----------------------------

# Vì nhiều luồng cùng đọc và ghi vào các biến danh sách bên dưới tại một thời điểm, 
# luồng nào muốn chạm vào dữ liệu bắt buộc phải "xin khóa" de tranh xung dot bo nho
state_lock = threading.Lock() 

# Cau truc : {"Chau"  : {"ip"  : "192.168.1.12", "tcp_port" : 6000, 
#            "status"  : "online", "last_seen"  : 1732215600.0}}
online_peers : dict = {} # luu danh sach nguoi dung truc tuyen voi key la ten nguoi dung

# Cau truc : {"Chau"  : <socket.socket object>} 
# Moi cap peer toi da 1 ket noi chat
chat_conns : dict = {} # luu anh xa tu ten nguoi dung sang doi tuong socket tcp dang mo

# Cau truc : {"Chau"  : ["[Chau] Alo", "[Ha] Nghe ro"]}
chat_history : dict = {} # luu danh sach lich su cac dong tin nhan

# Cau truc : {"Chau"  : 2}
unread_counts : dict = {} # bo dem so tin nhan chua doc cua tung nguoi

# Ten doi tuong dang chat hien tai (None neu dang o man hinh chinh)
current_partner : str | None = None # Luu ten nguoi tro chuyen tren man hinh


def update_peer (name : str , ip : str , tcp_port : int , status : str) -> bool :
    """
    TV1 gọi khi nhận gói UDP HELLO.
    Trả về True nếu đây là người dùng MỚI lên mạng (để in thông báo online).
    """

    with state_lock : # quan ly ngu canh tu dong khoa khi bat dau khoi lenh và tu dong mo khi thoat ra
        is_new = name not in online_peers # kiem tra xem name co trong danh sach?
        online_peers [name] = {
            "ip" : ip,
            "tcp_port" : tcp_port,
            "status" : status,
            "last_seen" : time.time(), # tinh bang giay tu thoi diem vua nhan goi HELLO
        }
    return is_new

def remove_timed_out_peers (timeout : float = 10.0) -> list[str] :
    """
    TV1 gọi định kỳ mỗi 1 giây để lọc các peer quá 10s không gửi HELLO.
    Trả về danh sách tên các peer vừa offline để in thông báo.
    """
    now = time.time()
    offline_names = []
    with state_lock:
        for name, info in list(online_peers.items()): 
            if now - info["last_seen"] > timeout: # Neu qua 10 giay khong nhan them tin thi xoa peer khoi danh sach và luu vao offline
                offline_names.append(name)
                del online_peers[name]
    return offline_names

def get_online_peers_snapshot() -> list[tuple[str, dict]]:
    """
    TV1 (lệnh /list) và TV2 (lệnh /chat <số>) gọi để lấy bản sao danh sách online
    mà không giữ Lock quá lâu.
    """
    with state_lock:
        return [(name, info.copy()) for name, info in online_peers.items()]

def get_chat_conn(peer_name: str) -> socket.socket | None:
    """TV2 và TV3 gọi để lấy socket chat đang mở của peer_name một cách an toàn luồng."""
    with state_lock:
        return chat_conns.get(peer_name)

def register_chat_conn(peer_name: str, conn: socket.socket) -> None:
    """TV2 gọi khi mở mới hoặc chấp nhận kết nối TCP chat (đảm bảo tối đa 1 kết nối/peer)."""
    with state_lock:
        old_conn = chat_conns.get(peer_name)
        if old_conn and old_conn is not conn:
            try:
                old_conn.close()
            except OSError:
                pass
        chat_conns[peer_name] = conn

def append_own_msg(partner: str, text: str) -> None:
    """TV2 hoặc TV3 gọi ngay sau khi gửi tin nhắn thành công để lưu vào lịch sử chat."""
    with state_lock:
        chat_history.setdefault(partner, []).append(f"[{my_name}] {text}")

def remove_chat_conn(peer_name: str) -> bool:
    """
    TV2 gọi khi nhận BYE|, gõ /leave hoặc đối tác rớt mạng.
    Trả về True nếu peer_name đang là current_partner (để reset về màn hình chính).
    """
    global current_partner
    was_current = False
    with state_lock:
        conn = chat_conns.pop(peer_name, None)
        if conn:
            try:
                conn.close()
            except OSError:
                pass
        if current_partner == peer_name:
            current_partner = None
            was_current = True
    return was_current


def handle_incoming_chat_msg(sender: str, text: str) -> tuple[str, int]:
    """
    TV2 gọi khi nhận gói MSG| từ sender.
    Lưu lịch sử và quyết định hiển thị ngay hay cộng dồn tin chưa đọc.
    Trả về tuple (action, unread_count):
      - ("SHOW", 0): In thẳng tin nhắn ra màn hình.
      - ("AUTO_SWITCH_AND_SHOW", 0): Đang rảnh nên tự chuyển đối tác và in tin.
      - ("UNREAD", count): Đang chat với người khác, chỉ báo số tin chưa đọc.
    """
    global current_partner
    formatted = f"[{sender}] {text}"
    with state_lock:
        chat_history.setdefault(sender, []).append(formatted) #kiểm tra nếu sender chưa từng có trong chat_history thì tự động khởi tạo một danh sách rỗng [] cho người đó

        if current_partner == sender:
            return ("SHOW", 0)
        elif current_partner is None:
            current_partner = sender
            return ("AUTO_SWITCH_AND_SHOW", 0)
        else:
            # Lấy số tin chưa đọc hiện tại của sender, nếu chưa có thì trả về mặc định là 0 rồi cộng thêm 1
            unread_counts[sender] = unread_counts.get(sender, 0) + 1 
            return ("UNREAD", unread_counts[sender])


def switch_partner(target_name: str) -> list[str]:
    """
    TV2 gọi khi người dùng gõ /chat <target_name>.
    Chuyển đối tác hiện tại, xóa bộ đếm chưa đọc và trả về lịch sử tin nhắn cũ.
    """
    global current_partner
    with state_lock:
        current_partner = target_name
        unread_counts[target_name] = 0
        return list(chat_history.get(target_name, []))