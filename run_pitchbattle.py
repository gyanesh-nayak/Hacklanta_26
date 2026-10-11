import http.server
import socketserver
import os
import socket
import json
import subprocess
import threading
import time
import webbrowser
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from pitch_battle.gemini_commentary import (
    MAX_REQUEST_BYTES,
    CommentaryError,
    CommentaryRateLimit,
    MissingGeminiKey,
    generate_round_commentary,
)

PORT = 8000
DIRECTORY = os.path.dirname(os.path.abspath(__file__))

PUBLIC_TUNNEL_URL = None
ngrok_process = None

def get_lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip

LAN_IP = get_lan_ip()

def get_ngrok_url():
    """Return the HTTPS ngrok URL forwarding to this server's port."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=1) as response:
            tunnels = json.loads(response.read().decode("utf-8")).get("tunnels", [])
        for tunnel in tunnels:
            address = str(tunnel.get("config", {}).get("addr", ""))
            public_url = str(tunnel.get("public_url", ""))
            if address.endswith(f":{PORT}") and public_url.startswith("https://"):
                return public_url.rstrip("/")
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        pass
    return None

def run_tunnel():
    """Start or reuse ngrok so phones can securely reach the microphone page."""
    global PUBLIC_TUNNEL_URL, ngrok_process
    PUBLIC_TUNNEL_URL = get_ngrok_url()
    if PUBLIC_TUNNEL_URL:
        print(f"[TUNNEL] Using ngrok: {PUBLIC_TUNNEL_URL}", flush=True)
        return
    try:
        ngrok_process = subprocess.Popen(
            ["ngrok", "http", str(PORT), "--log=stdout"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
    except OSError as exc:
        print(f"[TUNNEL] Could not start ngrok ({exc}); using local Wi-Fi URL.", flush=True)
        return

    print("[TUNNEL] Starting ngrok HTTPS tunnel...", flush=True)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and ngrok_process.poll() is None:
        PUBLIC_TUNNEL_URL = get_ngrok_url()
        if PUBLIC_TUNNEL_URL:
            print("\n" + "=" * 62, flush=True)
            print(f"[TUNNEL READY] Public HTTPS URL: {PUBLIC_TUNNEL_URL}", flush=True)
            print("Mobile phones can scan the QR code on LTE or Wi-Fi.", flush=True)
            print("=" * 62 + "\n", flush=True)
            return
        time.sleep(0.25)

    if ngrok_process.poll() is None:
        print("[TUNNEL] ngrok did not become ready; using local Wi-Fi URL.", flush=True)
    else:
        print("[TUNNEL] ngrok exited; check its installation and authentication.", flush=True)
        ngrok_process = None

def stop_ngrok():
    global ngrok_process
    if ngrok_process and ngrok_process.poll() is None:
        ngrok_process.terminate()
        try:
            ngrok_process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            ngrok_process.kill()
            ngrok_process.wait()
    ngrok_process = None

class PitchBattleHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def _send_json(self, status_code, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/api/info", "/api/network-info", "/network-info"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            data = {
                "lan_ip": LAN_IP,
                "port": PORT,
                "lan_url": f"http://{LAN_IP}:{PORT}",
                "public_url": PUBLIC_TUNNEL_URL,
                "phone_url": f"{PUBLIC_TUNNEL_URL}/?phone=" if PUBLIC_TUNNEL_URL else f"http://{LAN_IP}:{PORT}/?phone=",
                "is_https": bool(PUBLIC_TUNNEL_URL),
            }
            self.wfile.write(json.dumps(data).encode("utf-8"))
            return
        super().do_GET()

    def do_POST(self):
        if urlsplit(self.path).path != "/api/battle-commentary":
            self.send_error(404)
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"detail": "Invalid content length."})
            return
        if content_length < 1 or content_length > MAX_REQUEST_BYTES:
            self._send_json(413, {"detail": "Round summary is missing or too large."})
            return

        try:
            summary = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"detail": "Invalid JSON round summary."})
            return

        try:
            commentary = generate_round_commentary(
                summary,
                client_id=self.client_address[0],
            )
        except ValueError as exc:
            self._send_json(400, {"detail": str(exc)})
            return
        except CommentaryError as exc:
            status_code = 503 if isinstance(exc, MissingGeminiKey) else (
                429 if isinstance(exc, CommentaryRateLimit) else 502
            )
            self._send_json(status_code, {"detail": str(exc)})
            return

        self._send_json(200, {"commentary": commentary})

if __name__ == "__main__":
    print("=" * 62)
    print("  PITCH BATTLE - STANDALONE SERVER")
    print(f"  Local URL:   http://localhost:{PORT}")
    print(f"  Wi-Fi IP:    http://{LAN_IP}:{PORT}")
    print("=" * 62)

    t = threading.Thread(target=run_tunnel, daemon=True)
    t.start()
    socketserver.TCPServer.allow_reuse_address = True

    try:
        with socketserver.TCPServer(("", PORT), PitchBattleHandler) as httpd:
            print(f"Serving HTTP on port {PORT}...")
            time.sleep(1)
            webbrowser.open(f"http://localhost:{PORT}")
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
        stop_ngrok()
    except Exception as e:
        print(f"Server error: {e}")
        stop_ngrok()
