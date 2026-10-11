"""
PitchBattle Unified FastAPI Server
Integrates:
- Local Wi-Fi QR code pairing (phones and host on the same network)
- Room/Party multi-device management (2+ players)
- 2 Game Modes: 'popcorn' (turn-based by phrase) and 'mog_battle' (concurrent vocal duel)
- Audio streaming for laptop speaker playback
- Real-time scoring and summary screens
"""

import asyncio
import io
import json
import math
import os
import pathlib
import re
import socket
import subprocess
import time
import urllib.error
import urllib.request
from typing import Dict, List, Optional

import qrcode
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

if __package__:
    from .scorer import SongScorer, PlayerScoreTracker
    from .gemini_commentary import (
        MAX_REQUEST_BYTES,
        CommentaryError,
        CommentaryRateLimit,
        MissingGeminiKey,
        generate_round_commentary,
    )
else:
    from scorer import SongScorer, PlayerScoreTracker
    from gemini_commentary import (
        MAX_REQUEST_BYTES,
        CommentaryError,
        CommentaryRateLimit,
        MissingGeminiKey,
        generate_round_commentary,
    )

BASE_DIR = pathlib.Path(__file__).resolve().parent
SONGS_DIR = BASE_DIR / "songs"
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="OpenLight Karaoke Arena")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/songs", StaticFiles(directory=str(BASE_DIR.parent / "songs")), name="standalone-songs")

PLAYER_COLORS = [
    "#00dfd8",  # Cyan
    "#ff0080",  # Magenta / Pink
    "#f5a623",  # Amber
    "#9d4edd",  # Purple
    "#00f279",  # Green
    "#ff4757",  # Red
]
NO_SINGING_KICK_SECONDS = 5.0

PUBLIC_TUNNEL_URL: Optional[str] = None
_ngrok_process: Optional[subprocess.Popen] = None


def _ngrok_tunnel_url() -> Optional[str]:
    """Return the HTTPS ngrok URL forwarding to the local game server, if any."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=1) as response:
            tunnels = json.loads(response.read().decode("utf-8")).get("tunnels", [])
        for tunnel in tunnels:
            address = str(tunnel.get("config", {}).get("addr", ""))
            public_url = str(tunnel.get("public_url", ""))
            if address.endswith(":8000") and public_url.startswith("https://"):
                return public_url.rstrip("/")
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        pass
    return None


@app.on_event("startup")
async def start_ngrok_tunnel():
    """Start or reuse ngrok so phones can securely reach the microphone page."""
    global PUBLIC_TUNNEL_URL, _ngrok_process

    # Cloud hosts provide HTTPS and a public hostname directly; ngrok is only
    # useful for the local desktop launcher.
    if os.environ.get("RENDER") or os.environ.get("VERCEL"):
        print("[PITCH BATTLE] Running on a hosted HTTPS platform; using its public URL.")
        return

    PUBLIC_TUNNEL_URL = _ngrok_tunnel_url()
    if PUBLIC_TUNNEL_URL:
        print(f"[PITCH BATTLE] Using ngrok: {PUBLIC_TUNNEL_URL}")
        return

    try:
        _ngrok_process = subprocess.Popen(
            ["ngrok", "http", "8000", "--log=stdout"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
    except OSError as exc:
        print(f"[PITCH BATTLE] Could not start ngrok ({exc}); using local Wi-Fi URL.")
        return

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and _ngrok_process.poll() is None:
        PUBLIC_TUNNEL_URL = _ngrok_tunnel_url()
        if PUBLIC_TUNNEL_URL:
            print(f"[PITCH BATTLE] ngrok ready: {PUBLIC_TUNNEL_URL}")
            return
        await asyncio.sleep(0.25)

    if _ngrok_process.poll() is None:
        print("[PITCH BATTLE] ngrok did not become ready; using local Wi-Fi URL.")
    else:
        print("[PITCH BATTLE] ngrok exited before connecting; check `ngrok config check` and authentication.")
        _ngrok_process = None


@app.on_event("shutdown")
async def stop_ngrok_tunnel():
    global _ngrok_process
    if _ngrok_process and _ngrok_process.poll() is None:
        _ngrok_process.terminate()
        try:
            _ngrok_process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            _ngrok_process.kill()
            _ngrok_process.wait()
    _ngrok_process = None


def get_local_ip() -> str:
    """Finds machine LAN IP for mobile QR code pairing."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


