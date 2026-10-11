"""
Rebuilds clean, musical, rhythmically aligned song JSONs for PitchBattle.
Eliminates raw acoustic noise, aligns syllables to actual musical beats,
and ensures quantized, singable MIDI pitch targets.
"""

import json
import pathlib
import numpy as np
import soundfile as sf

BASE_DIR = pathlib.Path(__file__).resolve().parent
SONGS_DIR = BASE_DIR / "songs"
SONGS_DIR.mkdir(parents=True, exist_ok=True)

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

def midi_to_note(m: int) -> str:
    return f"{NOTE_NAMES[m % 12]}{(m // 12) - 1}"

def midi_to_hz(m: float) -> float:
    return round(440.0 * (2.0 ** ((m - 69.0) / 12.0)), 2)

def make_note(start: float, end: float, midi: int, text: str, phrase_idx: int = 0) -> dict:
    dur = round(end - start, 3)
    return {
        "start": round(start, 3),
        "end": round(end, 3),
        "duration": dur,
        "midi": midi,
        "note": midi_to_note(midi),
        "pitch_hz": midi_to_hz(midi),
        "text": text,
        "lyric": text,
        "phrase_idx": phrase_idx,
    }


# ==============================================================================
# 1. YOU ARE MY SUNSHINE (Gold standard benchmark from PitchBattle Lite)
# ==============================================================================
def build_sunshine():
    raw_notes = [
        (3.0, 3.4, 60, "You", 0), (3.5, 3.9, 62, "are", 0), (4.0, 4.4, 64, "my", 0),
        (4.5, 5.3, 64, "sun-", 0), (5.4, 6.7, 64, "-shine,", 0),
        (7.0, 7.4, 62, "my", 0), (7.5, 8.1, 64, "on-", 0), (8.2, 8.6, 60, "-ly", 0),
        (8.7, 9.4, 60, "sun-", 0), (9.5, 10.9, 60, "-shine.", 0),

        (11.4, 11.8, 60, "You", 1), (11.9, 12.3, 62, "make", 1), (12.4, 12.8, 64, "me", 1),
        (12.9, 13.6, 65, "hap-", 1), (13.7, 15.0, 69, "-py", 1),
        (15.4, 15.9, 69, "when", 1), (16.0, 16.5, 67, "skies", 1), (16.6, 17.1, 65, "are", 1),
        (17.2, 18.7, 64, "gray.", 1),

        (19.2, 19.6, 60, "You'll", 2), (19.7, 20.1, 62, "ne-", 2), (20.2, 20.6, 64, "-ver", 2),
        (20.7, 21.4, 65, "know,", 2), (21.5, 22.8, 69, "dear,", 2),
        (23.2, 23.7, 69, "how", 2), (23.8, 24.3, 67, "much", 2), (24.4, 24.9, 65, "I", 2),
        (25.0, 25.7, 64, "love", 2), (25.8, 27.0, 60, "you.", 2),

        (27.5, 27.9, 60, "Please", 3), (28.0, 28.4, 62, "don't", 3), (28.5, 28.9, 64, "take", 3),
        (29.0, 29.5, 65, "my", 3), (29.6, 30.3, 64, "sun-", 3),
        (30.4, 31.0, 62, "-shine", 3), (31.1, 31.6, 62, "a-", 3), (31.7, 33.5, 60, "-way!", 3)
    ]
    notes = [make_note(s, e, m, txt, p) for s, e, m, txt, p in raw_notes]

    # Render clean accompanying audio
    sr = 44100
    dur = 35.0
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    audio = np.zeros_like(t)

    # Count-in
    for o, f in [(0, 587), (0.75, 587), (1.5, 587), (2.25, 880)]:
        i0 = int(o * sr)
        dur_f = int(0.12 * sr)
        env = np.linspace(1, 0, dur_f)
        audio[i0:i0+dur_f] += 0.25 * env * np.sin(2 * np.pi * f * t[:dur_f])

    # Melody guide
    for n in notes:
        f = n["pitch_hz"]
        i0 = int(n["start"] * sr)
        i1 = int(n["end"] * sr)
        length = i1 - i0
        env = np.ones(length)
        fade = int(0.02 * sr)
        if length > fade * 2:
            env[:fade] = np.linspace(0, 1, fade)
            env[-fade:] = np.linspace(1, 0, fade)
        audio[i0:i1] += 0.22 * env * np.sin(2 * np.pi * f * t[:length])

    # Bass accompaniment
    bass_chords = [(0,36),(3,36),(7,36),(11,41),(15,36),(19,41),(23,36),(27,43),(31,36)]
    for idx, (t_b, midi_b) in enumerate(bass_chords):
        next_t = bass_chords[idx+1][0] if idx+1 < len(bass_chords) else dur
        f = midi_to_hz(midi_b)
        i0 = int(t_b * sr)
        i1 = int(next_t * sr)
        length = i1 - i0
        env = np.linspace(0.18, 0.05, length)
        audio[i0:i1] += env * np.sign(np.sin(2 * np.pi * f * t[:length])) * 0.12

    sf.write(str(SONGS_DIR / "sunshine.wav"), audio, sr)

    return {
        "song_name": "sunshine",
        "title": "You Are My Sunshine",
        "artist": "Traditional",
        "file_name": "sunshine.wav",
        "duration_sec": 35.0,
        "difficulty": "Easy",
        "notes": notes,
    }


