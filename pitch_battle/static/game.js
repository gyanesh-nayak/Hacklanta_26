/**
 * PitchBattle Game Engine & Canvas Renderer
 * Renders the 60 FPS battle canvas matching user's screenshot
 * Synchronizes with FastAPI backend and mobile WebSockets
 */

// App State
const state = {
  roomId: "1234",
  mode: "mog_battle", // 'mog_battle' or 'popcorn'
  songs: [],
  selectedSong: null,
  songData: null,
  ws: null,
  connectedPlayers: [],
  livePlayerStates: [],
  popcornState: null, // Popcorn turn & countdown metadata
  isLaptopMicActive: false,
  audioContext: null,
  analyser: null,
  micStream: null,
  particles: [],
  pxPerSec: 160, // Scroll speed
  hitLineX: 150, // Matches screenshot hitline position
};

// Canvas references
const canvas = document.getElementById("battle-canvas");
const ctx = canvas.getContext("2d");
const audioElement = document.getElementById("bg-audio");

// Note Names
const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
function midiToNoteName(midi) {
  if (!midi) return "--";
  const rounded = Math.round(midi);
  return `${NOTE_NAMES[rounded % 12]}${Math.floor(rounded / 12) - 1}`;
}

// ==========================================
// INITIALIZATION
// ==========================================
window.addEventListener("DOMContentLoaded", async () => {
  setupCanvasSize();
  window.addEventListener("resize", setupCanvasSize);

  setupUIEventListeners();
  await loadServerInfo();
  await loadSongCatalog();
  connectHostWebSocket();

  // Start Animation Loop
  requestAnimationFrame(gameLoop);
});

function setupCanvasSize() {
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  ctx.scale(dpr, dpr);
}

// ==========================================
// SERVER & WEBSOCKET
// ==========================================
async function loadServerInfo() {
  try {
    const res = await fetch("/api/info");
    const data = await res.json();
    const pill = document.getElementById("lan-url-pill");
    const qrImg = document.getElementById("qr-image");
    
    if (data.public_url) {
      pill.innerHTML = `🔒 <strong>Phone link via ngrok</strong><br><span style="color:#00dfd8; font-weight:700;">${data.public_url}/phone?room=${state.roomId}</span><br><small>On the first visit, tap “Visit Site” on ngrok’s confirmation page.</small>`;
      qrImg.src = `/api/qr/${state.roomId}?t=${Date.now()}`;
    } else {
      pill.textContent = `Local Wi-Fi only — connect phones to the same Wi-Fi: http://${data.lan_ip}:${data.port}/phone?room=${state.roomId}`;
      qrImg.src = `/api/qr/${state.roomId}?t=${Date.now()}`;
    }
  } catch (err) {
    console.warn("Could not load server info:", err);
  }
}

async function loadSongCatalog() {
  try {
    const res = await fetch("/api/songs");
    state.songs = await res.json();
    renderSongCatalog();
  } catch (err) {
    console.error("Failed to load song catalog:", err);
  }
}

function connectHostWebSocket() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${protocol}//${window.location.host}/ws/host/${state.roomId}`;
  state.ws = new WebSocket(wsUrl);

  state.ws.onopen = () => {
    console.log("[Host WS] Connected to room", state.roomId);
  };

  state.ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    handleWebSocketMessage(msg);
  };

  state.ws.onclose = () => {
    console.log("[Host WS] Disconnected. Reconnecting in 2s...");
    setTimeout(connectHostWebSocket, 2000);
  };
}

function handleWebSocketMessage(msg) {
  switch (msg.type) {
    case "host_connected":
      updatePlayersList(msg.players || []);
      break;

    case "device_paired":
      updatePlayersList(msg.players || []);
      break;

    case "device_disconnected":
      updatePlayersList(msg.players || []);
      break;

    case "players_update":
      updatePlayersList(msg.players || []);
      break;

    case "game_started":
      onGameStarted(msg);
      break;

    case "live_battle_update":
      state.livePlayerStates = msg.players || [];
      state.popcornState = msg.popcorn_state || null;
      updateBattleUI(msg);
      break;

    case "game_over":
      onGameOver(msg);
      break;
  }
}

// ==========================================
// UI & NAVIGATION
// ==========================================
function switchView(viewId) {
  document.querySelectorAll(".view-container").forEach(el => el.classList.remove("active"));
  const target = document.getElementById(viewId);
  if (target) {
    target.classList.add("active");
    if (viewId === "view-battle") {
      setupCanvasSize();
    }
  }
}