class BattleRoom:
    def __init__(self, room_id: str):
        self.room_id = room_id
        self.host_socket: Optional[WebSocket] = None
        self.players: Dict[str, WebSocket] = {}
        self.trackers: Dict[str, PlayerScoreTracker] = {}
        self.current_song_data: Optional[Dict] = None
        self.scorer: Optional[SongScorer] = None
        self.is_playing: bool = False
        self.game_mode: str = "mog_battle"  # 'popcorn' or 'mog_battle'
        self.active_popcorn_index: int = 0
        self.popcorn_phrase_count: int = 0
        self.current_audio_time: float = 0.0
        self.start_epoch: Optional[float] = None
        self.split_time: float = 0.0
        self.half1_end_time: float = 0.0
        self.half2_start_time: float = 0.0
        self.no_voice_since: Dict[str, float] = {}

    def load_song(self, song_id: str, game_mode: str = "mog_battle"):
        self.game_mode = game_mode
        self.current_audio_time = 0.0
        self.active_popcorn_index = 0
        self.no_voice_since.clear()
        json_path = SONGS_DIR / f"{song_id}.json"
        if not json_path.exists():
            raise FileNotFoundError(f"Song {song_id} not found")

        with open(json_path, "r", encoding="utf-8") as f:
            self.current_song_data = json.load(f)

        notes = self.current_song_data.get("notes", [])
        total_notes = len(notes)

        # Calculate exact half-song split for Popcorn mode (Note-balanced)
        if total_notes >= 2:
            half_idx = total_notes // 2
            h1_last = notes[half_idx - 1]
            h2_first = notes[half_idx]
            self.half1_end_time = float(h1_last["end"])
            self.half2_start_time = float(h2_first["start"])
            self.split_time = (self.half1_end_time + self.half2_start_time) / 2.0
            
            # Tag each note with its designated player half (0 for User 1, 1 for User 2)
            for idx, n in enumerate(notes):
                n["half_idx"] = 0 if idx < half_idx else 1
        elif total_notes == 1:
            self.half1_end_time = float(notes[0]["end"])
            self.half2_start_time = float(notes[0]["end"])
            self.split_time = float(notes[0]["end"])
            notes[0]["half_idx"] = 0
        else:
            self.split_time = 0.0
            self.half1_end_time = 0.0
            self.half2_start_time = 0.0

        self.scorer = SongScorer(notes)

        # Reset player trackers with distinct player colors
        self.trackers.clear()
        for idx, name in enumerate(self.players.keys()):
            color = PLAYER_COLORS[idx % len(PLAYER_COLORS)]
            self.trackers[name] = PlayerScoreTracker(name, self.scorer, color)

    def get_popcorn_state(self, current_time: float) -> Dict:
        """Returns active player, turn index, countdowns, and transition metadata."""
        player_names = list(self.players.keys())
        player_count = len(player_names)

        # Solo mode: Player 1 sings entire song
        if player_count <= 1:
            active_name = player_names[0] if player_names else None
            return {
                "active_player": active_name,
                "player_count": player_count,
                "active_half": 0,
                "user1_name": active_name,
                "user2_name": None,
                "split_time": self.split_time,
                "is_transition_window": False,
                "countdown_seconds": None,
                "turn_ending_player": None,
                "turn_starting_player": None,
            }

        # 2+ Players: User 1 does first half, User 2 does second half
        user1 = player_names[0]
        user2 = player_names[1]

        # Transition threshold is the split_time
        is_second_half = current_time >= self.split_time
        active_name = user2 if is_second_half else user1
        active_half = 1 if is_second_half else 0

        # Countdown calculation: 3...2...1 warning prompt before half-way switch
        # Triggers in the 3.5 seconds leading up to split_time
        time_to_switch = self.split_time - current_time
        countdown_secs = None
        is_transition = False

        if 0 < time_to_switch <= 3.5:
            is_transition = True
            countdown_secs = int(math.ceil(time_to_switch))
            if countdown_secs > 3:
                countdown_secs = 3
            if countdown_secs < 1:
                countdown_secs = 1

        return {
            "active_player": active_name,
            "player_count": player_count,
            "active_half": active_half,
            "user1_name": user1,
            "user2_name": user2,
            "split_time": self.split_time,
            "half1_end": self.half1_end_time,
            "half2_start": self.half2_start_time,
            "is_transition_window": is_transition,
            "countdown_seconds": countdown_secs,
            "turn_ending_player": user1 if is_transition else None,
            "turn_starting_player": user2 if is_transition else None,
        }

    def get_active_popcorn_player(self) -> Optional[str]:
        state_info = self.get_popcorn_state(self.current_audio_time)
        return state_info["active_player"]