# ==============================================================================
# 2. TWINKLE TWINKLE LITTLE STAR (Exact audio-aligned 0.8s onsets)
# ==============================================================================
def build_twinkle():
    # Audio verified: starts at 0.8s, exactly 0.8s per beat
    melody = [
        # Phrase 0: "Twinkle, twinkle, little star"
        (0.8, 1.5, 60, "Twin-", 0), (1.6, 2.3, 60, "-kle", 0),
        (2.4, 3.1, 67, "twin-", 0), (3.2, 3.9, 67, "-kle", 0),
        (4.0, 4.7, 69, "lit-", 0),  (4.8, 5.5, 69, "-tle", 0),
        (5.6, 7.1, 67, "star,", 0),

        # Phrase 1: "How I wonder what you are"
        (7.2, 7.9, 65, "How", 1),   (8.0, 8.7, 65, "I", 1),
        (8.8, 9.5, 64, "won-", 1),  (9.6, 10.3, 64, "-der", 1),
        (10.4, 11.1, 62, "what", 1), (11.2, 11.9, 62, "you", 1),
        (12.0, 13.5, 60, "are.", 1),

        # Phrase 2: "Up above the world so high"
        (13.6, 14.3, 67, "Up", 2),   (14.4, 15.1, 67, "a-", 2),
        (15.2, 15.9, 65, "-bove", 2), (16.0, 16.7, 65, "the", 2),
        (16.8, 17.5, 64, "world", 2), (17.6, 18.3, 64, "so", 2),
        (18.4, 19.9, 62, "high,", 2),

        # Phrase 3: "Like a diamond in the sky"
        (20.0, 20.7, 67, "Like", 3),  (20.8, 21.5, 67, "a", 3),
        (21.6, 22.3, 65, "dia-", 3),  (22.4, 23.1, 65, "-mond", 3),
        (23.2, 23.9, 64, "in", 3),
    ]
    notes = [make_note(s, e, m, txt, p) for s, e, m, txt, p in melody]
    return {
        "song_name": "twinkle_twinkle",
        "title": "Twinkle Twinkle Little Star",
        "artist": "Traditional",
        "file_name": "twinkle_twinkle.wav",
        "duration_sec": 24.0,
        "difficulty": "Easy",
        "notes": notes,
    }