function setupUIEventListeners() {
  // Mode selection cards
  const modeCards = document.querySelectorAll(".mode-card");
  modeCards.forEach(card => {
    card.addEventListener("click", () => {
      modeCards.forEach(c => c.classList.remove("selected"));
      card.classList.add("selected");
      state.mode = card.getAttribute("data-mode");
    });
  });

  // Laptop Mic Solo toggle
  document.getElementById("btn-toggle-laptop-mic").addEventListener("click", toggleLaptopMic);

  // Navigate to song select
  document.getElementById("btn-to-song-select").addEventListener("click", () => {
    switchView("view-songs");
  });

  // Back to lobby
  document.getElementById("btn-back-to-lobby").addEventListener("click", () => {
    switchView("view-lobby");
  });

  // Launch Battle
  document.getElementById("btn-launch-battle").addEventListener("click", startBattle);

  // Quit Song
  document.getElementById("btn-quit-song").addEventListener("click", stopBattle);

  // Scoreboard navigation
  document.getElementById("btn-play-again").addEventListener("click", () => {
    startBattle();
  });
  document.getElementById("btn-choose-song").addEventListener("click", () => {
    switchView("view-songs");
  });

  // Clear all connected devices
  const btnClearAll = document.getElementById("btn-clear-all-devices");
  if (btnClearAll) {
    btnClearAll.addEventListener("click", kickAllPlayers);
  }
}

// Global functions for kick / remove
window.kickPlayer = async function(deviceName) {
  if (confirm(`Disconnect "${deviceName}"?`)) {
    // Send via WebSocket if open
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      state.ws.send(JSON.stringify({
        type: "remove_player",
        device_name: deviceName,
      }));
    }
    // Also post to REST endpoint for guaranteed disconnection
    try {
      await fetch(`/api/room/${state.roomId}/remove_player?device_name=${encodeURIComponent(deviceName)}`, { method: "POST" });
    } catch (err) {
      console.warn("REST remove error:", err);
    }
    // Optimistic UI update
    updatePlayersList(state.connectedPlayers.filter(p => p !== deviceName));
  }
};

window.kickAllPlayers = async function() {
  if (confirm("Disconnect all connected devices from this room?")) {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      state.ws.send(JSON.stringify({
        type: "remove_all_players",
      }));
    }
    try {
      await fetch(`/api/room/${state.roomId}/clear_players`, { method: "POST" });
    } catch (err) {
      console.warn("REST clear error:", err);
    }
    updatePlayersList([]);
  }
};

function updatePlayersList(players) {
  state.connectedPlayers = players;
  const countEl = document.getElementById("player-count");
  const listEl = document.getElementById("players-list");
  const btnSelect = document.getElementById("btn-to-song-select");
  const btnClearAll = document.getElementById("btn-clear-all-devices");

  const totalParticipants = players.length + (state.isLaptopMicActive ? 1 : 0);
  countEl.textContent = totalParticipants;

  if (btnClearAll) {
    btnClearAll.style.display = players.length > 0 ? "inline-block" : "none";
  }

  let html = "";
  if (state.isLaptopMicActive) {
    html += `
      <div class="player-item">
        <div class="p-left">
          <span class="player-avatar" style="background: #00dfd8; color: #00dfd8;"></span>
          <span class="player-name">Laptop Host (Solo Mic)</span>
        </div>
        <div style="display: flex; align-items: center; gap: 8px;">
          <span class="player-tag">HOST</span>
          <button class="btn-kick-device" onclick="toggleLaptopMic()" title="Disable laptop mic">✕ Off</button>
        </div>
      </div>
    `;
  }

  players.forEach((name, idx) => {
    const colors = ["#00dfd8", "#ff0080", "#f5a623", "#9d4edd", "#00f279", "#ff4757"];
    const color = colors[idx % colors.length];
    html += `
      <div class="player-item">
        <div class="p-left">
          <span class="player-avatar" style="background: ${color}; color: ${color};"></span>
          <span class="player-name">${name}</span>
        </div>
        <div style="display: flex; align-items: center; gap: 8px;">
          <span class="player-tag">PHONE</span>
          <button class="btn-kick-device" onclick="kickPlayer('${name}')" title="Disconnect ${name}">✕ Remove</button>
        </div>
      </div>
    `;
  });

  if (totalParticipants === 0) {
    html = `<div class="empty-players">Waiting for phones to connect...</div>`;
  }

  listEl.innerHTML = html;
  btnSelect.disabled = totalParticipants === 0;
}

