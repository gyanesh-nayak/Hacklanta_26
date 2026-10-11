/**
 * PitchBattle Mobile Microphone Client
 * Powered by PitchBattle-main's High-Precision YIN Pitch Detection Algorithm
 * Features:
 * - Sub-sample parabolic interpolation
 * - Octave-folding distance calculator
 * - Optimized YIN step-2 loop for lag-free 60 FPS performance
 * - NoiseSuppression: false for clean singing vowel capture
 */

const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];

function hzToMidi(hz) {
  return 69 + 12 * Math.log2(hz / 440);
}

function midiToNoteName(midi) {
  if (!midi) return { note: "--", octave: 0, fullName: "--" };
  const rounded = Math.round(midi);
  const noteIndex = ((rounded % 12) + 12) % 12;
  const octave = Math.floor(rounded / 12) - 1;
  const note = NOTE_NAMES[noteIndex];
  return { note, octave, fullName: `${note}${octave}` };
}

function getOctaveFoldedDistance(userMidi, targetMidi) {
  const userPitchClass = ((userMidi % 12) + 12) % 12;
  const targetPitchClass = ((targetMidi % 12) + 12) % 12;
  const rawDiff = Math.abs(userPitchClass - targetPitchClass);
  return Math.min(rawDiff, 12 - rawDiff);
}

/**
 * Exact YIN Pitch Detector from PitchBattle-main
 */
class PitchDetector {
  constructor(sampleRate = 44100, minFreq = 65, maxFreq = 1000, yinThreshold = 0.15) {
    this.sampleRate = sampleRate;
    this.minFreq = minFreq;   // C2 (~65Hz) - covers low male voices
    this.maxFreq = maxFreq;   // C6 (~1046Hz) - covers high soprano / falsetto
    this.yinThreshold = yinThreshold;
  }

  setSampleRate(rate) {
    this.sampleRate = rate;
  }

  detect(buffer, rmsThreshold = 0.003) {
    const bufferSize = buffer.length;

    // 1. RMS Energy
    let sumSquares = 0;
    for (let i = 0; i < bufferSize; i++) {
      const sample = buffer[i];
      sumSquares += sample * sample;
    }
    const rms = Math.sqrt(sumSquares / bufferSize);

    if (rms < rmsThreshold) {
      return { hz: null, midi: null, rms, clarity: 0, noteName: "--", cents: 0, isVoiced: false };
    }

    // 2. Fast YIN difference function (step = 2 for smooth mobile performance)
    const minPeriod = Math.max(2, Math.floor(this.sampleRate / this.maxFreq));
    const maxPeriod = Math.min(Math.floor(bufferSize / 2), Math.floor(this.sampleRate / this.minFreq));
    const yinBuffer = new Float32Array(maxPeriod);

    const step = 2;
    const windowSize = bufferSize - maxPeriod;

    for (let tau = 0; tau < maxPeriod; tau++) {
      let diff = 0;
      for (let i = 0; i < windowSize; i += step) {
        const delta = buffer[i] - buffer[i + tau];
        diff += delta * delta;
      }
      yinBuffer[tau] = diff * step;
    }

    // 3. Cumulative mean normalized difference
    yinBuffer[0] = 1;
    let runningSum = 0;
    for (let tau = 1; tau < maxPeriod; tau++) {
      runningSum += yinBuffer[tau];
      yinBuffer[tau] = runningSum > 0 ? (yinBuffer[tau] * tau) / runningSum : 1;
    }

    // 4. Absolute thresholding & local minimum search
    let period = -1;
    let foundBelowThreshold = false;

    for (let tau = minPeriod; tau < maxPeriod; tau++) {
      if (yinBuffer[tau] < this.yinThreshold) {
        foundBelowThreshold = true;
        while (tau + 1 < maxPeriod && yinBuffer[tau + 1] < yinBuffer[tau]) {
          tau++;
        }
        period = tau;
        break;
      }
    }

    // Fallback: global minimum
    if (!foundBelowThreshold) {
      let minVal = 1.0;
      let minIdx = -1;
      for (let tau = minPeriod; tau < maxPeriod; tau++) {
        if (yinBuffer[tau] < minVal) {
          minVal = yinBuffer[tau];
          minIdx = tau;
        }
      }
      if (minIdx !== -1 && minVal < 0.35) {
        period = minIdx;
      }
    }

    if (period === -1 || period < minPeriod || period >= maxPeriod) {
      return { hz: null, midi: null, rms, clarity: 0, noteName: "--", cents: 0, isVoiced: false };
    }

    // 5. Parabolic interpolation for sub-sample precision
    let betterPeriod = period;
    if (period > 0 && period < maxPeriod - 1) {
      const s0 = yinBuffer[period - 1];
      const s1 = yinBuffer[period];
      const s2 = yinBuffer[period + 1];
      const denominator = 2 * (2 * s1 - s0 - s2);
      if (denominator !== 0) {
        const delta = (s2 - s0) / denominator;
        betterPeriod = period + delta;
      }
    }

    const hz = this.sampleRate / betterPeriod;
    if (hz < this.minFreq || hz > this.maxFreq || !isFinite(hz)) {
      return { hz: null, midi: null, rms, clarity: 0, noteName: "--", cents: 0, isVoiced: false };
    }

    const rawMidi = hzToMidi(hz);
    const clarity = Math.max(0, Math.min(1, 1 - (yinBuffer[period] || 0.2)));
    const noteInfo = midiToNoteName(rawMidi);
    const roundedMidi = Math.round(rawMidi);
    const cents = Math.round((rawMidi - roundedMidi) * 100);

    return {
      hz,
      midi: rawMidi,
      rms,
      clarity,
      noteName: noteInfo.fullName,
      cents,
      isVoiced: true,
    };
  }
}

