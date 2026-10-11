"""Lyrics transcription and lyric-to-pitch alignment module using Whisper."""

from pathlib import Path
import numpy as np
import librosa
import whisper


_WHISPER_MODELS = {}


def get_whisper_model(model_name: str = "small"):
    """Load and cache the Whisper model in memory."""
    global _WHISPER_MODELS
    if model_name not in _WHISPER_MODELS:
        _WHISPER_MODELS[model_name] = whisper.load_model(model_name)
    return _WHISPER_MODELS[model_name]


def transcribe_lyrics(
    audio_path: str | Path,
    model_name: str = "small",
    language: str = "en",
    prompt: str | None = None,
    filter_watermarks: bool = True,
) -> list[dict]:
    """Transcribe lyrics from an audio file with timestamps for lines and individual words.

    Parameters:
        audio_path: Path to the audio file (.mp3, .wav)
        model_name: Whisper model size ('tiny', 'base', 'small')
        language: Language code (default 'en')
        prompt: Optional ground-truth lyrics string to guide speech recognition
        filter_watermarks: If True, filters out non-singing watermarks like 'audio jungle'

    Returns:
        list of lyric line dictionaries:
        [{
            'text': str,
            'start': float,
            'end': float,
            'words': [{'word': str, 'start': float, 'end': float}]
        }]
    """
    path_obj = Path(audio_path)
    if not path_obj.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    # Check for corresponding .txt or .lrc lyrics file in the same directory
    if prompt is None:
        txt_path = path_obj.with_suffix(".txt")
        lrc_path = path_obj.with_suffix(".lrc")
        if txt_path.exists():
            prompt = txt_path.read_text(encoding="utf-8").strip()
        elif lrc_path.exists():
            prompt = lrc_path.read_text(encoding="utf-8").strip()

    # Load audio via librosa at 16,000 Hz to avoid needing external ffmpeg CLI
    audio_16k, _ = librosa.load(str(path_obj), sr=16000, mono=True)

    model = get_whisper_model(model_name)
    transcribe_kwargs = {
        "word_timestamps": True,
        "language": language,
        "fp16": False,
    }
    if prompt:
        transcribe_kwargs["initial_prompt"] = prompt

    result = model.transcribe(audio_16k, **transcribe_kwargs)

    watermark_words = {"audio", "jungle"} if filter_watermarks else set()

    lyrics = []
    for seg in result.get("segments", []):
        raw_text = seg["text"].strip()
        if not raw_text:
            continue

        words = []
        for w in seg.get("words", []):
            cleaned_word = w["word"].strip().strip(" ,.!?\"'-")
            if not cleaned_word:
                continue

            # Skip audio jungle watermarks
            if filter_watermarks and cleaned_word.lower() in watermark_words:
                continue

            words.append({
                "word": cleaned_word,
                "start": round(float(w["start"]), 3),
                "end": round(float(w["end"]), 3),
            })

        if not words:
            continue

        # Reconstruct clean line text
        line_clean = " ".join(w["word"] for w in words)
        lyrics.append({
            "text": line_clean,
            "start": round(float(words[0]["start"]), 3),
            "end": round(float(words[-1]["end"]), 3),
            "words": words,
        })

    return lyrics


def attach_pitches_to_lyrics(
    lyrics: list[dict],
    times: list[float] | np.ndarray,
    midi_pitches: list[float] | np.ndarray,
    pitch_hz: list[float] | np.ndarray
) -> list[dict]:
    """Map expected pitches and notes to each transcribed word and lyric phrase.

    Parameters:
        lyrics: Output from transcribe_lyrics()
        times: Array/list of frame timestamps
        midi_pitches: Array/list of continuous MIDI pitches per frame
        pitch_hz: Array/list of frequency in Hz per frame

    Returns:
        Updated lyrics list with expected_note, expected_midi, expected_hz on each word & line.
    """
    times_arr = np.array(times)
    midi_arr = np.array(midi_pitches)
    hz_arr = np.array(pitch_hz)

    enriched_lyrics = []
    for line in lyrics:
        line_start = line["start"]
        line_end = line["end"]

        # Mask frames falling in line duration
        mask = (times_arr >= line_start) & (times_arr <= line_end) & (midi_arr > 0)
        if np.any(mask):
            med_midi = float(np.median(midi_arr[mask]))
            med_hz = float(np.median(hz_arr[mask]))
            note_str = librosa.midi_to_note(int(round(med_midi)))
        else:
            med_midi = 0.0
            med_hz = 0.0
            note_str = "N/A"

        # Enrich individual words
        enriched_words = []
        for w in line.get("words", []):
            w_start, w_end = w["start"], w["end"]
            w_mask = (times_arr >= w_start) & (times_arr <= w_end) & (midi_arr > 0)
            if np.any(w_mask):
                w_midi = float(np.median(midi_arr[w_mask]))
                w_hz = float(np.median(hz_arr[w_mask]))
                w_note = librosa.midi_to_note(int(round(w_midi)))
            else:
                w_midi = 0.0
                w_hz = 0.0
                w_note = "N/A"

            enriched_words.append({
                "word": w["word"],
                "start": w["start"],
                "end": w["end"],
                "expected_midi": round(w_midi, 2),
                "expected_hz": round(w_hz, 1),
                "expected_note": w_note,
            })

        enriched_lyrics.append({
            "text": line["text"],
            "start": line_start,
            "end": line_end,
            "expected_midi": round(med_midi, 2),
            "expected_hz": round(med_hz, 1),
            "expected_note": note_str,
            "words": enriched_words,
        })

    return enriched_lyrics