// ==========================================
// SONG CATALOG RENDERING
// ==========================================
function renderSongCatalog() {
  const container = document.getElementById("song-cards-container");
  container.innerHTML = "";

  state.songs.forEach((song, idx) => {
    const card = document.createElement("div");
    card.className = `song-card ${idx === 0 ? "selected" : ""}`;
    card.id = `song-card-${song.id}`;

    const dur = song.duration || song.duration_sec || 0;
    const notesCount = song.total_notes || song.notes_count || 0;
    const diff = song.difficulty || (notesCount > 350 ? "Hard" : (notesCount > 100 ? "Medium" : "Easy"));
    const diffClass = diff === "Easy" ? "badge-easy" : (diff === "Hard" ? "badge-hard" : "badge-medium");
    const minutes = Math.floor(dur / 60);
    const seconds = Math.floor(dur % 60).toString().padStart(2, "0");

    card.innerHTML = `
      <div>
        <div class="song-header">
          <div class="song-icon">🎵</div>
          <div>
            <div class="song-title">${song.title}</div>
            <div class="song-artist">${song.artist}</div>
            <div class="song-badges">
              <span class="badge ${diffClass}">${diff}</span>
              <span class="badge badge-notes">${notesCount} Notes</span>
            </div>
          </div>
        </div>
      </div>
      <div class="song-footer">
        <span class="song-duration">⏱️ ${minutes}:${seconds}</span>
        <button class="btn-secondary" style="padding: 6px 14px; font-size: 12px;">Select</button>
      </div>
    `;

    card.addEventListener("click", () => {
      document.querySelectorAll(".song-card").forEach(c => c.classList.remove("selected"));
      card.classList.add("selected");
      state.selectedSong = song;
      document.getElementById("btn-launch-battle").disabled = false;
    });

    container.appendChild(card);
  });

  if (state.songs.length > 0) {
    state.selectedSong = state.songs[0];
    document.getElementById("btn-launch-battle").disabled = false;
  }
}

// ==========================================
// BATTLE LIFECYCLE
// ==========================================
async function startBattle() {
  if (!state.selectedSong) return;

  // Load song JSON details
  try {
    const res = await fetch(`/api/song/${state.selectedSong.id}`);
    state.songData = await res.json();
  } catch (err) {
    console.error("Error loading song details:", err);
    return;
  }

  // Set audio source for laptop speaker playback
  // Catalog audio_url values are relative `songs/...` paths, but this app
  // serves the files through the API endpoint (the songs directory is not
  // mounted as a static path).
  audioElement.src = `/api/audio/${encodeURIComponent(state.selectedSong.audio_file)}`;
  audioElement.currentTime = 0;

  // Build syllable strip at bottom
  renderBottomLyricsStrip();

  // Switch to Battle Arena view
  switchView("view-battle");

  // Send start command to server
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify({
      type: "start_song",
      song_id: state.selectedSong.id,
      mode: state.mode,
    }));
  }

  // Play audio on laptop speakers
  try {
    await audioElement.play();
  } catch (err) {
    console.warn("Auto-play blocked, playing on user gesture:", err);
  }

  // Audio ended listener
  audioElement.onended = () => {
    stopBattle();
  };
}

function stopBattle() {
  audioElement.pause();
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify({
      type: "stop_song",
    }));
  }
}

function onGameStarted(msg) {
  state.songData = msg.song_data;
  renderBottomLyricsStrip();
}

function onGameOver(msg) {
  audioElement.pause();
  switchView("view-scoreboard");

  const scoreboard = msg.scoreboard || [];
  const winner = msg.winner || (scoreboard[0] ? scoreboard[0].device_name : "Nobody");

  document.getElementById("winner-banner-title").textContent = `👑 WINNER: ${winner}`;
  document.getElementById("winner-banner-sub").textContent =
    state.mode === "popcorn"
      ? "Carried the party with unmatched phrasing & pitch!"
      : "Completely mogged the voice duel with pitch dominance!";

  const podiumGrid = document.getElementById("scoreboard-podium");
  podiumGrid.innerHTML = "";

  scoreboard.forEach((p, idx) => {
    const card = document.createElement("div");
    card.className = `podium-card rank-${idx + 1}`;
    card.innerHTML = `
      <div class="podium-rank">#${idx + 1} PLACE</div>
      <div class="podium-name">${p.device_name}</div>
      <div class="grade-badge grade-${p.grade}">${p.grade}</div>
      <div class="podium-score">${p.total_score.toLocaleString()} PTS</div>
      <div class="podium-stats">
        <div class="stat-row"><span>Accuracy</span><span>${p.pitch_accuracy_pct}%</span></div>
        <div class="stat-row"><span>Perfect Hits</span><span>${p.hits_perfect}</span></div>
        <div class="stat-row"><span>Good Hits</span><span>${p.hits_good}</span></div>
        <div class="stat-row"><span>Misses</span><span>${p.misses}</span></div>
        <div class="stat-row"><span>Max Streak</span><span>${p.max_streak}x</span></div>
      </div>
    `;
    podiumGrid.appendChild(card);
  });
}

