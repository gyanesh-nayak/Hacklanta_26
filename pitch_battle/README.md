# 🎤 PitchBattle - Unified Voice Arena & Multiplayer Karaoke

PitchBattle brings together:
1. **Song Library from V2 Offline ML Pipeline**: 5 songs with extracted pitch intervals, word alignment, and instrumental audio playback.
2. **Middleware Pairing Engine**: Real-time QR code generation, room code party system (`1234`), and instant Apple iOS Safari & Android Chrome pairing.
3. **Exact PitchBattle Canvas UI**:
   - Cyan hit line at `x = 150` with &plusmn;0.75 semitone tolerance badge.
   - Left-scrolling purple note capsules with lyric words inside (`Please`, `don't`, `take`, `my`, `sun-`, etc.).
   - Gold glowing active note at the hit line.
   - Live singer pitch cursors on the hit line (green when in tune, red when out of tune).
   - Pitch tuner gauge (`FLAT -50` / `IN TUNE` / `+50 SHARP`) and mic volume meter.
   - Bottom syllable karaoke strip and song timeline progress bar.
4. **Two Game Modes**:
   - 🍿 **Popcorn Singing**: Turn-based by phrase! The system detects lyric pauses and passes the mic between players automatically.
   - ⚔️ **Concurrent "Mog" Voice Battle**: Head-to-head multiplayer pitch duel. Both players sing simultaneously and out-mog each other on the live canvas.
5. **Scoreboard & Ranking Screen**:
   - Device names, winner crown banner, rankings (1st, 2nd, etc.).
   - Letter grades (**S**, **A**, **B**, **C**, **D**, **F**).
   - Pitch accuracy percentage, perfect hits, good hits, misses, and max combo streaks.

---

## 🚀 Quick Start

### 1. Launch the Server
Double-click `run_server.bat` or run:
```bash
cd pitch_battle
python app.py
```

The terminal will display your host URL and secure phone link once ngrok is ready:
```
=======================================================
🎤 PITCH BATTLE - Unified Arena
Host Screen:   http://localhost:8000/
Phone Mic URL: https://<your-ngrok-domain>/phone?room=1234
=======================================================
```

### 2. Open the Host Screen
Open your browser to:
[http://localhost:8000/](http://localhost:8000/)

### 3. Pair Phones (or Test Solo)
- **Multiplayer (iPhone & Android)**:
  - Point your phone's camera at the QR code on the laptop screen or open the HTTPS ngrok phone link shown there.
  - On the first visit, ngrok's free tier shows a confirmation page; tap **Visit Site** to continue to the mic interface.
  - Tap **Start Microphone** to stream your singing pitch.
- **Solo Mode (Laptop Mic)**:
  - Click **"Enable Laptop Mic (Solo Mode)"** directly on the host screen to test singing right from your laptop without needing a phone!

### 4. Select a Track & Battle!
Pick any song from the catalog:
1. **Happy** - Pharrell Williams (408 Notes | Medium | 233s)
2. **Love Story** - Taylor Swift (355 Notes | Medium | 235s)
3. **On & On** - Cartoon, Daniel Levi (291 Notes | Hard | 208s)
4. **Alphabet Song** (152 Notes | Easy | 115s)
5. **Twinkle Twinkle Little Star** (17 Notes | Beginner | 19s)

---

## 📂 Project Structure

```
pitch_battle/
├── app.py              # FastAPI server, WebSockets, QR code generator, audio streaming
├── scorer.py           # Scoring engine (octave folding, ±0.75 semitones tolerance, letter grades)
├── run_server.bat      # One-click Windows launcher
├── songs/              # V2 offline ML extracted songs
│   ├── index.json
│   ├── happy_pharrellwilliams.mp3 / .json / .txt
│   ├── love_story_taylorswift.mp3 / .json / .txt
│   ├── on_and_on_cartoon.mp3 / .json / .txt
│   ├── alphabet_song.mp3 / .json / .txt
│   └── twinkle_twinkle.wav / .json / .txt
└── static/
    ├── index.html      # Host screen (Lobby, Song Select, Battle Canvas, Scoreboard)
    ├── style.css       # PitchBattle midnight styling matching screenshot
    ├── game.js         # 60 FPS HTML5 canvas engine & synchronization
    ├── phone.html      # Mobile microphone web client
    └── phone.js        # Web Audio API autocorrelation pitch detector & streaming
```
