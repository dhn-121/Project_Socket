from __future__ import annotations
from collections.abc import Callable
import socket
import threading

import protocol
import state

FileHandlerType = Callable[[socket.socket, str, str, int, Callable[[str], None]], None]
tcp_send_lock = threading.Lock()

def tcp_receiver (
        conn: socket.socket, addr: tuple, safe_print: Callable[[str], None] = print, 
        file_handler: FileHandlerType | None = None, known_peer_name: str | None = None) -> None:
        peer_name = known_peer_name
        handed_off_to_file = False
        try:
            if peer_name is None: 
                peer_name, _peer_tcp_port = protocol.recv_hi(conn)
            while state.running:
                packet_type = protocol.recv_token(conn, max_len=16)
                if packet_type == "FILE":
                    filename = protocol.recv_token(conn, max_len = 256)
                    filesz = int(protocol.recv_token(conn, max_len=32))
                    if file_handler is not None: 
                        handed_off_to_file = True
                        file_handler (conn, peer_name, filename, filesz, safe_print)
                    else:
                        safe_print(f"[*] Nhan duoc tep tin yeu cau '{filename}' tu {peer_name}")
                    return
                elif packet_type == "MSG":
                    if state.get_chat_conn(peer_name) is not conn:
                      state.register_chat_conn(peer_name, conn)

                    msg_len = int(protocol.recv_token(conn, max_len=16))
                    raw_payload = protocol.recv_exact(conn, msg_len)
                    text = raw_payload.decode("utf-8", errors="replace")
                    action, unread = state.handle_incoming_chat_msg(peer_name, text)
                    if action == "SHOW":
                        safe_print(f"[{peer_name}] {text}")
                    elif action == "AUTO_SWITCH_AND_SHOW":
                        safe_print(f"[*] Chatting with {peer_name}")
                        safe_print(f"[{peer_name}] {text}")
                    elif action == "UNREAD":
                        safe_print(f"[*] New message from {peer_name} ({unread} unread)")
                elif packet_type == "BYE":
                    state.remove_chat_conn(peer_name)
                    safe_print(f"[*] {peer_name} left the chat")
                    return
                else:
                    raise ValueError(f"Khong ro dang goi tin cua doi tac: {packet_type!r}")
        except (ValueError, UnicodeDecodeError):
            try:
                conn.close()
            except OSError:
                pass
        except (ConnectionError, OSError):
            if peer_name and state.get_chat_conn(peer_name) is conn:
                state.remove_chat_conn(peer_name)
                safe_print(f"[*] {peer_name} disconnected")
            elif not handed_off_to_file:
                try:
                    conn.close()
                except OSError:
                    pass

                

def tcp_acceptor (server_sock: socket.socket, safe_print: Callable[[str], None] = print, file_handler: FileHandlerType | None = None):
    while (state.running): 
        try: conn, addr = server_sock.accept()
        except OSError: break
        threading.Thread (
            target = tcp_receiver, 
            args = (conn, addr, safe_print, file_handler, None),
            daemon = True,
        ).start()

        
    
def start_tcp_server (safe_print: Callable[[str], None] = print, file_handler: FileHandlerType | None = None) -> socket.socket:
    # Tao socket TCP server, lang nghe tren state.my_tcp_port va khoi chayluong tcp_acceptor
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind(("0.0.0.0", state.my_tcp_port))
    server_sock.listen(10)
    threading.Thread (
        target = tcp_acceptor,
        args = (server_sock, safe_print, file_handler), 
        daemon = True,
    ).start ()
    return server_sock

def _resolve_target_peer (target: str) -> tuple[str|None, dict|None]:
    snapshot = state.get_online_peers_snapshot()
    if target.isdigit():
        idx = int(target) - 1
        if 0 <= idx < len(snapshot):
            return snapshot[idx]
        return (None, None)
    for name, info in snapshot:
        if name.lower() == target.lower():
            return (name, info)
    return (None, None)

def start_chat_with(target: str, safe_print: Callable[[str], None] = print, file_handler: FileHandlerType | None = None) -> bool:
    target_name, peer_info = _resolve_target_peer(target.strip())

    # Trường hợp peer vừa offline nhưng vẫn còn trong chat_conns hoặc chat_history
    if target_name is None:
        with state.state_lock:
            for existing_name in list(state.chat_conns.keys()) + list(state.chat_history.keys()):
                if existing_name.lower() == target.strip().lower():
                    target_name = existing_name
                    break

    if target_name is None:
        safe_print(f"[!] User '{target}' is not online.")
        return False

    if target_name == state.my_name:
        safe_print("[!] Cannot chat with yourself.")
        return False

    # Kiểm tra xem đã có sẵn kết nối chat chưa (Quy ước: tối đa 1 kết nối chat/cặp peer)
    existing_conn = state.get_chat_conn(target_name)
    if existing_conn is None:
        if peer_info is None:
            safe_print(f"[!] {target_name} is offline.")
            return False

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)  # Tránh treo lâu nếu peer vừa tắt máy chưa kịp hết timeout UDP
        try:
            sock.connect((peer_info["ip"], peer_info["tcp_port"]))
            sock.settimeout(None)
            with tcp_send_lock:
                sock.sendall(protocol.pack_hi(state.my_name, state.my_tcp_port))
        except OSError as exc:
            try:
                sock.close()
            except OSError:
                pass
            safe_print(f"[!] Failed to connect to {target_name}: {exc}")
            return False

        state.register_chat_conn(target_name, sock)
        threading.Thread(
            target=tcp_receiver,
            args=(sock, (peer_info["ip"], peer_info["tcp_port"]), safe_print, file_handler, target_name),
            daemon=True,
        ).start()
        safe_print(f"[*] Connected to {target_name} ({peer_info['ip']}:{peer_info['tcp_port']})")

    # Lấy số tin chưa đọc trước khi reset về 0
    with state.state_lock:
        unread = state.unread_counts.get(target_name, 0)
    history = state.switch_partner(target_name)

    if unread > 0:
        safe_print(f"[*] Switched to {target_name} — {unread} unread:")
        for line in history[-unread:]:
            safe_print(line)
    else:
        safe_print(f"[*] Switched to {target_name}")

    return True