// ==========================================
// BOTTOM LYRICS STRIP
// ==========================================
function renderBottomLyricsStrip() {
  const strip = document.getElementById("arena-lyrics-strip");
  strip.innerHTML = "";
  if (!state.songData || !state.songData.notes) return;

  state.songData.notes.forEach((note, idx) => {
    const span = document.createElement("span");
    span.className = "syllable-item";
    span.id = `syl-${idx}`;
    span.textContent = note.text ? `${note.text} ` : "... ";
    strip.appendChild(span);
  });
}

// ==========================================
// LIVE BATTLE UI & CANVAS RENDERING
// ==========================================
function updateBattleUI(msg) {
  // Update Popcorn / Mog turn badge
  const pill = document.getElementById("arena-turn-indicator");
  if (msg.game_mode === "popcorn") {
    const pc = msg.popcorn_state;
    if (pc && pc.player_count > 1) {
      if (pc.is_transition_window && pc.countdown_seconds) {
        pill.innerHTML = `<span style="color:#f59e0b; animation: pulse 0.5s infinite alternate;">⚠️ TURN SWITCH IN ${pc.countdown_seconds}... (${pc.turn_ending_player} ➔ ${pc.turn_starting_player})</span>`;
      } else {
        const halfText = pc.active_half === 0 ? "First Half" : "Second Half";
        pill.textContent = `🍿 Part ${pc.active_half + 1} (${halfText}): ${msg.active_popcorn_player || "Player"}`;
      }
    } else {
      pill.textContent = `🍿 Solo Turn: ${msg.active_popcorn_player || "Player 1"} (Full Song)`;
    }
  } else {
    pill.textContent = `⚔️ Concurrent Mog Battle (${(msg.players || []).length} Singers)`;
  }

  // Update top bar with the active singer's tuner metrics
  let activeSinger = null;
  if (msg.players && msg.players.length > 0) {
    if (msg.game_mode === "popcorn" && msg.active_popcorn_player) {
      activeSinger = msg.players.find(p => p.device_name === msg.active_popcorn_player) || msg.players[0];
    } else {
      activeSinger = msg.players[0];
    }
  }
  if (activeSinger) {
    const micPct = activeSinger.mic_signal_received
      ? Math.min(100, Math.round((activeSinger.rms || 0) * 500))
      : 0;
    const micBar = document.getElementById("arena-mic-bar");
    const micLabel = document.getElementById("arena-mic-pct");
    if (micBar) micBar.style.width = `${micPct}%`;
    if (micLabel) micLabel.textContent = `${micPct}%`;

    // Top Active Lyric Word
    const wordEl = document.getElementById("arena-active-lyric");
    if (activeSinger.current_word) {
      wordEl.textContent = `"${activeSinger.current_word}"`;
    }

    // Pitch Tuner Display
    const pitchEl = document.getElementById("arena-pitch-name");
    const subtextEl = document.getElementById("arena-tuner-subtext");
    const needleEl = document.getElementById("arena-tuner-needle");

    if (activeSinger.sung_midi) {
      pitchEl.textContent = midiToNoteName(activeSinger.sung_midi);
      const diff = activeSinger.semitone_diff || 0;
      // Map [-0.75, +0.75] to [15%, 85%]
      const clampedDiff = Math.max(-1.0, Math.min(1.0, diff));
      const pct = 50 + (clampedDiff * 40);
      needleEl.style.left = `${pct}%`;

      if (activeSinger.is_in_tune) {
        subtextEl.textContent = `${activeSinger.device_name}: IN TUNE!`;
        subtextEl.style.color = "var(--green)";
      } else {
        subtextEl.textContent = `${activeSinger.device_name}: ${diff < 0 ? "Flat (sing higher)" : "Sharp (sing lower)"}`;
        subtextEl.style.color = "var(--red)";
      }
    } else {
      pitchEl.textContent = "--";
      needleEl.style.left = "50%";
      if (!activeSinger.is_active_turn) {
        subtextEl.textContent = `${activeSinger.device_name}: Ready for turn`;
      } else if (!activeSinger.mic_signal_received) {
        subtextEl.textContent = `${activeSinger.device_name}: No mic frames received`;
      } else if ((activeSinger.rms || 0) >= 0.003) {
        subtextEl.textContent = `${activeSinger.device_name}: Sound received; pitch not locked`;
      } else {
        subtextEl.textContent = `${activeSinger.device_name}: Mic connected, no sound detected`;
      }
      subtextEl.style.color = "var(--text-muted)";
    }
  }
}