// Client State
const phoneState = {
  ws: null,
  roomId: "1234",
  deviceName: "Singer 1",
  audioContext: null,
  analyser: null,
  micStream: null,
  detector: null,
  isRecording: false,
  hasJoined: false,
  loopRunning: false,
};

let screenWakeLock = null;
let reconnectTimer = null;
let isReconnectingWS = false;
let wakeLockRetryTimer = null;

/**
 * Screen Wake Lock API: Keeps phone screen awake during karaoke
 */
async function requestWakeLock() {
  if ("wakeLock" in navigator) {
    try {
      if (screenWakeLock !== null) return;
      screenWakeLock = await navigator.wakeLock.request("screen");
      screenWakeLock.addEventListener("release", () => {
        screenWakeLock = null;
        updateWakeLockUI(false);
        if (phoneState.hasJoined && document.visibilityState === "visible") {
          clearTimeout(wakeLockRetryTimer);
          wakeLockRetryTimer = setTimeout(requestWakeLock, 500);
        }
      });
      updateWakeLockUI(true);
      console.log("[WakeLock] Screen wake lock acquired successfully.");
    } catch (err) {
      console.warn("[WakeLock] Wake lock request error:", err);
      updateWakeLockUI(false);
    }
  } else {
    updateWakeLockUI(false);
  }
}

function updateWakeLockUI(active) {
  const el = document.getElementById("awake-status");
  if (!el) return;
  if (active) {
    el.textContent = "⚡ Stay-Awake: ON";
    el.classList.remove("sleep-mode");
  } else {
    el.textContent = "⚡ Stay-Awake: Paused";
    el.classList.add("sleep-mode");
  }
}

/**
 * Mobile Lifecycle Recovery:
 * When phone is unlocked or app returns from background, instantly revive services
 */