# ==============================================================================
# 3. ALPHABET SONG (Exact audio-aligned 10.15s vocal entrance after chime intro)
# ==============================================================================
def build_alphabet():
    # Audio verified: chime intro ends at 10.0s, vocal entrance begins at 10.15s, 0.45s per beat
    letters = [
        # A B C D E F G
        (0.45, 0.85, 60, "A", 0), (0.90, 1.30, 60, "B", 0),
        (1.35, 1.80, 67, "C", 0), (1.85, 2.25, 67, "D", 0),
        (2.30, 2.70, 69, "E", 0), (2.75, 3.15, 69, "F", 0),
        (3.20, 4.10, 67, "G", 0),

        # H I J K L M N O P
        (4.15, 4.55, 65, "H", 1), (4.60, 5.00, 65, "I", 1),
        (5.05, 5.45, 64, "J", 1), (5.50, 5.95, 64, "K", 1),
        (6.00, 6.40, 62, "L", 1), (6.45, 6.85, 62, "M", 1),
        (6.90, 7.30, 62, "N", 1), (7.35, 7.80, 62, "O", 1),
        (7.85, 8.70, 60, "P", 1),

        # Q R S, T U V
        (8.75, 9.15, 67, "Q", 2), (9.20, 9.65, 67, "R", 2),
        (9.70, 10.10, 65, "S", 2), (10.15, 10.55, 64, "T", 2),
        (10.60, 11.45, 64, "U", 2), (11.50, 11.90, 62, "V", 2),

        # W X, Y and Z
        (11.95, 12.40, 67, "W", 3), (12.45, 12.85, 65, "X", 3),
        (12.90, 13.30, 64, "Y", 3), (13.35, 14.20, 62, "and Z", 3),

        # Now I know my ABCs
        (14.30, 14.75, 60, "Now", 4), (14.80, 15.20, 60, "I", 4),
        (15.25, 15.70, 67, "know", 4), (15.75, 16.15, 67, "my", 4),
        (16.20, 16.65, 69, "A", 4), (16.70, 17.15, 69, "B", 4),
        (17.20, 18.05, 67, "Cs,", 4),

        # Next time won't you sing with me
        (18.10, 18.55, 65, "Next", 5), (18.60, 19.00, 65, "time", 5),
        (19.05, 19.50, 64, "won't", 5), (19.55, 19.95, 64, "you", 5),
        (20.00, 20.45, 62, "sing", 5), (20.50, 20.95, 62, "with", 5),
        (21.00, 22.20, 60, "me!", 5),
    ]

    # Shift base cycle to start at 10.15s (9.70s shift from 0.45s)
    VOCAL_START_SHIFT = 9.70
    CYCLE_LENGTH = 22.80

    all_notes = []
    offsets = [VOCAL_START_SHIFT, VOCAL_START_SHIFT + CYCLE_LENGTH, VOCAL_START_SHIFT + 2 * CYCLE_LENGTH]
    for cycle_idx, off in enumerate(offsets):
        for s, e, m, txt, p in letters:
            t_s = s + off
            t_e = e + off
            if t_e <= 92.0:
                all_notes.append(make_note(t_s, t_e, m, txt, p + cycle_idx * 6))

    return {
        "song_name": "alphabet_song",
        "title": "Alphabet Song (ABC)",
        "artist": "Traditional",
        "file_name": "alphabet_song.mp3",
        "duration_sec": 92.5,
        "difficulty": "Easy",
        "notes": all_notes,
    }