// Periodic Time Sync to WebSocket
let lastTimeSync = 0;
function sendTimeSync() {
  const now = performance.now();
  if (now - lastTimeSync > 50 && state.ws && state.ws.readyState === WebSocket.OPEN) {
    lastTimeSync = now;
    state.ws.send(JSON.stringify({
      type: "time_update",
      currentTime: audioElement.currentTime,
    }));
  }
}

// ==========================================
// 60 FPS CANVAS GAME LOOP
// ==========================================
function gameLoop() {
  const rect = canvas.getBoundingClientRect();
  const width = rect.width;
  const height = rect.height;

  // Clear canvas with dark navy
  ctx.fillStyle = "#020612";
  ctx.fillRect(0, 0, width, height);

  const t = audioElement.currentTime;
  const effectiveDuration = (state.selectedSong && state.selectedSong.duration_sec)
    ? state.selectedSong.duration_sec
    : (audioElement.duration || 1);

  // Send authoritative playback time to server
  if (!audioElement.paused) {
    sendTimeSync();
  }

  // Update Bottom Timeline Progress Bar
  const progFill = document.getElementById("arena-song-progress");
  if (progFill && effectiveDuration > 0) {
    progFill.style.width = `${Math.min(100, (t / effectiveDuration) * 100)}%`;
  }

  // Draw Horizontal Pitch Grid & Note Labels
  drawPitchGrid(width, height);

  // Draw Vertical Hit Line at x = 150 (Matching Screenshot)
  drawHitLine(height);

  // Draw Scrolling Note Pills
  if (state.songData && state.songData.notes) {
    drawNotes(t, width, height);
  }

  // Draw Live Player Cursors at Hit Line
  drawPlayerCursors(height, t);

  // Draw Popcorn Transition 3...2...1 Countdown Banner on Canvas
  drawPopcornCountdownBanner(width, height, t);

  // Draw Floating Score Particles
  updateAndDrawParticles();

  requestAnimationFrame(gameLoop);
}

// Pitch Grid Configuration
const MIN_MIDI = 53; // F3
const MAX_MIDI = 74; // D5
const MIDI_RANGE = MAX_MIDI - MIN_MIDI;

function midiToY(midi, height) {
  // Fold octave to fit display nicely
  let m = midi;
  while (m < MIN_MIDI) m += 12;
  while (m > MAX_MIDI) m -= 12;
  const norm = (m - MIN_MIDI) / MIDI_RANGE;
  return height - (norm * (height - 60)) - 30;
}

function drawPitchGrid(width, height) {
  ctx.strokeStyle = "rgba(255, 255, 255, 0.05)";
  ctx.lineWidth = 1;
  ctx.fillStyle = "#4b5563";
  ctx.font = "11px system-ui";

  // Visible key pitch lines
  const keyNotes = [57, 60, 62, 64, 65, 67, 69, 72]; // A3, C4, D4, E4, F4, G4, A4, C5
  keyNotes.forEach(m => {
    const y = midiToY(m, height);
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(width, y);
    ctx.stroke();

    // Note label on left axis
    ctx.fillText(midiToNoteName(m), 18, y + 4);
  });
}