async function handleLifecycleWake() {
  console.log("[Phone] Lifecycle wake detected. Restoring wake lock, audio & connection...");

  // 1. Re-acquire the screen wake lock while the page is active.
  await requestWakeLock();

  // 2. Resume Web AudioContext if suspended by OS
  if (phoneState.audioContext) {
    if (phoneState.audioContext.state !== "running" && phoneState.audioContext.state !== "closed") {
      try {
        await phoneState.audioContext.resume();
        console.log("[Phone] AudioContext resumed successfully.");
      } catch (err) {
        console.warn("[Phone] Could not resume AudioContext:", err);
      }
    }
  }

  // 3. Inspect microphone media tracks
  if (phoneState.isRecording && phoneState.micStream) {
    const tracks = phoneState.micStream.getAudioTracks();
    const needsRestart = tracks.length === 0 || tracks.some(t => t.readyState !== "live");
    if (needsRestart) {
      console.log("[Phone] Mic stream track ended during sleep. Restarting mic...");
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            echoCancellation: true,
            autoGainControl: true,
            noiseSuppression: false,
          },
          video: false,
        });
        phoneState.micStream = stream;
        const source = phoneState.audioContext.createMediaStreamSource(stream);
        source.connect(phoneState.analyser);
        watchMicrophoneTracks(stream);
      } catch (e) {
        console.warn("[Phone] Mic re-acquisition error:", e);
      }
    }
  }

  // 4. WebSocket auto-reconnect if severed during sleep
  if (phoneState.hasJoined && (!phoneState.ws || phoneState.ws.readyState === WebSocket.CLOSED || phoneState.ws.readyState === WebSocket.CLOSING)) {
    reconnectWebSocket();
  }

  // 5. Ensure pitch loop is actively running
  if (phoneState.isRecording && !phoneState.loopRunning) {
    startPitchLoop();
  }
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    handleLifecycleWake();
  }
});
window.addEventListener("focus", handleLifecycleWake);
window.addEventListener("pageshow", handleLifecycleWake);

// User touch on screen reinforces stay-awake lock
document.addEventListener("touchstart", () => {
  requestWakeLock();
  if (phoneState.audioContext && phoneState.audioContext.state === "suspended") {
    phoneState.audioContext.resume();
  }
}, { passive: true });

window.addEventListener("DOMContentLoaded", () => {
  const urlParams = new URLSearchParams(window.location.search);
  const roomParam = urlParams.get("room");
  if (roomParam) {
    document.getElementById("input-room").value = roomParam.toUpperCase();
  }

  const randNum = Math.floor(10 + Math.random() * 90);
  const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);
  const isAndroid = /Android/.test(navigator.userAgent);
  const deviceType = isIOS ? "iPhone" : (isAndroid ? "Samsung" : "Singer");
  document.getElementById("input-device").value = `${deviceType}-${randNum}`;

  document.getElementById("btn-join").addEventListener("click", joinRoom);
  document.getElementById("btn-enable-mic").addEventListener("click", enableMicrophone);

  const btnLeave = document.getElementById("btn-phone-leave");
  if (btnLeave) {
    btnLeave.addEventListener("click", () => {
      if (confirm("Disconnect and leave room?")) {
        phoneState.hasJoined = false;
        if (reconnectTimer) clearTimeout(reconnectTimer);
        if (phoneState.micStream) {
          phoneState.micStream.getTracks().forEach(t => t.stop());
        }
        if (phoneState.ws) {
          phoneState.ws.close();
        }
        window.location.reload();
      }
    });
  }
});

function joinRoom() {
  phoneState.roomId = document.getElementById("input-room").value.trim().toUpperCase() || "1234";
  phoneState.deviceName = document.getElementById("input-device").value.trim() || "Singer";
  phoneState.hasJoined = true;

  // Activate stay-awake on join
  requestWakeLock();

  reconnectWebSocket();
}

