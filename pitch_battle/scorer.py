"""
PitchBattle Scoring Engine
Features:
- Octave folding: ((sung_midi - target_midi + 6) % 12) - 6
- ±0.75 semitones tolerance (matching PitchBattle spec)
- Note interval lookup: note['start'] to note['end']
- Multi-player tracking (device name based)
- Real-time accuracy metrics and letter grades (S, A, B, C, D, F)
"""

import math
import time
from typing import Dict, List, Optional, Tuple

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def hz_to_midi(hz: float) -> Optional[float]:
    if hz <= 0:
        return None
    return 69.0 + 12.0 * math.log2(hz / 440.0)


def midi_to_note_name(midi: float) -> str:
    rounded = int(round(midi))
    return f"{NOTE_NAMES[rounded % 12]}{(rounded // 12) - 1}"


def octave_folded_diff(sung_midi: float, target_midi: float) -> float:
    """Calculates cyclic pitch distance accounting for octave folding from PitchBattle Lite."""
    raw = abs(((sung_midi - target_midi) % 12.0 + 12.0) % 12.0)
    return min(raw, 12.0 - raw)


class SongScorer:
    def __init__(self, notes: List[Dict], semitone_tolerance: float = 0.75):
        self.notes = sorted(notes, key=lambda n: n["start"])
        self.tolerance = semitone_tolerance  # ±0.75 semitones (~75 cents)

    def find_active_note(self, t: float) -> Optional[Dict]:
        """Finds note active at time t (with 100ms timing slack)."""
        slack = 0.10
        for n in self.notes:
            if (n["start"] - slack) <= t <= (n["end"] + slack):
                return n
            if n["start"] - slack > t:
                break
        return None


class PlayerScoreTracker:
    def __init__(self, device_name: str, scorer: SongScorer, player_color: str = "#00dfd8"):
        self.device_name = device_name
        self.scorer = scorer
        self.player_color = player_color
        self.total_score = 0
        self.streak = 0
        self.max_streak = 0
        self.frames_evaluated = 0
        self.hits_perfect = 0
        self.hits_good = 0
        self.misses = 0
        self.total_cents_error = 0.0
        self.is_singing = False
        self.last_sung_midi: Optional[float] = None
        self.last_target_midi: Optional[float] = None
        self.is_in_tune: bool = False
        self.last_semitone_diff: float = 0.0
        self.current_lyric_word: str = ""
        self.last_rms: float = 0.0
        self.last_frame_at: Optional[float] = None

    def evaluate(self, t: float, hz: float, midi: Optional[float], confidence: float, rms: float) -> Dict:
        self.last_rms = max(0.0, rms)
        self.last_frame_at = time.monotonic()
        # Gate out ambient silence matching PitchBattle-main sensitivity
        is_voiced = (confidence >= 0.45) and (rms >= 0.003) and (hz >= 50.0)
        self.is_singing = is_voiced

        active_note = self.scorer.find_active_note(t)
        if active_note and active_note.get("text"):
            self.current_lyric_word = active_note["text"]

        if not is_voiced:
            self.last_sung_midi = None
            self.is_in_tune = False
            return {
                "device_name": self.device_name,
                "t": t,
                "is_singing": False,
                "is_in_tune": False,
                "sung_midi": None,
                "target_midi": active_note["midi"] if active_note else None,
                "active_note": active_note,
                "score": self.total_score,
                "streak": self.streak,
            }

        sung_midi = midi if midi is not None else hz_to_midi(hz)
        self.last_sung_midi = sung_midi

        if not active_note:
            self.last_target_midi = None
            self.is_in_tune = False
            return {
                "device_name": self.device_name,
                "t": t,
                "is_singing": True,
                "is_in_tune": False,
                "sung_midi": round(sung_midi, 2),
                "target_midi": None,
                "active_note": None,
                "rating": "REST",
                "cents_off": 0.0,
                "semitone_diff": 0.0,
                "score": self.total_score,
                "streak": self.streak,
            }

        target_midi = float(active_note["midi"])
        self.last_target_midi = target_midi

        diff = octave_folded_diff(sung_midi, target_midi)
        self.last_semitone_diff = diff
        cents_off = abs(diff) * 100.0

        self.frames_evaluated += 1
        self.total_cents_error += cents_off

        # ±0.75 semitones tolerance from PitchBattle Lite
        if diff <= self.scorer.tolerance:
            self.is_in_tune = True
            self.streak += 1
            if self.streak > self.max_streak:
                self.max_streak = self.streak

            # Exact PitchBattle Lite combo multipliers: >=30: 3x, >=15: 2x, >=5: 1.5x, else 1x
            mult = 3.0 if self.streak >= 30 else (2.0 if self.streak >= 15 else (1.5 if self.streak >= 5 else 1.0))

            if diff <= 0.35:
                pts = int(round(100 * mult))
                self.hits_perfect += 1
                rating = "PERFECT"
            else:
                pts = int(round(50 * mult))
                self.hits_good += 1
                rating = "GOOD"

            self.total_score += pts
        else:
            self.is_in_tune = False
            self.streak = 0
            self.misses += 1
            rating = "MISS"
            pts = 0

        return {
            "device_name": self.device_name,
            "t": t,
            "is_singing": True,
            "is_in_tune": self.is_in_tune,
            "sung_midi": round(sung_midi, 2),
            "target_midi": target_midi,
            "semitone_diff": round(diff, 2),
            "cents_off": round(cents_off, 1),
            "rating": rating,
            "awarded_points": pts,
            "score": self.total_score,
            "streak": self.streak,
            "active_note": active_note,
        }

    def get_summary(self) -> Dict:
        total = self.frames_evaluated
        if total == 0:
            pitch_acc = 0.0
            avg_cents = 0.0
        else:
            hits = self.hits_perfect + self.hits_good
            pitch_acc = round((hits / total) * 100, 1)
            avg_cents = round(self.total_cents_error / total, 1)

        # Calculate Letter Grade
        if pitch_acc >= 90.0:
            grade = "S"
        elif pitch_acc >= 80.0:
            grade = "A"
        elif pitch_acc >= 70.0:
            grade = "B"
        elif pitch_acc >= 55.0:
            grade = "C"
        elif pitch_acc >= 40.0:
            grade = "D"
        else:
            grade = "F"

        return {
            "device_name": self.device_name,
            "total_score": self.total_score,
            "grade": grade,
            "pitch_accuracy_pct": pitch_acc,
            "avg_cents_error": avg_cents,
            "max_streak": self.max_streak,
            "hits_perfect": self.hits_perfect,
            "hits_good": self.hits_good,
            "misses": self.misses,
            "color": self.player_color,
        }