function drawHitLine(height) {
  const x = state.hitLineX;

  ctx.save();
  // Glowing vertical cyan line
  ctx.strokeStyle = "#00dfd8";
  ctx.lineWidth = 2.5;
  ctx.shadowColor = "rgba(0, 223, 216, 0.6)";
  ctx.shadowBlur = 10;

  ctx.beginPath();
  ctx.moveTo(x, 20);
  ctx.lineTo(x, height - 20);
  ctx.stroke();

  // Cyan end cap dots
  ctx.fillStyle = "#00dfd8";
  ctx.beginPath();
  ctx.arc(x, 20, 4, 0, Math.PI * 2);
  ctx.arc(x, height - 20, 4, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

function drawNotes(t, width, height) {
  const notes = state.songData.notes;
  const pxPerSec = state.pxPerSec;
  const hitX = state.hitLineX;
  let activeNoteFound = false;

  notes.forEach((note, idx) => {
    const startX = hitX + (note.start - t) * pxPerSec;
    const noteWidth = Math.max(34, (note.end - note.start) * pxPerSec);
    const endX = startX + noteWidth;

    // Skip offscreen notes
    if (endX < 0 || startX > width + 100) return;

    const y = midiToY(note.midi, height);
    const pillHeight = 26;
    const pillY = y - pillHeight / 2;

    const isPassed = note.end < t;
    const isActive = note.start <= t && t <= note.end;

    // Highlight bottom syllable strip
    const sylEl = document.getElementById(`syl-${idx}`);
    if (sylEl) {
      if (isActive) {
        activeNoteFound = true;
        sylEl.className = "syllable-item active";
        // Update top lyric word
        if (note.text) {
          document.getElementById("arena-active-lyric").textContent = `"${note.text}"`;
        }
      } else if (isPassed) {
        sylEl.className = "syllable-item passed";
      } else {
        sylEl.className = "syllable-item";
      }
    }

    ctx.save();
    // Rounded capsule path
    const r = pillHeight / 2;
    ctx.beginPath();
    ctx.roundRect(startX, pillY, noteWidth, pillHeight, [r]);

    if (isActive) {
      // Golden Glowing Pill from Screenshot
      ctx.fillStyle = "#f59e0b";
      ctx.shadowColor = "rgba(245, 158, 11, 0.8)";
      ctx.shadowBlur = 18;
      ctx.fill();
      ctx.strokeStyle = "#fbbf24";
      ctx.lineWidth = 2;
      ctx.stroke();

      // Golden bold lyric text inside
      ctx.fillStyle = "#040814";
      ctx.font = "bold 13px system-ui";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(note.text || "", startX + noteWidth / 2, y);

    } else if (isPassed) {
      // Passed note (dark muted pill)
      ctx.fillStyle = "rgba(40, 52, 75, 0.55)";
      ctx.fill();
      ctx.strokeStyle = "rgba(255, 255, 255, 0.08)";
      ctx.lineWidth = 1;
      ctx.stroke();

      ctx.fillStyle = "rgba(255, 255, 255, 0.3)";
      ctx.font = "12px system-ui";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(note.text || "", startX + noteWidth / 2, y);

    } else {
      // Future note capsule
      // In Popcorn mode with 2+ players, color-code capsules:
      // Half 0 (User 1): Cyan accent (#06b6d4)
      // Half 1 (User 2): Magenta/Pink accent (#ec4899)
      let pillFill = "#6366f1";
      let pillStroke = "#818cf8";
      let pillShadow = "rgba(99, 102, 241, 0.4)";

      if (state.mode === "popcorn" && state.popcornState && state.popcornState.player_count > 1) {
        if (note.half_idx === 0) {
          pillFill = "#0891b2"; // Cyan for User 1
          pillStroke = "#22d3ee";
          pillShadow = "rgba(6, 182, 212, 0.4)";
        } else {
          pillFill = "#db2777"; // Magenta/Pink for User 2
          pillStroke = "#f472b6";
          pillShadow = "rgba(219, 39, 119, 0.4)";
        }
      }

      ctx.fillStyle = pillFill;
      ctx.shadowColor = pillShadow;
      ctx.shadowBlur = 10;
      ctx.fill();
      ctx.strokeStyle = pillStroke;
      ctx.lineWidth = 1.5;
      ctx.stroke();

      // White lyric syllable text inside capsule
      ctx.fillStyle = "#ffffff";
      ctx.font = "600 12px system-ui";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(note.text || "", startX + noteWidth / 2, y);
    }

    ctx.restore();
  });

  if (!activeNoteFound && t > 2.0) {
    const lyricEl = document.getElementById("arena-active-lyric");
    if (lyricEl) lyricEl.textContent = "♫ (Instrumental Break) ♫";
  }
}

function drawPlayerCursors(height, t) {
  const hitX = state.hitLineX;
  const players = state.livePlayerStates;

  const activeTargetNote = (state.songData && state.songData.notes) ? state.songData.notes.find(
    (n) => t >= n.start - 0.05 && t <= n.end + 0.05
  ) : null;

  players.forEach((p) => {
    if (!p.is_singing || !p.sung_midi) return;

    // PitchBattle Lite octave-folded display & scoring
    const targetMidi = activeTargetNote ? activeTargetNote.midi : 65;
    const disp = p.sung_midi + 12 * Math.round((targetMidi - p.sung_midi) / 12);
    
    let dist = 99;
    if (activeTargetNote) {
      dist = Math.abs(((p.sung_midi - activeTargetNote.midi) % 12 + 12) % 12);
      dist = Math.min(dist, 12 - dist);
    }
    const inTune = dist <= 0.75;

    // PitchBattle Lite cursor smoothing: curY += (targetY - curY) * 0.4
    const targetY = midiToY(disp, height);
    if (p.curY === undefined || isNaN(p.curY)) {
      p.curY = targetY;
    } else {
      p.curY += (targetY - p.curY) * 0.4;
    }
    const y = p.curY;

    ctx.save();
    // Glowing voice cursor orb: green (#4caf50) when in-tune (dist <= 0.75), red when off
    const orbColor = inTune ? "#4caf50" : "#ff4757";
    ctx.shadowColor = orbColor;
    ctx.shadowBlur = 16;
    ctx.fillStyle = orbColor;
    ctx.beginPath();
    ctx.arc(hitX, y, 9, 0, Math.PI * 2);
    ctx.fill();

    // Outer ring
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 2;
    ctx.stroke();

    // Floating Player Name & Score Tag
    ctx.fillStyle = "#ffffff";
    ctx.font = "bold 11px system-ui";
    ctx.textAlign = "left";
    ctx.fillText(`${p.device_name} (${p.score} pts)`, hitX + 16, y + 4);

    // Particle burst on in-tune streak
    if (inTune && Math.random() < 0.25) {
      spawnParticle(hitX, y, dist <= 0.35 ? "PERFECT! +100" : "GOOD! +50", orbColor);
    }

    ctx.restore();
  });
}

function drawPopcornCountdownBanner(width, height, t) {
  if (state.mode !== "popcorn") return;
  const pc = state.popcornState;
  if (!pc || pc.player_count <= 1) return;

  const splitT = pc.split_time || 0;
  if (splitT <= 0) return;

  const timeToSwitch = splitT - t;

  // 1. Countdown Warning Window: 3.5s leading up to half split
  if (timeToSwitch > 0 && timeToSwitch <= 3.5) {
    const count = Math.min(3, Math.max(1, Math.ceil(timeToSwitch)));
    const endingUser = pc.turn_ending_player || pc.user1_name || "Player 1";
    const startingUser = pc.turn_starting_player || pc.user2_name || "Player 2";

    ctx.save();
    // Dark amber-gold glowing notification overlay
    const bannerW = Math.min(520, width - 40);
    const bannerH = 74;
    const bannerX = (width - bannerW) / 2;
    const bannerY = 24;

    ctx.fillStyle = "rgba(10, 16, 32, 0.92)";
    ctx.strokeStyle = "#f59e0b";
    ctx.lineWidth = 2.5;
    ctx.shadowColor = "rgba(245, 158, 11, 0.7)";
    ctx.shadowBlur = 18;
    ctx.beginPath();
    ctx.roundRect(bannerX, bannerY, bannerW, bannerH, [16]);
    ctx.fill();
    ctx.stroke();

    // Giant countdown digit
    ctx.fillStyle = "#fbbf24";
    ctx.font = "900 36px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(`${count}`, bannerX + 44, bannerY + bannerH / 2);

    // Warning text
    ctx.fillStyle = "#ffffff";
    ctx.font = "bold 15px system-ui, sans-serif";
    ctx.textAlign = "left";
    ctx.fillText(`⚠️ TURN ENDING FOR: ${endingUser}`, bannerX + 80, bannerY + 26);

    ctx.fillStyle = "#00dfd8";
    ctx.font = "600 13px system-ui, sans-serif";
    ctx.fillText(`🎤 ${startingUser} GET READY TO SING IN ${count}...`, bannerX + 80, bannerY + 50);

    ctx.restore();
  }
  // 2. Freshly Switched Alert Window: First 3 seconds of second half
  else if (t >= splitT && t <= splitT + 3.0) {
    const startingUser = pc.user2_name || "Player 2";
    ctx.save();
    const bannerW = Math.min(480, width - 40);
    const bannerH = 60;
    const bannerX = (width - bannerW) / 2;
    const bannerY = 24;

    ctx.fillStyle = "rgba(6, 78, 59, 0.92)";
    ctx.strokeStyle = "#10b981";
    ctx.lineWidth = 2.5;
    ctx.shadowColor = "rgba(16, 185, 129, 0.8)";
    ctx.shadowBlur = 18;
    ctx.beginPath();
    ctx.roundRect(bannerX, bannerY, bannerW, bannerH, [16]);
    ctx.fill();
    ctx.stroke();

    ctx.fillStyle = "#10b981";
    ctx.font = "900 24px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(`🎤 ${startingUser}'s TURN! SING NOW!`, width / 2, bannerY + bannerH / 2);
    ctx.restore();
  }
}

function spawnParticle(x, y, text, color) {
  state.particles.push({
    x,
    y,
    text,
    color,
    vy: -1.2,
    alpha: 1.0,
  });
}

function updateAndDrawParticles() {
  for (let i = state.particles.length - 1; i >= 0; i--) {
    const p = state.particles[i];
    p.y += p.vy;
    p.alpha -= 0.03;

    if (p.alpha <= 0) {
      state.particles.splice(i, 1);
      continue;
    }

    ctx.save();
    ctx.fillStyle = p.color;
    ctx.globalAlpha = p.alpha;
    ctx.font = "bold 12px system-ui";
    ctx.textAlign = "center";
    ctx.fillText(p.text, p.x, p.y);
    ctx.restore();
  }
}

// ==========================================
// LAPTOP MIC SOLO TESTING (AUTOCORRELATION)
// ==========================================
async function toggleLaptopMic() {
  const btn = document.getElementById("btn-toggle-laptop-mic");
  const icon = document.getElementById("laptop-mic-icon");
  const text = document.getElementById("laptop-mic-text");

  if (state.isLaptopMicActive) {
    // Disable mic
    if (state.micStream) {
      state.micStream.getTracks().forEach(t => t.stop());
    }
    state.isLaptopMicActive = false;
    icon.textContent = "🎙️";
    text.textContent = "Enable Laptop Mic (Solo Mode)";
    updatePlayersList(state.connectedPlayers);
    return;
  }

  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        autoGainControl: true,
        noiseSuppression: false,
      },
      video: false,
    });
    state.micStream = stream;
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    state.audioContext = new AudioCtx();
    const source = state.audioContext.createMediaStreamSource(stream);
    state.analyser = state.audioContext.createAnalyser();
    state.analyser.fftSize = 2048;
    state.analyser.smoothingTimeConstant = 0.0;
    
    // Silent sink keeps the audio graph pulling samples continuously without feedback
    const z = state.audioContext.createGain();
    z.gain.value = 0;
    source.connect(state.analyser);
    state.analyser.connect(z);
    z.connect(state.audioContext.destination);

    state.isLaptopMicActive = true;
    icon.textContent = "🟢";
    text.textContent = "Laptop Mic Active (Singing enabled)";
    updatePlayersList(state.connectedPlayers);

    // Start Pitch Detection Loop
    startLaptopPitchDetection();
  } catch (err) {
    alert("Microphone permission denied or unavailable on this device.");
    console.error("Mic error:", err);
  }
}