function reconnectWebSocket() {
  if (!phoneState.hasJoined) return;
  if (isReconnectingWS) return;
  if (phoneState.ws && phoneState.ws.readyState === WebSocket.OPEN) return;

  isReconnectingWS = true;
  const statusPill = document.getElementById("conn-status");
  if (statusPill) {
    statusPill.textContent = "Connecting...";
    statusPill.classList.remove("connected");
  }

  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${protocol}//${window.location.host}/ws/player/${phoneState.roomId}/${encodeURIComponent(phoneState.deviceName)}`;

  try {
    if (phoneState.ws) {
      phoneState.ws.onopen = null;
      phoneState.ws.onmessage = null;
      phoneState.ws.onclose = null;
      phoneState.ws.onerror = null;
      try { phoneState.ws.close(); } catch (e) {}
    }

    const ws = new WebSocket(wsUrl);
    phoneState.ws = ws;

    ws.onopen = () => {
      isReconnectingWS = false;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (statusPill) {
        statusPill.textContent = "Connected ●";
        statusPill.classList.add("connected");
      }
      const btnLeave = document.getElementById("btn-phone-leave");
      if (btnLeave) btnLeave.style.display = "inline-block";
      document.getElementById("card-join").classList.add("hidden");
      if (!phoneState.isRecording) {
        document.getElementById("card-mic").classList.remove("hidden");
      }
      console.log("[Phone] Connected to room", phoneState.roomId);
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        handlePlayerMessage(msg);
      } catch (e) {
        console.error("[WS Parse Error]", e);
      }
    };

    ws.onclose = () => {
      isReconnectingWS = false;
      if (statusPill) {
        statusPill.textContent = "Disconnected";
        statusPill.classList.remove("connected");
      }
      // Automatically retry if user is still in the room
      if (phoneState.hasJoined) {
        if (reconnectTimer) clearTimeout(reconnectTimer);
        reconnectTimer = setTimeout(reconnectWebSocket, 1500);
      }
    };

    ws.onerror = (err) => {
      console.warn("[WS Error]", err);
      isReconnectingWS = false;
    };
  } catch (err) {
    console.error("[WS Connect Exception]", err);
    isReconnectingWS = false;
    if (phoneState.hasJoined) {
      if (reconnectTimer) clearTimeout(reconnectTimer);
      reconnectTimer = setTimeout(reconnectWebSocket, 2000);
    }
  }
}

async function enableMicrophone() {
  try {
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      throw new Error("This phone browser requires HTTPS for microphone access. A local HTTP link may open, but its mic will be blocked.");
    }

    // Request Screen Wake Lock and start media loop immediately
    await requestWakeLock();

    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    phoneState.audioContext = new AudioCtx();
    await phoneState.audioContext.resume();

    // Critical: noiseSuppression: false allows sustained singing vowels to pass through!
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        autoGainControl: true,
        noiseSuppression: false,
      },
      video: false,
    });

    phoneState.micStream = stream;
    watchMicrophoneTracks(stream);
    const audioTracks = stream.getAudioTracks();
    if (!audioTracks.length || audioTracks.some(track => track.readyState !== "live")) {
      throw new Error("The microphone stream has no live audio track.");
    }
    const source = phoneState.audioContext.createMediaStreamSource(stream);

    phoneState.analyser = phoneState.audioContext.createAnalyser();
    phoneState.analyser.fftSize = 2048;
    phoneState.analyser.smoothingTimeConstant = 0.0;
    source.connect(phoneState.analyser);

    // Keep mobile Web Audio pipeline alive indefinitely on iOS Safari & Android Chrome
    try {
      const silentGain = phoneState.audioContext.createGain();
      silentGain.gain.value = 0.00001; // Silent output keeps mobile audio hardware running
      phoneState.analyser.connect(silentGain);
      silentGain.connect(phoneState.audioContext.destination);
    } catch (sinkErr) {
      console.warn("Silent sink warning:", sinkErr);
    }

    phoneState.detector = new PitchDetector(phoneState.audioContext.sampleRate);
    phoneState.isRecording = true;

    const signalCaption = document.getElementById("mic-signal-caption");
    if (signalCaption) signalCaption.textContent = "Microphone connected. Sing a steady note to test pitch detection.";

    document.getElementById("card-mic").classList.add("hidden");
    document.getElementById("card-battle").classList.remove("hidden");

    startPitchLoop();
  } catch (err) {
    const signalCaption = document.getElementById("mic-signal-caption");
    if (signalCaption) signalCaption.textContent = `Microphone unavailable: ${err.message || err.name || "check browser permission"}`;
    alert(`Microphone unavailable: ${err.message || "Please allow microphone access in your browser settings."}`);
    console.error("Mic error:", err);
  }
}