ROOMS: Dict[str, BattleRoom] = {}


def get_room(room_id: str) -> BattleRoom:
    rid = room_id.upper()
    if rid not in ROOMS:
        ROOMS[rid] = BattleRoom(rid)
    return ROOMS[rid]


# ==========================================
# HTTP ENDPOINTS
# ==========================================
@app.get("/healthz")
async def health_check():
    return {"status": "ok"}


@app.get("/")
async def root():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/phone")
async def phone_page():
    return FileResponse(STATIC_DIR / "phone.html")


@app.get("/api/info")
async def get_server_info(request: Request):
    host_header = request.headers.get("host", "localhost:8000")
    port = host_header.split(":")[1] if ":" in host_header else "8000"
    lan_ip = get_local_ip()
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
    protocol = forwarded_proto or request.url.scheme
    safe_public_host = bool(re.fullmatch(r"[A-Za-z0-9.-]+(?::\d{1,5})?", host_header))
    public_url = PUBLIC_TUNNEL_URL
    if not public_url and safe_public_host and protocol == "https":
        public_url = f"https://{host_header}"
    phone_url = f"{public_url}/phone" if public_url else f"http://{lan_ip}:{port}/phone"
    return {
        "lan_ip": lan_ip,
        "port": port,
        "public_url": public_url,
        "phone_url": phone_url,
        "is_https": bool(PUBLIC_TUNNEL_URL),
    }