function startLaptopPitchDetection() {
  const buffer = new Float32Array(state.analyser.fftSize);

  function detectPitch() {
    if (!state.isLaptopMicActive) return;

    state.analyser.getFloatTimeDomainData(buffer);
    const f = yin(buffer, state.audioContext.sampleRate);

    let rms = 0;
    for (let i = 0; i < buffer.length; i++) rms += buffer[i] * buffer[i];
    rms = Math.sqrt(rms / buffer.length);

    // Update Mic Level Meter
    const pct = Math.min(100, Math.round(rms * 500));
    const bar = document.getElementById("arena-mic-bar");
    const pctEl = document.getElementById("arena-mic-pct");
    if (bar && pctEl) {
      bar.style.width = `${pct}%`;
      pctEl.textContent = `${pct}%`;
    }

    if (f > 0 && state.ws && state.ws.readyState === WebSocket.OPEN) {
      const midi = 69 + 12 * Math.log2(f / 440);
      state.ws.send(JSON.stringify({
        type: "host_pitch_frame",
        device_name: "Host Laptop",
        hz: f,
        midi: midi,
        confidence: 0.95,
        rms: rms,
      }));
    }

    requestAnimationFrame(detectPitch);
  }

  requestAnimationFrame(detectPitch);
}

// ==========================================
// PITCHBATTLE LITE YIN PITCH DETECTOR ENGINE
// ==========================================
function yin(buf, sr) {
  let r = 0;
  for (const x of buf) r += x * x;
  if (Math.sqrt(r / buf.length) < 0.005) return -1;
  const n = buf.length >> 1, d = new Float32Array(n), maxT = Math.min(n - 1, sr / 80 | 0), minT = sr / 1000 | 0;
  for (let t = 1; t <= maxT; t++) {
    let s = 0;
    for (let i = 0; i < n; i += 2) {
      const v = buf[i] - buf[i + t];
      s += v * v;
    }
    d[t] = s;
  }
  let run = 0;
  d[0] = 1;
  for (let t = 1; t <= maxT; t++) {
    run += d[t];
    d[t] = d[t] * t / run;
  }
  for (let t = Math.max(2, minT); t < maxT; t++) {
    if (d[t] < 0.15) {
      while (t + 1 < maxT && d[t + 1] < d[t]) t++;
      const a = d[t - 1], b = d[t], c = d[t + 1], den = a - 2 * b + c;
      return sr / (t + (den ? (a - c) / (2 * den) : 0));
    }
  }
  return -1;
}