def send_chat_message(text: str, safe_print: Callable[[str], None] = print) -> bool:
    with state.state_lock:
        partner = state.current_partner

    if partner is None:
        safe_print("[!] No active chat. Use /list and /chat <name|number> first.")
        return False

    conn = state.get_chat_conn(partner)
    if conn is None:
        safe_print(f"[!] No active connection with {partner}. Try /chat {partner} again.")
        return False

    packet = protocol.pack_message(text)
    try:
        with tcp_send_lock:
            conn.sendall(packet)
        state.append_own_msg(partner, text)
        safe_print(f"[{state.my_name}] {text}")
        return True
    except OSError:
        state.remove_chat_conn(partner)
        safe_print(f"[!] Connection to {partner} lost.")
        return False


def leave_chat(safe_print: Callable[[str], None] = print) -> None:
    with state.state_lock:
        partner = state.current_partner

    if partner is None:
        safe_print("[!] You are not in an active chat.")
        return

    conn = state.get_chat_conn(partner)
    if conn is not None:
        try:
            with tcp_send_lock:
                conn.sendall(protocol.pack_bye())
        except OSError:
            pass

    state.remove_chat_conn(partner)
    safe_print(f"[*] Chat with {partner} closed")


def close_all_chats() -> None:
    with state.state_lock:
        conns_snapshot = list(state.chat_conns.items())

    for peer_name, conn in conns_snapshot:
        try:
            with tcp_send_lock:
                conn.sendall(protocol.pack_bye())
        except OSError:
            pass
        state.remove_chat_conn(peer_name)

# test

if __name__ == "__main__":
    import sys

    print_lock = threading.Lock()

    def test_safe_print(msg: str) -> None:
        with print_lock:
            print(msg)

    # Cú pháp chạy: python3 chat_service.py <Tên_mình> <Cổng_TCP_mình> [Tên_bạn] [Cổng_TCP_bạn]
    state.my_name = sys.argv[1] if len(sys.argv) > 1 else "Chau"
    state.my_tcp_port = int(sys.argv[2]) if len(sys.argv) > 2 else 5001

    # Nạp sẵn 1 peer giả lập vào state.online_peers (vì chưa chạy udp_service)
    if len(sys.argv) >= 5:
        peer_name = sys.argv[3]
        peer_port = int(sys.argv[4])
        state.update_peer(peer_name, "127.0.0.1", peer_port, "online")

    # Khởi động TCP server (acceptor + receiver)
    server_sock = start_tcp_server(safe_print=test_safe_print)
    test_safe_print(f"[*] '{state.my_name}' dang lang nghe TCP tai cong {state.my_tcp_port}")
    test_safe_print("[*] Lenh thu nghiem: /list | /add <ten> <port> | /chat <ten|so> | /leave | /quit")

    try:
        while state.running:
            line = input("> ").strip()
            if not line:
                continue

            if line == "/list":
                peers = state.get_online_peers_snapshot()
                if not peers:
                    test_safe_print("[*] Danh sach online dang trong.")
                else:
                    for idx, (name, info) in enumerate(peers, start=1):
                        test_safe_print(f"  {idx}. {name} ({info['ip']}:{info['tcp_port']}) - {info['status']}")

            elif line.startswith("/add "):
                # Lenh phu de them nhanh peer thu 2, thu 3 vao state khi test 1 minh
                parts = line.split()
                if len(parts) == 3 and parts[2].isdigit():
                    state.update_peer(parts[1], "127.0.0.1", int(parts[2]), "online")
                    test_safe_print(f"[*] Da them {parts[1]} (127.0.0.1:{parts[2]}) vao danh sach.")
                else:
                    test_safe_print("[!] Cu phap: /add <ten> <tcp_port>")

            elif line.startswith("/chat "):
                target = line[6:].strip()
                if target:
                    start_chat_with(target, safe_print=test_safe_print)
                else:
                    test_safe_print("[!] Cu phap: /chat <ten|so>")

            elif line == "/leave":
                leave_chat(safe_print=test_safe_print)

            elif line == "/quit":
                state.running = False
                close_all_chats()
                server_sock.close()
                test_safe_print("[*] Da thoat chuong trinh.")
                break

            elif line.startswith("/"):
                test_safe_print("[!] Lenh khong hop le.")

            else:
                send_chat_message(line, safe_print=test_safe_print)

    except (KeyboardInterrupt, EOFError):
        state.running = False
        close_all_chats()
        server_sock.close()