@app.get("/api/songs")
async def get_songs():
    index_file = SONGS_DIR / "index.json"
    if index_file.exists():
        with open(index_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


@app.get("/api/song/{song_id}")
async def get_song_json(song_id: str):
    filename = song_id if song_id.endswith(".json") else f"{song_id}.json"
    path = SONGS_DIR / filename
    if path.exists():
        return FileResponse(path, media_type="application/json")
    raise HTTPException(status_code=404, detail="Song not found")


@app.get("/api/audio/{filename}")
@app.head("/api/audio/{filename}")
async def get_audio_file(filename: str):
    path = SONGS_DIR / filename
    if path.exists():
        media = "audio/mpeg" if path.suffix == ".mp3" else "audio/wav"
        return FileResponse(path, media_type=media)
    raise HTTPException(status_code=404, detail="Audio not found")


@app.post("/api/battle-commentary")
async def get_battle_commentary(request: Request):
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_REQUEST_BYTES:
                raise HTTPException(status_code=413, detail="Round summary is too large.")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid content length.") from None

    try:
        summary = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Invalid JSON round summary.") from None

    client_id = request.client.host if request.client else "unknown"
    try:
        commentary = await asyncio.to_thread(
            generate_round_commentary,
            summary,
            client_id=client_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except CommentaryError as exc:
        status_code = 503 if isinstance(exc, MissingGeminiKey) else (
            429 if isinstance(exc, CommentaryRateLimit) else 502
        )
        raise HTTPException(status_code=status_code, detail=str(exc)) from None

    return JSONResponse({"commentary": commentary})


@app.get("/api/qr/{room_id}")
async def get_room_qr(request: Request, room_id: str):
    if PUBLIC_TUNNEL_URL:
        url = f"{PUBLIC_TUNNEL_URL}/phone?room={room_id.upper()}"
    else:
        host_header = request.headers.get("host", "localhost:8000")
        if "localhost" in host_header or "127.0.0.1" in host_header:
            port = host_header.split(":")[1] if ":" in host_header else "8000"
            host = f"{get_local_ip()}:{port}"
        else:
            host = host_header
        forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
        protocol = "https" if forwarded_proto == "https" or request.url.scheme == "https" or "ngrok" in host else "http"
        url = f"{protocol}://{host}/phone?room={room_id.upper()}"

    qr = qrcode.QRCode(box_size=7, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#00dfd8", back_color="#070b18")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")


@app.post("/api/room/{room_id}/remove_player")
async def remove_player_endpoint(room_id: str, device_name: str):
    room = get_room(room_id)
    p_ws = room.players.pop(device_name, None)
    room.trackers.pop(device_name, None)
    room.no_voice_since.pop(device_name, None)
    if p_ws:
        try:
            await p_ws.send_json({"type": "kicked", "reason": "Removed by host"})
            await p_ws.close()
        except Exception:
            pass
    if room.host_socket:
        try:
            await room.host_socket.send_json({
                "type": "device_disconnected",
                "device_name": device_name,
                "players": list(room.players.keys()),
            })
        except Exception:
            pass
    return {"status": "ok", "removed": device_name, "remaining": list(room.players.keys())}


@app.post("/api/room/{room_id}/clear_players")
async def clear_players_endpoint(room_id: str):
    room = get_room(room_id)
    for p_name, p_ws in list(room.players.items()):
        try:
            await p_ws.send_json({"type": "kicked", "reason": "Cleared by host"})
            await p_ws.close()
        except Exception:
            pass
    room.players.clear()
    room.trackers.clear()
    if room.host_socket:
        try:
            await room.host_socket.send_json({
                "type": "players_update",
                "players": [],
            })
        except Exception:
            pass
    return {"status": "ok", "remaining": []}


# ==========================================
# WEBSOCKETS (HOST & PHONES)
# ==========================================
@app.websocket("/ws/host/{room_id}")
async def host_ws_endpoint(ws: WebSocket, room_id: str):
    await ws.accept()
    room = get_room(room_id)
    room.host_socket = ws

    # Send initial room state
    await ws.send_json({
        "type": "host_connected",
        "room_id": room.room_id,
        "players": list(room.players.keys()),
        "lan_ip": get_local_ip(),
        "public_url": PUBLIC_TUNNEL_URL,
    })

    ticker_task = asyncio.create_task(run_host_ticker(room, ws))

    try:
        while True:
            msg = await ws.receive_json()
            mtype = msg.get("type")

            if mtype == "time_update":
                room.current_audio_time = float(msg.get("currentTime", 0.0))

            elif mtype == "start_song":
                song_id = msg.get("song_id")
                mode = msg.get("mode", "mog_battle")
                room.load_song(song_id, mode)
                room.is_playing = True
                room.start_epoch = time.time()

                start_payload = {
                    "type": "game_started",
                    "song_id": song_id,
                    "mode": mode,
                    "song_data": room.current_song_data,
                    "players": list(room.players.keys()),
                }

                # Notify all connected phones
                for p_name, p_ws in list(room.players.items()):
                    try:
                        await p_ws.send_json(start_payload)
                    except Exception:
                        pass

            elif mtype == "host_pitch_frame" and room.is_playing:
                # Host laptop mic input
                d_name = msg.get("device_name", "Host Laptop")
                if d_name not in room.trackers:
                    room.trackers[d_name] = PlayerScoreTracker(d_name, room.scorer or SongScorer([]), "#00dfd8")
                tracker = room.trackers[d_name]
                t = room.current_audio_time
                hz = float(msg.get("hz", 0.0))
                midi = float(msg.get("midi")) if msg.get("midi") is not None else None
                conf = float(msg.get("confidence", 0.0))
                rms = float(msg.get("rms", 0.0))
                tracker.evaluate(t, hz, midi, conf, rms)

            elif mtype == "stop_song" or mtype == "song_ended":
                room.is_playing = False
                room.start_epoch = None

                # Compile final scoreboard for all devices
                scoreboard = [tracker.get_summary() for tracker in room.trackers.values()]
                scoreboard.sort(key=lambda x: x["total_score"], reverse=True)

                end_payload = {
                    "type": "game_over",
                    "scoreboard": scoreboard,
                    "winner": scoreboard[0]["device_name"] if scoreboard else None,
                }

                await ws.send_json(end_payload)
                for p_ws in list(room.players.values()):
                    try:
                        await p_ws.send_json(end_payload)
                    except Exception:
                        pass

            elif mtype == "remove_player":
                target_name = msg.get("device_name")
                if target_name:
                    p_ws = room.players.pop(target_name, None)
                    room.trackers.pop(target_name, None)
                    room.no_voice_since.pop(target_name, None)
                    if p_ws:
                        try:
                            await p_ws.send_json({"type": "kicked", "reason": "Removed by host"})
                            await p_ws.close()
                        except Exception:
                            pass
                    await ws.send_json({
                        "type": "device_disconnected",
                        "device_name": target_name,
                        "players": list(room.players.keys()),
                    })

            elif mtype == "remove_all_players":
                for p_name, p_ws in list(room.players.items()):
                    try:
                        await p_ws.send_json({"type": "kicked", "reason": "Cleared by host"})
                        await p_ws.close()
                    except Exception:
                        pass
                room.players.clear()
                room.trackers.clear()
                room.no_voice_since.clear()
                await ws.send_json({
                    "type": "players_update",
                    "players": [],
                })

    except WebSocketDisconnect:
        pass
    finally:
        ticker_task.cancel()
        if room.host_socket == ws:
            room.host_socket = None


async def run_host_ticker(room: BattleRoom, ws: WebSocket):
    """Streams live player positions, color markers, scores, and popcorn transition countdowns to host at 20 FPS."""
    try:
        while True:
            await asyncio.sleep(0.05)  # 20 FPS
            if room.is_playing and room.host_socket:
                player_states = []
                popcorn_state = room.get_popcorn_state(room.current_audio_time)
                active_popcorn = popcorn_state["active_player"]
                active_note = room.scorer.find_active_note(room.current_audio_time) if room.scorer else None
                now = time.monotonic()

                # Only count inactivity while the player is expected to sing a charted note.
                # Instrumental gaps and the other player's Popcorn turn never count.
                for name, player_ws in list(room.players.items()):
                    is_active_turn = (
                        name == active_popcorn
                        if room.game_mode == "popcorn" and popcorn_state["player_count"] > 1
                        else True
                    )
                    tracker = room.trackers.get(name)
                    if not active_note or not is_active_turn:
                        room.no_voice_since.pop(name, None)
                        continue

                    has_recent_voice = bool(
                        tracker
                        and tracker.is_singing
                        and tracker.last_frame_at is not None
                        and now - tracker.last_frame_at < 0.5
                    )
                    if has_recent_voice:
                        room.no_voice_since.pop(name, None)
                        continue

                    idle_since = room.no_voice_since.setdefault(name, now)
                    if now - idle_since < NO_SINGING_KICK_SECONDS:
                        continue

                    room.no_voice_since.pop(name, None)
                    room.players.pop(name, None)
                    room.trackers.pop(name, None)
                    try:
                        await player_ws.send_json({
                            "type": "kicked",
                            "reason": "No singing detected for 5 seconds during your active turn.",
                        })
                        await player_ws.close()
                    except Exception:
                        pass
                    try:
                        await ws.send_json({
                            "type": "device_disconnected",
                            "device_name": name,
                            "players": list(room.players.keys()),
                        })
                    except Exception:
                        pass

                for name, tracker in room.trackers.items():
                    player_states.append({
                        "device_name": name,
                        "color": tracker.player_color,
                        "is_singing": tracker.is_singing,
                        "rms": tracker.last_rms,
                        "mic_signal_received": (
                            tracker.last_frame_at is not None
                            and time.monotonic() - tracker.last_frame_at < 1.0
                        ),
                        "is_in_tune": tracker.is_in_tune,
                        "sung_midi": tracker.last_sung_midi,
                        "target_midi": tracker.last_target_midi,
                        "semitone_diff": tracker.last_semitone_diff,
                        "score": tracker.total_score,
                        "streak": tracker.streak,
                        "current_word": tracker.current_lyric_word,
                        "is_active_turn": (name == active_popcorn) if room.game_mode == "popcorn" else True,
                    })

                await ws.send_json({
                    "type": "live_battle_update",
                    "game_mode": room.game_mode,
                    "currentTime": room.current_audio_time,
                    "active_popcorn_player": active_popcorn,
                    "popcorn_state": popcorn_state,
                    "players": player_states,
                })
    except asyncio.CancelledError:
        pass


@app.websocket("/ws/player/{room_id}/{device_name}")
async def player_ws_endpoint(ws: WebSocket, room_id: str, device_name: str):
    await ws.accept()
    room = get_room(room_id)

    # Clean up any previous connection with the same name
    if device_name in room.players:
        old_ws = room.players.pop(device_name, None)
        if old_ws:
            try:
                await old_ws.close()
            except Exception:
                pass

    room.players[device_name] = ws
    room.no_voice_since.pop(device_name, None)

    # Preserve existing tracker on reconnection so score is not lost
    if device_name in room.trackers:
        color = room.trackers[device_name].player_color
    else:
        color = PLAYER_COLORS[len(room.trackers) % len(PLAYER_COLORS)]
        room.trackers[device_name] = PlayerScoreTracker(device_name, room.scorer or SongScorer([]), color)

    # Confirm connection
    await ws.send_json({
        "type": "player_joined",
        "device_name": device_name,
        "color": color,
        "is_playing": room.is_playing,
        "game_mode": room.game_mode,
    })

    # Notify host that a new player joined
    if room.host_socket:
        await room.host_socket.send_json({
            "type": "device_paired",
            "device_name": device_name,
            "players": list(room.players.keys()),
        })

    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("type") == "pitch_frame" and room.is_playing:
                eval_t = room.current_audio_time if room.current_audio_time > 0 else float(msg.get("t", 0.0))
                hz = float(msg.get("hz", 0.0))
                midi = float(msg.get("midi")) if msg.get("midi") is not None else None
                conf = float(msg.get("confidence", 0.0))
                rms = float(msg.get("rms", 0.0))

                tracker = room.trackers.get(device_name)
                if tracker:
                    # In popcorn mode: User 1 sings 1st half, User 2 sings 2nd half.
                    # If only 1 player, Player 1 sings the entire song.
                    popcorn_info = room.get_popcorn_state(eval_t)
                    active_player_name = popcorn_info["active_player"]
                    player_count = popcorn_info["player_count"]

                    is_my_turn = (active_player_name == device_name) if (room.game_mode == "popcorn" and player_count > 1) else True

                    if is_my_turn:
                        eval_res = tracker.evaluate(eval_t, hz, midi, conf, rms)
                    else:
                        eval_res = {
                            "is_in_tune": False,
                            "rating": "WAITING",
                            "score": tracker.total_score,
                            "streak": tracker.streak,
                            "cents_off": 0.0,
                            "sung_midi": None,
                            "target_midi": None,
                            "active_note": None,
                        }

                    # Determine countdown status for this user
                    # If this user's turn is ending soon, countdown_type = "ending"
                    # If this user's turn is starting soon, countdown_type = "starting"
                    countdown_secs = popcorn_info.get("countdown_seconds")
                    countdown_type = None
                    if popcorn_info.get("is_transition_window"):
                        if device_name == popcorn_info.get("turn_ending_player"):
                            countdown_type = "ending"
                        elif device_name == popcorn_info.get("turn_starting_player"):
                            countdown_type = "starting"

                    # Send feedback directly to phone (never freeze tuner)
                    await ws.send_json({
                        "type": "score_feedback",
                        "game_mode": room.game_mode,
                        "is_my_turn": is_my_turn,
                        "is_in_tune": eval_res["is_in_tune"] if is_my_turn else False,
                        "rating": eval_res.get("rating", "") if is_my_turn else "LISTENING",
                        "score": eval_res["score"],
                        "streak": eval_res["streak"],
                        "cents_off": eval_res.get("cents_off", 0.0),
                        "sung_midi": eval_res.get("sung_midi"),
                        "target_midi": eval_res.get("target_midi"),
                        "active_word": eval_res.get("active_note", {}).get("text", "") if eval_res.get("active_note") else "",
                        "active_player": active_player_name,
                        "user1_name": popcorn_info.get("user1_name"),
                        "user2_name": popcorn_info.get("user2_name"),
                        "active_half": popcorn_info.get("active_half", 0),
                        "is_transition_window": popcorn_info.get("is_transition_window", False),
                        "countdown_seconds": countdown_secs,
                        "countdown_type": countdown_type,
                    })

    except WebSocketDisconnect:
        pass
    finally:
        # Avoid race condition: only clean up if this ws is still the registered connection
        if room.players.get(device_name) == ws:
            room.players.pop(device_name, None)
            room.no_voice_since.pop(device_name, None)
            if not room.is_playing:
                room.trackers.pop(device_name, None)
            if room.host_socket:
                try:
                    await room.host_socket.send_json({
                        "type": "device_disconnected",
                        "device_name": device_name,
                        "players": list(room.players.keys()),
                    })
                except Exception:
                    pass


if __name__ == "__main__":
    import uvicorn
    ip = get_local_ip()
    print("\n=======================================================")
    print("[PITCH BATTLE] Unified Arena")
    print(f"Host Screen:   http://localhost:8000/ or http://{ip}:8000/")
    print("Phone Mic URL: ngrok HTTPS link will appear after startup")
    print("=======================================================\n")
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
