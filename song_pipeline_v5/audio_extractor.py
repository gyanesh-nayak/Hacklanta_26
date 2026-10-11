"""Audio feature extraction module for reference songs and vocal covers."""

from pathlib import Path
import json
import numpy as np
import librosa


def extract_song_data(
    audio_path: str | Path,
    sr: int = 22050,
    fmin: float = 65.0,     # ~C2
    fmax: float = 1050.0,   # ~C6
    hop_length: int = 512,
    min_note_duration: float = 0.12,
    include_lyrics: bool = False,
    whisper_model: str = "small",
) -> dict:
    """Extract pitch contours, voiced regions, discrete musical notes, and lyrics from an audio file.

    Parameters:
        audio_path: Path to the audio file (.mp3, .wav, etc.)
        sr: Sampling rate to load audio (default: 22050 Hz)
        fmin: Minimum frequency in Hz for pitch detection
        fmax: Maximum frequency in Hz for pitch detection
        hop_length: Hop length in samples for frame analysis
        min_note_duration: Minimum duration in seconds to consider a stable segment a note
        include_lyrics: If True, transcribes lyrics and links expected pitches to each lyric word
        whisper_model: Whisper model size ('tiny', 'base', 'small')
    """
    path_obj = Path(audio_path)
    if not path_obj.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    # 1. Load audio
    y, sr_actual = librosa.load(str(path_obj), sr=sr, mono=True)
    duration = float(len(y) / sr_actual)

    # 2. Separate harmonic content to reduce drum/percussion interference
    y_harm, _ = librosa.effects.hpss(y)

    # 3. Extract pitch contour using probabilistic YIN (pYIN)
    f0, voiced_flag, voiced_probs = librosa.pyin(
        y_harm,
        fmin=fmin,
        fmax=fmax,
        sr=sr_actual,
        hop_length=hop_length,
        fill_na=0.0
    )

    times = librosa.times_like(f0, sr=sr_actual, hop_length=hop_length)

    # Compute continuous MIDI pitch contour
    midi_contour = np.zeros_like(f0)
    valid_mask = (f0 > 0) & np.isfinite(f0) & (voiced_flag == True)
    midi_contour[valid_mask] = librosa.hz_to_midi(f0[valid_mask])

    # 4. Segment continuous voiced frames into discrete musical notes
    notes = _segment_notes(
        times=times,
        f0=f0,
        voiced_mask=valid_mask,
        min_duration=min_note_duration
    )

    # 5. Summary statistics
    voiced_midi = midi_contour[valid_mask]
    if len(voiced_midi) > 0:
        median_midi = float(np.median(voiced_midi))
        min_midi = float(np.min(voiced_midi))
        max_midi = float(np.max(voiced_midi))
        key_approx = librosa.midi_to_note(int(round(median_midi)))
    else:
        median_midi = min_midi = max_midi = 0.0
        key_approx = "N/A"

    times_list = [round(float(t), 3) for t in times]
    pitch_hz_list = [round(float(f), 2) for f in f0]
    midi_pitches_list = [round(float(m), 2) for m in midi_contour]

    # 6. Lyrics Transcription and Word-Level Pitch Mapping
    lyrics_data = []
    if include_lyrics:
        try:
            from lyrics_transcriber import transcribe_lyrics, attach_pitches_to_lyrics
            raw_lyrics = transcribe_lyrics(path_obj, model_name=whisper_model)
            lyrics_data = attach_pitches_to_lyrics(
                raw_lyrics,
                times=times_list,
                midi_pitches=midi_pitches_list,
                pitch_hz=pitch_hz_list
            )
        except Exception as e:
            print(f"Warning: Lyrics transcription skipped or failed: {e}")

    return {
        "song_name": path_obj.stem,
        "file_name": path_obj.name,
        "duration_sec": round(duration, 3),
        "sample_rate": sr_actual,
        "hop_length": hop_length,
        "times": times_list,
        "pitch_hz": pitch_hz_list,
        "midi_pitches": midi_pitches_list,
        "voiced_mask": [bool(v) for v in valid_mask],
        "notes": notes,
        "lyrics": lyrics_data,
        "note_summary": {
            "total_notes": len(notes),
            "total_lyrics": len(lyrics_data),
            "voiced_ratio": round(float(np.mean(valid_mask)), 3),
            "min_midi": round(min_midi, 1),
            "max_midi": round(max_midi, 1),
            "median_midi": round(median_midi, 1),
            "median_note": key_approx,
        }
    }


def _segment_notes(
    times: np.ndarray,
    f0: np.ndarray,
    voiced_mask: np.ndarray,
    min_duration: float = 0.12,
) -> list[dict]:
    """Group consecutive voiced frames with similar pitch into discrete note events."""
    notes = []
    curr_midi = None
    curr_start = None
    curr_pitches = []

    for t, f, is_voiced in zip(times, f0, voiced_mask):
        if is_voiced:
            midi_rounded = int(round(float(librosa.hz_to_midi(f))))
            if curr_midi is None:
                curr_midi = midi_rounded
                curr_start = t
                curr_pitches = [f]
            elif midi_rounded == curr_midi:
                curr_pitches.append(f)
            else:
                # Note changed
                dur = t - curr_start
                if dur >= min_duration:
                    avg_hz = float(np.median(curr_pitches))
                    notes.append({
                        "start": round(float(curr_start), 3),
                        "end": round(float(t), 3),
                        "duration": round(float(dur), 3),
                        "note": librosa.midi_to_note(curr_midi),
                        "midi": curr_midi,
                        "pitch_hz": round(avg_hz, 1)
                    })
                curr_midi = midi_rounded
                curr_start = t
                curr_pitches = [f]
        else:
            if curr_midi is not None:
                dur = t - curr_start
                if dur >= min_duration:
                    avg_hz = float(np.median(curr_pitches))
                    notes.append({
                        "start": round(float(curr_start), 3),
                        "end": round(float(t), 3),
                        "duration": round(float(dur), 3),
                        "note": librosa.midi_to_note(curr_midi),
                        "midi": curr_midi,
                        "pitch_hz": round(avg_hz, 1)
                    })
                curr_midi = None
                curr_start = None
                curr_pitches = []

    # Final segment if still open
    if curr_midi is not None and curr_start is not None:
        dur = times[-1] - curr_start
        if dur >= min_duration:
            avg_hz = float(np.median(curr_pitches))
            notes.append({
                "start": round(float(curr_start), 3),
                "end": round(float(times[-1]), 3),
                "duration": round(float(dur), 3),
                "note": librosa.midi_to_note(curr_midi),
                "midi": curr_midi,
                "pitch_hz": round(avg_hz, 1)
            })

    return notes


def save_song_data(data: dict, output_path: str | Path) -> str:
    """Save extracted song features to a JSON file."""
    path_obj = Path(output_path)
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    with open(path_obj, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return str(path_obj)


def load_song_data(file_path: str | Path) -> dict:
    """Load previously extracted song features from a JSON file."""
    path_obj = Path(file_path)
    if not path_obj.exists():
        raise FileNotFoundError(f"Song feature file not found: {file_path}")
    with open(path_obj, "r", encoding="utf-8") as f:
        return json.load(f)