function watchMicrophoneTracks(stream) {
  for (const track of stream.getAudioTracks()) {
    track.addEventListener("ended", () => {
      const caption = document.getElementById("mic-signal-caption");
      if (caption && phoneState.isRecording) {
        caption.textContent = "Phone microphone stopped. Return to this page and tap to reconnect.";
      }
      console.warn("[Phone] Microphone track ended.");
    });
    track.addEventListener("mute", () => console.warn("[Phone] Microphone track muted by browser or OS."));
    track.addEventListener("unmute", () => console.log("[Phone] Microphone track resumed."));
  }
}

function startPitchLoop() {
  if (phoneState.loopRunning) return;
  phoneState.loopRunning = true;
  const buffer = new Float32Array(2048);

  function loop() {
    if (!phoneState.isRecording) {
      phoneState.loopRunning = false;
      return;
    }

    try {
      // Mobile watchdog: auto-resume if mobile OS suspended audio context during song
      if (phoneState.audioContext && phoneState.audioContext.state === "suspended") {
        phoneState.audioContext.resume();
      }

      phoneState.analyser.getFloatTimeDomainData(buffer);
      const result = phoneState.detector.detect(buffer, 0.003);
      const micPct = Math.min(100, Math.round(result.rms * 500));
      const micBar = document.getElementById("mic-level-bar");
      const micValue = document.getElementById("mic-level-value");
      const signalCaption = document.getElementById("mic-signal-caption");
      if (micBar) micBar.style.width = `${micPct}%`;
      if (micValue) micValue.textContent = `${micPct}%`;

      if (result.isVoiced && result.hz) {
        // 1. Instant local tuner gauge response (never freezes!)
        const noteEl = document.getElementById("pitch-note");
        if (noteEl) noteEl.textContent = result.noteName;
        if (signalCaption) signalCaption.textContent = `Pitch detected: ${result.noteName}`;

        const needleEl = document.getElementById("pitch-needle");
        if (needleEl && result.midi) {
          const roundedMidi = Math.round(result.midi);
          const semitoneDiff = result.midi - roundedMidi;
          const pct = Math.max(10, Math.min(90, 50 + (semitoneDiff * 40)));
          needleEl.style.left = `${pct}%`;
        }

        // 2. Transmit frame to server for authoritative scoring
        if (phoneState.ws && phoneState.ws.readyState === WebSocket.OPEN) {
          phoneState.ws.send(JSON.stringify({
            type: "pitch_frame",
            hz: result.hz,
            midi: result.midi,
            confidence: result.clarity,
            rms: result.rms,
          }));
        }
      } else {
        if (signalCaption) {
          signalCaption.textContent = result.rms >= 0.003
            ? "Sound is reaching the mic; hold a steady vowel for pitch detection."
            : "No sound detected. Check that this browser is using the phone microphone.";
        }
        // During silence / rest between notes: gently relax needle to center (50%)
        const needleEl = document.getElementById("pitch-needle");
        if (needleEl && needleEl.style.left !== "50%") {
          needleEl.style.left = "50%";
        }

        // Heartbeat frame every ~100ms during unvoiced silence to keep server connection alive & synced
        const now = performance.now();
        if (phoneState.ws && phoneState.ws.readyState === WebSocket.OPEN && (!phoneState.lastSilenceBeat || now - phoneState.lastSilenceBeat > 100)) {
          phoneState.lastSilenceBeat = now;
          phoneState.ws.send(JSON.stringify({
            type: "pitch_frame",
            hz: 0.0,
            midi: null,
            confidence: 0.0,
            rms: result.rms || 0.0,
          }));
        }
      }
    } catch (loopErr) {
      console.warn("[PitchLoop Err]", loopErr);
    }

    setTimeout(loop, 30); // ~33 FPS continuous
  }

  loop();
}

