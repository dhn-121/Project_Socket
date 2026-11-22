from collections.abc import Callable
import socket
import sys
import threading
import time

my_status = "online"
status_lock = threading.Lock()
send_lock = threading.Lock()
peers = {}
peers_lock = threading.Lock()


# udp = socket.socket(family=socket.AF_INET, type=socket.SOCK_DGRAM)
# udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
# if hasattr(socket, "SO_REUSEPORT"):
#     udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
# udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
# udp.bind(("", 9999))


def build_hello(my_name: str, my_tcp_port: int, status: str) -> bytes:
    msg = f"HELLO|{my_name}|{my_tcp_port}|{status}"
    return msg.encode("utf-8")


def send_hello_now(
    udp_sock: socket.socket,
    bcast_ip: str,
    udp_port: int,
    my_name: str,
    my_tcp_port: int,
) -> None:
    with status_lock:
        current_status = my_status
    message = f"HELLO|{my_name}|{my_tcp_port}|{current_status}"
    try:
        with send_lock:
            udp_sock.sendto(message.encode("utf-8"), (bcast_ip, udp_port))
    except OSError:
        pass


def set_my_status(
    new_status: str,
    udp_sock: socket.socket,
    bcast_ip: str,
    udp_port: int,
    my_name: str,
    my_tcp_port: int,
) -> None:
    global my_status
    with status_lock:
        my_status = new_status
    send_hello_now(udp_sock, bcast_ip, udp_port, my_name, my_tcp_port)


def udp_broadcaster(
    udp_sock: socket.socket,
    bcast_ip: str,
    udp_port: int,
    my_name: str,
    my_tcp_port: int,
    stop: threading.Event,
) -> None:
    # Gửi HELLO sau mỗi 3s
    while not stop.is_set():
        send_hello_now(udp_sock, bcast_ip, udp_port, my_name, my_tcp_port)
        stop.wait(3.0)


def udp_listener(udp_sock: socket.socket, my_name: str, stop: threading.Event, safe_print: Callable[[str], None] = print) -> None:
    # lắng nghe gói HELLO từ các máy khác trong mạng
    udp_sock.settimeout(1.0)
    while not stop.is_set():
        try:
            data, addr = udp_sock.recvfrom(1024)
        except socket.timeout:
            continue
        except OSError:
            break
        text = data.decode("utf-8", errors="replace").strip()
        parts = text.split("|")
        if len(parts) != 4 or parts[0] != "HELLO":
            continue
        _, peer_name, tcp_port_str, peer_status = parts
        if peer_name == my_name:
            continue
        try:
            peer_tcp = int(tcp_port_str)
        except ValueError:
            continue
        peer_ip = addr[0]
        with peers_lock:
            is_new = peer_name not in peers
            peers[peer_name] = {
                "ip": peer_ip,
                "tcp_port": peer_tcp,
                "status": peer_status,
                "last_seen": time.time(),
            }
        if is_new:
            print(f"\n{peer_name} ({peer_ip}: {peer_tcp}) vừa online ({peer_status}).")


def peers_timeout_check(stop: threading.Event, timeout: float = 10.0, safe_print: Callable[[str], None] = print) -> None:
    while not stop.is_set():
        now = time.time()
        offline_names = []
        with peers_lock:
            for name, info in list(peers.items()):
                if now - info["last_seen"] > timeout:
                    offline_names.append(name)
                    del peers[name]
        for name in offline_names:
            print(f"\n{name} đã offline (mất tín hiệu > {int(timeout)} s.)")
        stop.wait(1.0)

# main để test

# if __name__ == "__main__":
#     # Truyền tên và tcp_port từ dòng lệnh, ví dụ: python udp_service.py Ha 5001
#     my_name = sys.argv[1] if len(sys.argv) > 1 else "User1"
#     my_tcp_port = int(sys.argv[2]) if len(sys.argv) > 2 else 5001
#     udp_port = 9999
#     bcast_ip = "127.255.255.255"  # Địa chỉ broadcast nội bộ để test 1 mình trên 1 máy

#     # 1. Tạo UDP socket và cấu hình cho phép broadcast + dùng chung cổng trên Windows
#     udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
#     udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
#     udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
#     udp_sock.bind(("0.0.0.0", udp_port))

#     stop_event = threading.Event()

#     # 2. Khởi chạy 3 luồng ngầm
#     threading.Thread(
#         target=udp_broadcaster,
#         args=(udp_sock, bcast_ip, udp_port, my_name, my_tcp_port, stop_event),
#         daemon=True,
#     ).start()
#     threading.Thread(
#         target=udp_listener, args=(udp_sock, my_name, stop_event), daemon=True
#     ).start()
#     threading.Thread(
#         target=peers_timeout_check, args=(stop_event, 10.0), daemon=True
#     ).start()

#     print(
#         f"Đang chạy '{my_name}' (TCP port {my_tcp_port}). Gõ: list | away | online | quit"
#     )

#     # 3. Vòng lặp nhập lệnh test ở luồng chính
#     while True:
#         cmd = input("> ").strip().lower()
#         if cmd == "list":
#             with peers_lock:
#                 print("Danh sách online:", peers)
#         elif cmd in ("away", "online"):
#             with status_lock:
#                 my_status = cmd
#             send_hello_now(udp_sock, bcast_ip, udp_port, my_name, my_tcp_port)
#             print(f"Đã đổi trạng thái thành {cmd} và bắn HELLO ngay!")
#         elif cmd == "quit":
#             stop_event.set()
#             udp_sock.close()
#             break