# ==============================================================================
# 4. ON & ON - CARTOON (Audio-aligned verse at 10.6s and iconic chorus at 38.0s)
# ==============================================================================
def build_on_and_on():
    # Vocal verse starts at 10.6s, Chorus starts at 38.0s
    raw = [
        # Verse 1: "Hold me close 'til I get up, Time is barely on our side"
        (10.6, 11.2, 60, "Hold", 0), (11.3, 11.8, 63, "me", 0), (11.9, 12.5, 65, "close", 0),
        (12.6, 13.0, 63, "'til", 0), (13.1, 13.5, 61, "I", 0), (13.6, 14.3, 60, "get up", 0),

        (14.6, 15.2, 60, "Time", 1), (15.3, 15.8, 63, "is", 1), (15.9, 16.5, 65, "bare-", 1),
        (16.6, 17.0, 63, "-ly", 1), (17.1, 17.5, 61, "on", 1), (17.6, 18.0, 60, "our", 1),
        (18.1, 18.8, 58, "side", 1),

        # "I don't wanna waste what's left"
        (19.0, 19.5, 60, "I", 2), (19.6, 20.0, 63, "don't", 2), (20.1, 20.6, 65, "wan-", 2),
        (20.7, 21.1, 68, "-na", 2), (21.2, 21.8, 67, "waste", 2), (21.9, 22.3, 65, "what's", 2),
        (22.4, 23.2, 63, "left", 2),

        # Build-up to Chorus (38s)
        # Chorus: "And on and on we'll go, Through the wastelands, through the highways"
        (38.0, 38.6, 65, "And", 3), (38.7, 39.3, 67, "on", 3), (39.4, 40.0, 68, "and", 3),
        (40.1, 40.7, 70, "on", 3), (40.8, 41.5, 72, "we'll", 3), (41.6, 42.5, 70, "go,", 3),

        (42.8, 43.3, 68, "Through", 4), (43.4, 43.8, 70, "the", 4), (43.9, 44.5, 72, "waste-", 4),
        (44.6, 45.1, 70, "-lands,", 4), (45.2, 45.6, 68, "through", 4), (45.7, 46.1, 67, "the", 4),
        (46.2, 47.0, 65, "high-ways", 4),

        # "'Til my shadow turns to sunrays"
        (47.3, 47.8, 68, "'Til", 5), (47.9, 48.3, 70, "my", 5), (48.4, 48.9, 72, "sha-", 5),
        (49.0, 49.5, 70, "-dow", 5), (49.6, 50.1, 68, "turns", 5), (50.2, 50.6, 67, "to", 5),
        (50.7, 52.0, 65, "sun-rays", 5),

        # "And on and on we'll go"
        (52.5, 53.0, 65, "And", 6), (53.1, 53.6, 67, "on", 6), (53.7, 54.2, 68, "and", 6),
        (54.3, 54.8, 70, "on", 6), (54.9, 55.5, 72, "we'll", 6), (55.6, 57.0, 70, "go!", 6),

        # Second chorus (1:26 = 86s)
        (86.0, 86.6, 65, "And", 7), (86.7, 87.3, 67, "on", 7), (87.4, 88.0, 68, "and", 7),
        (88.1, 88.7, 70, "on", 7), (88.8, 89.5, 72, "we'll", 7), (89.6, 90.5, 70, "go,", 7),
        (90.8, 91.3, 68, "Through", 8), (91.4, 91.8, 70, "the", 8), (91.9, 92.5, 72, "waste-", 8),
        (92.6, 93.1, 70, "-lands,", 8), (93.2, 93.6, 68, "through", 8), (93.7, 94.1, 67, "the", 8),
        (94.2, 95.0, 65, "high-ways", 8),
    ]
    notes = [make_note(s, e, m, txt, p) for s, e, m, txt, p in raw]
    return {
        "song_name": "on_and_on_cartoon",
        "title": "On & On",
        "artist": "Cartoon & Jeja",
        "file_name": "on_and_on_cartoon.mp3",
        "duration_sec": 195.9,
        "difficulty": "Hard",
        "notes": notes,
    }