function handlePlayerMessage(msg) {
  if (msg.type === "score_feedback") {
    const banner = document.getElementById("turn-banner");
    const isMyTurn = msg.is_my_turn !== false;
    const isPopcorn = msg.game_mode === "popcorn";
    const countdownSecs = msg.countdown_seconds;
    const countdownType = msg.countdown_type;

    if (isPopcorn && countdownType && countdownSecs) {
      if (countdownType === "ending") {
        banner.className = "turn-banner turn-ending";
        banner.textContent = `⚠️ YOUR TURN ENDING IN ${countdownSecs}...`;
      } else if (countdownType === "starting") {
        banner.className = "turn-banner turn-starting";
        banner.textContent = `🚀 YOUR TURN STARTING IN ${countdownSecs}! GET READY!`;
      }
    } else if (!isMyTurn) {
      banner.className = "turn-banner wait-turn";
      banner.textContent = `⏳ ${msg.active_player || "Partner"}'s Turn (Get Ready!)`;
    } else {
      banner.className = "turn-banner my-turn";
      banner.textContent = "🎤 YOUR TURN! SING NOW!";
    }

    if (msg.active_word) {
      document.getElementById("live-word").textContent = `"${msg.active_word}"`;
    } else if (msg.rating === "REST") {
      document.getElementById("live-word").textContent = "♫ Instrumental Break ♫";
    }

    const badge = document.getElementById("rating-badge");
    if (msg.rating) {
      if (msg.rating === "REST") {
        badge.textContent = "Take a breath ♫";
        badge.className = "rating-badge";
      } else {
        badge.textContent = `${msg.rating} ${msg.rating === "PERFECT" ? "+100" : (msg.rating === "GOOD" ? "+70" : "+0")}`;
        badge.className = `rating-badge ${msg.rating.toLowerCase()}`;
      }

      if (msg.rating === "PERFECT" && navigator.vibrate) {
        navigator.vibrate(35);
      }
    }

    const needle = document.getElementById("pitch-needle");
    if (needle) {
      if (msg.rating === "REST" || msg.rating === "WAITING" || !isMyTurn) {
        needle.style.left = "50%";
      } else if (msg.cents_off !== undefined && msg.sung_midi) {
        const semitones = (msg.cents_off / 100);
        const clamped = Math.max(-1.0, Math.min(1.0, msg.is_in_tune ? 0 : (semitones * (msg.rating === "FLAT" ? -1 : 1))));
        const pct = 50 + (clamped * 40);
        needle.style.left = `${pct}%`;
      }
    }

    document.getElementById("stat-score").textContent = (msg.score || 0).toLocaleString();
    document.getElementById("stat-streak").textContent = `${msg.streak || 0}x`;
  } else if (msg.type === "game_over") {
    const banner = document.getElementById("turn-banner");
    banner.className = "turn-banner my-turn";
    banner.textContent = "🏆 SONG FINISHED! CHECK HOST SCREEN!";
  } else if (msg.type === "kicked") {
    phoneState.hasJoined = false;
    phoneState.isRecording = false;
    if (reconnectTimer) clearTimeout(reconnectTimer);
    if (phoneState.micStream) {
      phoneState.micStream.getTracks().forEach(t => t.stop());
    }
    // Instantly reset phone back to join screen ready for reconnection
    window.location.reload();
  }
}
