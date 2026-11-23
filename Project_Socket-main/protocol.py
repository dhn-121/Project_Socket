from __future__ import annotations 
import socket

# DONG GOI DU LIEU ----------

# kieu bytes la kieu du lieu bieu dien cac byte nhi phan tho, moi phan tu la mot so nguyen
# tu 0 den 255 dung de luu tru va truyen tai du lieu giup may tinh hieu
# socket chi co the gui nhan theo kieu bytes thay vi string
def pack_hello (name: str, tcp_port: int, status: str) -> bytes:
    # Goi 1 (UDP Diem danh): HELLO|<name>|<tcp_port>|<status>
    return f"HELLO|{name}|{tcp_port}|{status}".encode("utf-8")

def pack_hi (name: str, tcp_port: int) -> bytes:
    # Goi 2 (TCP dinh danh - bat buoc gui dau tien ngay khi mo ket noi tcp):
    return f"HI|{name}|{tcp_port}|".encode("utf-8")

def pack_message (text: str) -> bytes:
    # Goi 3 (TCP tin nhan): MSG|<so_byte_utf_8>|<noi_dung_utf_8>
    payload = text.encode("utf-8")
    header = f"MSG|{len(payload)}|".encode("utf-8")
    return header + payload

def pack_file_header (filename: str, filesz: int) -> bytes:
    #Goi 4 (TCP truyen file): FILE|<ten>|<kich_thuoc>|<du_lieu_tho>
    return f"FILE|{filename}|{filesz}|".encode("utf-8")

def pack_bye () -> bytes:
    #Goi 5 {TCP roi cuoc chat}: BYE|
    return b"BYE|"

# GIAI MA & DOC LUONG TCP AN TOAN

def parse_hello (datagram: bytes) -> tuple [str, int, str] | None: 
    # Giai ma goi UDP Hello, tra ve (name, tcp_port, status) nếu hợp lệ, None nếu không
    try:
        text = datagram.decode("utf-8", errors="strict").strip()
        # Dich day byte nhan tu card mang ve lai kieu string theo bang ma utf-8
        # tham so errors dam bao ky tu phai la utf-8, neu khong se kich hoat loi ...
        #...UnicodeDecodeError de khoi except loai bo
        # strip() dung de loai bo cac ky tu thua o dau va cuoi chuoi (\n, \r, , \t)

        parts = text.split("|")
        if len(parts) != 4 or parts[0] != "HELLO":
            return None
        name = parts[1]
        tcp_port = int(parts[2])
        status = parts[3].strip()
        if not name or status not in ("online", "away"):
            return None
        return (name, tcp_port, status)
    except (UnicodeDecodeError, ValueError):
        return None

def recv_exact (sock: socket.socket, num_bytes: int) -> bytes:
    # Doc chinh xac num_bytes tu socket tcp
    if num_bytes < 0: raise ValueError ("Do dai (byte) < 0 (khong hop le)")
    buf = bytearray()
    while len(buf) < num_bytes:
        chunk = sock.recv(num_bytes - len(buf))
        if not chunk:
            raise ConnectionError ("Doi tac ngat ket noi truoc khi bytes duoc gui het") 
        buf.extend(chunk)
    return bytes(buf)

def recv_token (sock: socket.socket, max_len: int = 256) -> str:
    # Doc tung bye cua header TCP cho den khi gap '|'
    # Chan ky tu xuong dong hoac chuoi qua dai de tu choi goi rac 
    buf = bytearray()
    while True: 
        ch = sock.recv(1)
        if not ch:
            raise ConnectionError ("Doi tac ngat ket noi")
        if ch == b"|":
            return buf.decode("utf-8")
        if ch in (b"\r", b"\n") or len(buf) >= max_len:
            raise ValueError ("Loi header goi tin")
        buf.extend(ch)

def recv_hi (sock:socket.socket) -> tuple [str, int]:
    packet_type = recv_token(sock, max_len = 16)
    if packet_type != "HI": raise ValueError (f"Dang mong doi goi tin HI, nhung nhan duoc {packet_type!r}")
    peer_name = recv_token (sock, max_len = 64).strip()
    peer_port_str = recv_token(sock, max_len = 16).strip()
    if not peer_name: raise ValueError ("Khong co ten doi tac trong goi tin HI")
    return (peer_name, int(peer_port_str))