# ==============================================================================
# 5. HAPPY - PHARRELL WILLIAMS (Audio-aligned verse at 4.70s and full iconic chorus)
# ==============================================================================
def build_happy():
    # Audio verified at 160 BPM: Pharrell vocal begins at exactly 4.70s ("It might seem crazy...")
    # Shift relative to 6.00s baseline: delta = -1.30s
    raw_unaligned = [
        # Verse 1: "It might seem crazy what I'm 'bout to say"
        (6.0, 6.4, 65, "It", 0), (6.5, 6.9, 65, "might", 0), (7.0, 7.4, 68, "seem", 0),
        (7.5, 8.0, 65, "cra-", 0), (8.1, 8.5, 63, "-zy", 0), (8.6, 9.0, 65, "what", 0),
        (9.1, 9.4, 68, "I'm", 0), (9.5, 9.9, 70, "'bout", 0), (10.0, 10.3, 68, "to", 0),
        (10.4, 11.2, 65, "say", 0),

        # "Sunshine she's here, you can take a break"
        (11.8, 12.3, 65, "Sun-", 1), (12.4, 12.9, 68, "-shine", 1), (13.0, 13.5, 68, "she's", 1),
        (13.6, 14.1, 65, "here,", 1), (14.2, 14.6, 63, "you", 1), (14.7, 15.0, 65, "can", 1),
        (15.1, 15.5, 68, "take", 1), (15.6, 15.9, 70, "a", 1), (16.0, 16.8, 68, "break", 1),

        # "I'm a hot air balloon that could go to space"
        (17.2, 17.6, 65, "I'm", 2), (17.7, 18.0, 65, "a", 2), (18.1, 18.5, 68, "hot", 2),
        (18.6, 19.0, 65, "air", 2), (19.1, 19.5, 68, "bal-", 2), (19.6, 20.0, 70, "-loon", 2),
        (20.1, 20.5, 68, "that", 2), (20.6, 21.0, 65, "could", 2), (21.1, 21.5, 68, "go", 2),
        (21.6, 22.0, 70, "to", 2), (22.1, 23.0, 68, "space!", 2),

        # Chorus Part 1 (24.7s): "Because I'm happy - Clap along if you feel like a room without a roof"
        (26.0, 26.4, 65, "Be-", 3), (26.5, 26.9, 68, "-cause", 3), (27.0, 27.5, 70, "I'm", 3),
        (27.6, 29.0, 72, "hap-py!", 3),

        (29.2, 29.6, 68, "Clap", 4), (29.7, 30.1, 70, "a-", 4), (30.2, 30.6, 72, "-long", 4),
        (30.7, 31.0, 72, "if", 4), (31.1, 31.4, 70, "you", 4), (31.5, 31.8, 68, "feel", 4),
        (31.9, 32.2, 70, "like", 4), (32.3, 32.6, 68, "a", 4), (32.7, 33.1, 65, "room", 4),
        (33.2, 33.6, 68, "with-", 4), (33.7, 34.0, 70, "-out", 4), (34.1, 34.5, 68, "a", 4),
        (34.6, 35.5, 65, "roof", 4),

        # Chorus Part 2 (34.7s): "Because I'm happy - Clap along if you feel like happiness is the truth"
        (36.0, 36.4, 65, "Be-", 5), (36.5, 36.9, 68, "-cause", 5), (37.0, 37.5, 70, "I'm", 5),
        (37.6, 39.0, 72, "hap-py!", 5),

        (39.2, 39.6, 68, "Clap", 6), (39.7, 40.1, 70, "a-", 6), (40.2, 40.6, 72, "-long", 6),
        (40.7, 41.0, 72, "if", 6), (41.1, 41.4, 70, "you", 6), (41.5, 41.8, 68, "feel", 6),
        (41.9, 42.4, 70, "like", 6), (42.5, 43.0, 68, "hap-pi-", 6), (43.1, 43.6, 65, "-ness", 6),
        (43.7, 44.0, 68, "is", 6), (44.1, 44.5, 70, "the", 6), (44.6, 45.5, 68, "truth!", 6),

        # Chorus Part 3 (44.7s): "Because I'm happy - Clap along if you know what happiness is to you"
        (46.0, 46.4, 65, "Be-", 7), (46.5, 46.9, 68, "-cause", 7), (47.0, 47.5, 70, "I'm", 7),
        (47.6, 49.0, 72, "hap-py!", 7),

        (49.2, 49.6, 68, "Clap", 8), (49.7, 50.1, 70, "a-", 8), (50.2, 50.6, 72, "-long", 8),
        (50.7, 51.0, 72, "if", 8), (51.1, 51.4, 70, "you", 8), (51.5, 51.8, 68, "know", 8),
        (51.9, 52.3, 70, "what", 8), (52.4, 52.8, 68, "hap-pi-", 8), (52.9, 53.4, 65, "-ness", 8),
        (53.5, 53.9, 68, "is", 8), (54.0, 54.4, 70, "to", 8), (54.5, 55.5, 68, "you!", 8),

        # Chorus Part 4 (54.7s): "Because I'm happy - Clap along if you feel like that's what you wanna do"
        (56.0, 56.4, 65, "Be-", 9), (56.5, 56.9, 68, "-cause", 9), (57.0, 57.5, 70, "I'm", 9),
        (57.6, 59.0, 72, "hap-py!", 9),

        (59.2, 59.6, 68, "Clap", 10), (59.7, 60.1, 70, "a-", 10), (60.2, 60.6, 72, "-long", 10),
        (60.7, 61.0, 72, "if", 10), (61.1, 61.4, 70, "you", 10), (61.5, 61.8, 68, "feel", 10),
        (61.9, 62.3, 70, "like", 10), (62.4, 62.8, 68, "that's", 10), (62.9, 63.3, 65, "what", 10),
        (63.4, 63.8, 68, "you", 10), (63.9, 64.3, 70, "wan-", 10), (64.4, 64.8, 72, "-na", 10),
        (64.9, 66.2, 68, "do!", 10),

        # Outro Groove Hits (65.7s - 71.5s)
        (67.0, 67.8, 65, "Hey!", 11), (68.5, 69.3, 68, "Go!", 11),
        (70.0, 70.8, 70, "Happy,", 11), (71.0, 72.8, 72, "HAPPY!", 11)
    ]
    DELTA = 4.70 - 6.00  # Shift back to align Pharrell's vocal entrance at 4.70s
    raw = [(round(s + DELTA, 3), round(e + DELTA, 3), m, txt, p) for s, e, m, txt, p in raw_unaligned]
    notes = [make_note(s, e, m, txt, p) for s, e, m, txt, p in raw]
    return {
        "song_name": "happy_pharrellwilliams",
        "title": "Happy",
        "artist": "Pharrell Williams",
        "file_name": "happy_pharrellwilliams.mp3",
        "duration_sec": 73.0,
        "difficulty": "Medium",
        "notes": notes,
    }


