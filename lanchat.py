#!/usr/bin/env python3
"""LAN Chat & File Sharing - peer-to-peer console chat (UDP discovery + TCP chat/files).

    python lanchat.py --name Quan
    python lanchat.py --name Minh --port 5001        # second instance on the same machine
"""

import argparse
import sys

import protocol
from discovery import Discovery
from node import ChatError, Node
from ui import ConsoleUI

HELP = """\
Commands:
  /list                  show online users (number, name, IP, status)
  /chat <name|number>    open a chat with a user (TCP connection)
  <any text>             send a message to the current chat partner
  /send <file>           send a file to the current chat partner
  /leave                 close the current chat
  /status <online|away>  change your status (sent via UDP)
  /help                  show this help
  /quit                  exit the program
(Start a message with // to send a text that begins with a slash.)"""


def parse_args():
    ap = argparse.ArgumentParser(description="LAN chat and file sharing over UDP/TCP sockets")
    ap.add_argument("--name", default=None, help="your display name (unique on the LAN)")
    ap.add_argument("--port", type=int, default=None,
                    help="TCP port to listen on (default: first free port from 5000)")
    ap.add_argument("--udp-port", type=int, default=protocol.UDP_PORT,
                    help=f"UDP port for discovery (default {protocol.UDP_PORT})")
    ap.add_argument("--downloads", default="downloads", help="folder for received files")
    ap.add_argument("--broadcast", action="append", default=[], metavar="ADDR",
                    help="extra broadcast address, e.g. 192.168.1.255 (can be repeated)")
    ap.add_argument("--throttle", type=int, default=0, metavar="KBPS",
                    help="limit file sending speed (KB/s) - handy to show progress in a demo")
    ap.add_argument("--plain", action="store_true",
                    help="plain line input without redraw (used by the automated tests)")
    return ap.parse_args()


def main():
    args = parse_args()
    if sys.platform != "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    name = args.name
    if name is None:
        name = input("Your name: ").strip()
    if not protocol.valid_name(name):
        sys.exit("Invalid name: use 1-32 printable characters, no '|', no leading/trailing spaces.")

    ui = ConsoleUI(interactive=False if args.plain else None)
    node = Node(name, ui, download_dir=args.downloads, throttle_kbps=args.throttle)
    try:
        tcp_port = node.listen(args.port)
    except OSError as e:
        sys.exit(f"Error: {e}")

    def on_discovery(kind, peer, info=""):
        if kind == "online":
            ui.print_line(f"[+] {peer.name} is online ({peer.ip}, {peer.status})")
        elif kind == "offline":
            ui.print_line(f"[-] {peer.name} went offline")
        elif kind == "status":
            ui.print_line(f"[*] {peer.name} is now {info}")
        elif kind == "error":
            ui.print_line(f"[!] {info}")

    disc = Discovery(name, tcp_port, udp_port=args.udp_port, on_event=on_discovery,
                     extra_targets=args.broadcast)
    node.attach(disc)

    ui.start()
    ui.print_line(f"[*] Listening on TCP port {tcp_port}")
    try:
        disc.start()
    except OSError as e:
        ui.stop()
        sys.exit(f"Error: {e}")
    node.start()
    ui.print_line(f"[*] Broadcasting presence on UDP port {args.udp_port}")
    ui.print_line("[*] Type /help for the list of commands.")

    try:
        run_commands(ui, node, disc, name)
    except KeyboardInterrupt:
        pass
    finally:
        node.shutdown()
        disc.stop()
        ui.print_line("[*] Bye.")
        ui.stop()


def run_commands(ui, node, disc, me):
    while True:
        line = ui.read_line()
        if line is None:                   # EOF / Ctrl-D
            return
        line = line.strip()
        if not line:
            continue
        if line.startswith("//"):          # escape: send text starting with '/'
            send_text(ui, node, me, line[1:])
            continue
        if not line.startswith("/"):
            send_text(ui, node, me, line)
            continue

        ui.print_line(f"> {line}")         # keep the command visible in the history
        cmd, _, arg = line.partition(" ")
        cmd, arg = cmd.lower(), arg.strip()
        try:
            if cmd == "/list":
                cmd_list(ui, node, disc)
            elif cmd == "/chat":
                if not arg:
                    raise ChatError("Usage: /chat <name|number>")
                node.open_chat(arg)
            elif cmd == "/send":
                if not arg:
                    raise ChatError("Usage: /send <file>")
                node.send_file(arg)
            elif cmd == "/leave":
                peer = node.leave()
                ui.print_line(f"[*] Left the chat with {peer}.")
            elif cmd == "/status":
                if arg.lower() not in protocol.STATUSES:
                    raise ChatError("Usage: /status <online|away>")
                disc.set_status(arg.lower())
                ui.print_line(f"[*] Your status is now {arg.lower()}.")
            elif cmd == "/help":
                ui.print_line(HELP)
            elif cmd in ("/quit", "/exit"):
                return
            else:
                ui.print_line(f"[!] Unknown command: {cmd}")
                ui.print_line(HELP)
        except ChatError as e:
            ui.print_line(f"[!] {e}")


def send_text(ui, node, me, text):
    try:
        node.send_message(text)
    except ChatError as e:
        ui.print_line(f"[!] {e}")
        return
    ui.print_line(f"[{me}] {text}")


def cmd_list(ui, node, disc):
    peers = disc.snapshot()
    if not peers:
        ui.print_line("[*] Nobody else is online.")
        return
    with node.lock:
        current = node.current
        unread = {k: len(v) for k, v in node.unread.items()}
    for i, p in enumerate(peers, 1):
        extra = ""
        if p.name == current:
            extra += "  <- chatting"
        if unread.get(p.name):
            extra += f"  ({unread[p.name]} unread)"
        ui.print_line(f"{i}. {p.name} ({p.ip}) {p.status}{extra}")


if __name__ == "__main__":
    main()