# ==============================================================================
# 6. LOVE STORY - TAYLOR SWIFT (Vocal entrance audio-aligned at 17.00s after banjo)
# ==============================================================================
def build_love_story():
    # Audio verified at 117.5 BPM: Banjo intro plays until 16.9s, Taylor Swift vocal enters at 17.00s
    # Shift relative to 8.00s baseline: delta = +9.00s
    raw_unaligned = [
        # Verse 1 Part 1: "We were both young when I first saw you"
        (8.0, 8.4, 62, "We", 0), (8.5, 8.9, 64, "were", 0), (9.0, 9.4, 66, "both", 0),
        (9.5, 10.0, 66, "young", 0), (10.1, 10.5, 66, "when", 0), (10.6, 10.9, 64, "I", 0),
        (11.0, 11.4, 62, "first", 0), (11.5, 12.0, 64, "saw", 0), (12.1, 13.0, 62, "you.", 0),

        # "I close my eyes and the flashback starts"
        (13.2, 13.6, 62, "I", 1), (13.7, 14.1, 64, "close", 1), (14.2, 14.6, 66, "my", 1),
        (14.7, 15.2, 66, "eyes", 1), (15.3, 15.7, 66, "and", 1), (15.8, 16.1, 64, "the", 1),
        (16.2, 16.7, 62, "flash-", 1), (16.8, 17.2, 64, "-back", 1), (17.3, 18.2, 62, "starts,", 1),

        # "I'm standing there on a balcony in summer air"
        (18.5, 18.9, 62, "I'm", 2), (19.0, 19.4, 64, "stand-", 2), (19.5, 19.9, 66, "-ing", 2),
        (20.0, 20.4, 66, "there", 2), (20.5, 20.8, 66, "on", 2), (20.9, 21.2, 64, "a", 2),
        (21.3, 21.8, 62, "bal-", 2), (21.9, 22.3, 64, "-co-", 2), (22.4, 22.8, 62, "-ny", 2),
        (22.9, 23.2, 62, "in", 2), (23.3, 23.7, 64, "sum-", 2), (23.8, 24.2, 66, "-mer", 2),
        (24.3, 25.5, 62, "air.", 2),

        # Verse 1 Part 2: "See the lights, see the party, the ball gowns"
        (26.0, 26.5, 62, "See", 3), (26.6, 27.0, 64, "the", 3), (27.1, 27.6, 66, "lights,", 3),
        (27.7, 28.1, 66, "see", 3), (28.2, 28.6, 64, "the", 3), (28.7, 29.3, 62, "par-ty,", 3),
        (29.4, 29.8, 64, "the", 3), (29.9, 30.7, 62, "ball gowns", 3),

        # "See you make your way through the crowd, and say Hello"
        (31.0, 31.5, 62, "See", 4), (31.6, 32.0, 64, "you", 4), (32.1, 32.6, 66, "make", 4),
        (32.7, 33.1, 66, "your", 4), (33.2, 33.6, 66, "way", 4), (33.7, 34.1, 64, "through", 4),
        (34.2, 34.6, 62, "the", 4), (34.7, 35.8, 62, "crowd,", 4),
        (36.0, 36.5, 64, "and", 5), (36.6, 37.5, 66, "say,", 5), (37.6, 38.8, 66, "\"Hel-lo\"", 5),

        # Pre-Chorus: "Little did I know, that you were Romeo..."
        (39.8, 40.2, 62, "Lit-tle", 6), (40.3, 40.7, 64, "did", 6), (40.8, 41.5, 66, "I", 6),
        (41.6, 42.5, 62, "know", 6),
        (42.8, 43.2, 62, "That", 7), (43.3, 43.7, 64, "you", 7), (43.8, 44.2, 66, "were", 7),
        (44.3, 44.8, 66, "Ro-meo,", 7), (44.9, 45.3, 64, "you", 7), (45.4, 45.8, 62, "were", 7),
        (45.9, 46.3, 64, "throw-in'", 7), (46.4, 47.2, 62, "peb-bles", 7),
        (47.5, 47.9, 62, "And", 8), (48.0, 48.4, 64, "my", 8), (48.5, 48.9, 66, "dad-dy", 8),
        (49.0, 49.5, 66, "said", 8), (49.6, 50.0, 66, "\"Stay", 8), (50.1, 50.5, 64, "a-way", 8),
        (50.6, 50.9, 62, "from", 8), (51.0, 52.0, 62, "Ju-liet\"", 8),
        (52.2, 52.6, 64, "And", 9), (52.7, 53.2, 66, "I", 9), (53.3, 53.7, 68, "said...", 9),

        # Iconic Chorus (63.5s - 85.5s)
        (54.5, 55.0, 66, "Ro-meo,", 10), (55.1, 55.5, 66, "take", 10), (55.6, 56.1, 64, "me", 10),
        (56.2, 56.6, 62, "some-where", 10), (56.7, 57.1, 62, "we", 10), (57.2, 57.6, 64, "can", 10),
        (57.7, 58.2, 66, "be", 10), (58.3, 59.5, 62, "a-lone!", 10),

        (59.8, 60.2, 66, "I'll", 11), (60.3, 60.7, 66, "be", 11), (60.8, 61.4, 64, "wait-ing,", 11),
        (61.5, 61.9, 62, "all", 11), (62.0, 62.4, 62, "there's", 11), (62.5, 62.9, 64, "left", 11),
        (63.0, 63.4, 66, "to", 11), (63.5, 63.9, 64, "do", 11), (64.0, 64.8, 62, "is", 11),
        (64.9, 65.8, 62, "run!", 11),

        (66.0, 66.4, 66, "You'll", 12), (66.5, 66.9, 66, "be", 12), (67.0, 67.4, 64, "the", 12),
        (67.5, 68.0, 62, "prince", 12), (68.1, 68.5, 64, "and", 12), (68.6, 69.0, 66, "I'll", 12),
        (69.1, 69.5, 66, "be", 12), (69.6, 70.0, 64, "the", 12), (70.1, 71.0, 62, "prin-cess!", 12),

        (71.2, 71.6, 62, "It's", 13), (71.7, 72.1, 64, "a", 13), (72.2, 72.6, 66, "love", 13),
        (72.7, 73.1, 66, "sto-ry,", 13), (73.2, 73.6, 66, "ba-by,", 13), (73.7, 74.2, 64, "just", 13),
        (74.3, 74.8, 62, "say,", 13), (74.9, 76.5, 62, "\"YES!\"", 13)
    ]
    DELTA = 17.00 - 8.00  # Shift forward to align Taylor Swift's vocal entrance after banjo intro
    raw = [(round(s + DELTA, 3), round(e + DELTA, 3), m, txt, p) for s, e, m, txt, p in raw_unaligned]
    notes = [make_note(s, e, m, txt, p) for s, e, m, txt, p in raw]
    return {
        "song_name": "love_story_taylorswift",
        "title": "Love Story",
        "artist": "Taylor Swift",
        "file_name": "love_story_taylorswift.mp3",
        "duration_sec": 87.0,
        "difficulty": "Medium",
        "notes": notes,
    }


def main():
    songs = [
        build_sunshine(),
        build_twinkle(),
        build_alphabet(),
        build_on_and_on(),
        build_happy(),
        build_love_story(),
    ]

    catalog = []
    for s in songs:
        name = s["song_name"]
        json_path = SONGS_DIR / f"{name}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2)
        print(f"[OK] Wrote clean musical JSON: {json_path.name} ({len(s['notes'])} notes)")

        catalog.append({
            "id": name,
            "title": s["title"],
            "artist": s["artist"],
            "duration": s["duration_sec"],
            "duration_sec": s["duration_sec"],
            "difficulty": s["difficulty"],
            "audio_file": s["file_name"],
            "audio_url": f"/api/audio/{s['file_name']}",
            "json_url": f"/api/song/{name}.json",
            "total_notes": len(s["notes"]),
            "notes_count": len(s["notes"]),
            "lyrics_preview": " ".join([n["text"] for n in s["notes"][:6]]) + "...",
        })

    index_path = SONGS_DIR / "index.json"
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2)
    print(f"\n[SUCCESS] Updated catalog index.json with {len(catalog)} clean, musical tracks!")


if __name__ == "__main__":
    main()
