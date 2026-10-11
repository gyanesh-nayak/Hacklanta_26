"""Cover evaluation module: compares user cover performance against reference song data."""

from pathlib import Path
import numpy as np
import librosa

from audio_extractor import extract_song_data, load_song_data


def evaluate_cover(
    reference: dict | str | Path,
    cover: dict | str | Path,
    allow_octave_shift: bool = True,
    octave_shift_semitones: int | None = None,
) -> dict:
    """Compare a user's cover against a reference song and calculate accuracy scores.

    Parameters:
        reference: Reference song data dict, or path to reference .json / audio file
        cover: User cover data dict, or path to cover audio file / .json
        allow_octave_shift: If True, automatically detects and adjusts for octave differences
                            (e.g., male singing female vocal track 1 octave lower)
        octave_shift_semitones: If specified, manually shifts reference by this many semitones.

    Returns:
        dict containing:
            - overall_score: Final weighted score (0.0 - 100.0)
            - letter_grade: 'S', 'A', 'B', 'C', 'D', or 'F'
            - pitch_score: Pitch accuracy score (0.0 - 100.0)
            - timing_score: Rhythm & timing alignment score (0.0 - 100.0)
            - note_score: Percentage of discrete notes hit accurately (0.0 - 100.0)
            - octave_detected_shift: Detected octave shift in semitones (e.g. -12, 0, 12)
            - pitch_tendency: 'Sharp', 'Flat', or 'On Pitch'
            - mean_cents_error: Average pitch error in cents (100 cents = 1 semitone)
            - notes_hit_summary: {total_notes, hit_count, close_count, miss_count}
            - note_details: Detailed evaluation for each reference note
            - feedback: List of user-facing feedback bullet points
    """
    ref_data = _resolve_song_data(reference)
    cov_data = _resolve_song_data(cover)

    ref_midi = np.array(ref_data["midi_pitches"], dtype=np.float64)
    cov_midi = np.array(cov_data["midi_pitches"], dtype=np.float64)
    ref_times = np.array(ref_data["times"], dtype=np.float64)
    cov_times = np.array(cov_data["times"], dtype=np.float64)

    ref_voiced = ref_midi > 0
    cov_voiced = cov_midi > 0

    if np.sum(cov_voiced) == 0:
        return _empty_evaluation_result("No voiced singing detected in cover audio.")

    # 1. Detect / Apply Octave Shift (e.g. 12 semitones down for male covering female track)
    detected_shift = 0
    if octave_shift_semitones is not None:
        detected_shift = octave_shift_semitones
    elif allow_octave_shift:
        ref_median = np.median(ref_midi[ref_voiced]) if np.any(ref_voiced) else 60.0
        cov_median = np.median(cov_midi[cov_voiced])
        diff = cov_median - ref_median
        # Round diff to nearest octave (-24, -12, 0, 12, 24)
        octave_diff = int(round(diff / 12.0)) * 12
        if abs(octave_diff) <= 24:
            detected_shift = octave_diff

    adjusted_cov_midi = cov_midi.copy()
    adjusted_cov_midi[cov_voiced] -= detected_shift

    # 2. Dynamic Time Warping (DTW) alignment between pitch contours
    # Resample or smooth contours slightly to align time
    warp_ref_idx, warp_cov_idx = _align_pitch_contours(ref_midi, adjusted_cov_midi)

    # 3. Frame-Level Pitch Accuracy
    aligned_ref_midi = ref_midi[warp_ref_idx]
    aligned_cov_midi = adjusted_cov_midi[warp_cov_idx]

    # Evaluate voiced frames where both reference and user have pitch
    both_voiced = (aligned_ref_midi > 0) & (aligned_cov_midi > 0)
    
    if np.sum(both_voiced) > 0:
        semitone_errors = aligned_cov_midi[both_voiced] - aligned_ref_midi[both_voiced]
        abs_semitone_errors = np.abs(semitone_errors)
        cents_errors = abs_semitone_errors * 100.0
        mean_cents_error = float(np.mean(cents_errors))

        # Tendency: Flat or Sharp?
        mean_bias = float(np.mean(semitone_errors))
        if mean_bias > 0.2:
            pitch_tendency = "Sharp"
        elif mean_bias < -0.2:
            pitch_tendency = "Flat"
        else:
            pitch_tendency = "On Pitch"

        # Frame pitch score: 0 to 100 based on cents error
        # <= 25 cents: 100%, 50 cents: 90%, 100 cents (1 semitone): 65%, 200 cents: 20%
        frame_scores = np.clip(100.0 * np.exp(- (abs_semitone_errors / 0.8) ** 2), 0.0, 100.0)
        pitch_score = float(np.mean(frame_scores))
    else:
        mean_cents_error = 200.0
        pitch_tendency = "Off Pitch"
        pitch_score = 0.0

    # 4. Timing / Rhythm Alignment Score
    # Compare warping path to ideal diagonal (slope = 1)
    ref_span = max(len(ref_midi), 1)
    cov_span = max(len(cov_midi), 1)
    ideal_cov_idx = warp_ref_idx * (cov_span / ref_span)
    timing_displacements = np.abs(warp_cov_idx - ideal_cov_idx) / cov_span
    # Average normalized time displacement
    mean_displacement = float(np.mean(timing_displacements))
    timing_score = max(0.0, min(100.0, 100.0 * (1.0 - mean_displacement * 3.5)))

    # 5. Note-Level Evaluation (Discrete musical notes)
    ref_notes = ref_data.get("notes", [])
    note_results = []
    hits = 0
    closes = 0
    misses = 0

    for note in ref_notes:
        n_start, n_end, target_midi = note["start"], note["end"], note["midi"]
        # Find corresponding frames in cover via aligned mapping
        # Mask reference frames within note start and end
        ref_frame_mask = (ref_times >= n_start) & (ref_times <= n_end)
        ref_indices = np.where(ref_frame_mask)[0]

        if len(ref_indices) == 0:
            continue

        # Aligned cover frames
        cov_frames_for_note = warp_cov_idx[np.isin(warp_ref_idx, ref_indices)]
        note_cov_midi = adjusted_cov_midi[cov_frames_for_note]
        voiced_cov_note = note_cov_midi[note_cov_midi > 0]

        if len(voiced_cov_note) == 0 or len(voiced_cov_note) < 0.25 * len(ref_indices):
            status = "MISS"
            misses += 1
            cents_diff = None
            sung_note = None
        else:
            user_sung_midi = float(np.median(voiced_cov_note))
            diff_semi = user_sung_midi - target_midi
            cents_diff = round(diff_semi * 100.0, 1)
            sung_rounded_midi = int(round(user_sung_midi + detected_shift))
            sung_note = librosa.midi_to_note(sung_rounded_midi)

            if abs(diff_semi) <= 0.5:
                status = "HIT"
                hits += 1
            elif abs(diff_semi) <= 1.0:
                status = "CLOSE"
                closes += 1
            else:
                status = "MISS"
                misses += 1

        note_results.append({
            "target_note": note["note"],
            "target_midi": target_midi,
            "start_time": note["start"],
            "end_time": note["end"],
            "sung_note": sung_note,
            "status": status,
            "cents_diff": cents_diff,
        })

    total_notes = len(note_results)
    if total_notes > 0:
        note_score = float(((hits * 1.0) + (closes * 0.5)) / total_notes * 100.0)
    else:
        note_score = pitch_score

    # 6. Lyric-by-Lyric Pitch Evaluation (Comparing pitch at each sung word)
    ref_lyrics = ref_data.get("lyrics", [])
    lyric_results = []
    lyric_words_total = 0
    lyric_words_hit = 0
    lyric_words_close = 0
    lyric_words_miss = 0

    for line in ref_lyrics:
        line_word_evals = []
        for w in line.get("words", []):
            w_start, w_end = w["start"], w["end"]
            w_target_midi = w.get("expected_midi", 0.0)
            w_target_note = w.get("expected_note", "N/A")

            # Reference frames for this word
            w_ref_mask = (ref_times >= w_start) & (ref_times <= w_end)
            w_ref_idx = np.where(w_ref_mask)[0]

            if len(w_ref_idx) == 0:
                continue

            # Aligned cover frames
            w_cov_idx = warp_cov_idx[np.isin(warp_ref_idx, w_ref_idx)]
            w_cov_pitches = adjusted_cov_midi[w_cov_idx]
            w_voiced = w_cov_pitches[w_cov_pitches > 0]

            lyric_words_total += 1
            if len(w_voiced) == 0 or len(w_voiced) < 0.25 * len(w_ref_idx) or w_target_midi == 0:
                w_status = "MISSED"
                w_sung_note = None
                w_cents_diff = None
                lyric_words_miss += 1
            else:
                user_w_midi = float(np.median(w_voiced))
                diff_semi = user_w_midi - w_target_midi
                w_cents_diff = round(diff_semi * 100.0, 1)
                user_sung_rounded = int(round(user_w_midi + detected_shift))
                w_sung_note = librosa.midi_to_note(user_sung_rounded)

                if abs(diff_semi) <= 0.5:
                    w_status = "HIT"
                    lyric_words_hit += 1
                elif abs(diff_semi) <= 1.0:
                    w_status = "CLOSE"
                    lyric_words_close += 1
                else:
                    w_status = "OFF_KEY"
                    lyric_words_miss += 1

            line_word_evals.append({
                "word": w["word"],
                "start": w_start,
                "end": w_end,
                "expected_note": w_target_note,
                "sung_note": w_sung_note,
                "cents_diff": w_cents_diff,
                "status": w_status,
            })

        lyric_results.append({
            "line_text": line["text"],
            "start": line["start"],
            "end": line["end"],
            "expected_note": line.get("expected_note", "N/A"),
            "words": line_word_evals,
        })

    if lyric_words_total > 0:
        lyric_score = float(((lyric_words_hit * 1.0) + (lyric_words_close * 0.5)) / lyric_words_total * 100.0)
    else:
        lyric_score = None

    # 7. Overall Weighted Score & Letter Grade
    if lyric_score is not None:
        overall_score = round(
            (0.40 * pitch_score) + (0.35 * lyric_score) + (0.15 * timing_score) + (0.10 * note_score),
            1
        )
    else:
        overall_score = round(
            (0.55 * pitch_score) + (0.30 * note_score) + (0.15 * timing_score),
            1
        )

    if overall_score >= 93:
        letter_grade = "S"
    elif overall_score >= 85:
        letter_grade = "A"
    elif overall_score >= 75:
        letter_grade = "B"
    elif overall_score >= 65:
        letter_grade = "C"
    elif overall_score >= 50:
        letter_grade = "D"
    else:
        letter_grade = "F"

    # 8. Actionable Feedback
    feedback = []
    if detected_shift != 0:
        octaves = abs(detected_shift) // 12
        direction = "lower" if detected_shift < 0 else "higher"
        feedback.append(f"Singing detected {octaves} octave ({abs(detected_shift)} semitones) {direction} than the original reference (normalized).")

    if pitch_score >= 88:
        feedback.append("Great pitch accuracy! You stayed well in tune with the melody.")
    elif pitch_score >= 70:
        feedback.append(f"Decent pitch control, with a slight tendency to be {pitch_tendency.lower()}.")
    else:
        feedback.append(f"Pitch was frequently off-key ({pitch_tendency.lower()}). Focus on sustaining steady tones.")

    if timing_score >= 85:
        feedback.append("Solid rhythm and timing: your notes lined up nicely with the original tempo.")
    else:
        feedback.append("Timing varied from the reference. Watch the entry points and note durations.")

    if lyric_words_total > 0:
        lyric_pct = round((lyric_words_hit / lyric_words_total) * 100, 1)
        feedback.append(f"Lyric Accuracy: sung {lyric_words_hit} of {lyric_words_total} words on-pitch ({lyric_pct}%).")
    elif total_notes > 0:
        hit_pct = round((hits / total_notes) * 100, 1)
        feedback.append(f"Hit {hits} of {total_notes} melody notes cleanly ({hit_pct}%).")

    return {
        "overall_score": overall_score,
        "letter_grade": letter_grade,
        "pitch_score": round(pitch_score, 1),
        "timing_score": round(timing_score, 1),
        "note_score": round(note_score, 1),
        "lyric_score": round(lyric_score, 1) if lyric_score is not None else None,
        "octave_detected_shift": detected_shift,
        "pitch_tendency": pitch_tendency,
        "mean_cents_error": round(mean_cents_error, 1),
        "notes_hit_summary": {
            "total_notes": total_notes,
            "hit_count": hits,
            "close_count": closes,
            "miss_count": misses,
        },
        "lyrics_hit_summary": {
            "total_words": lyric_words_total,
            "hit_count": lyric_words_hit,
            "close_count": lyric_words_close,
            "miss_count": lyric_words_miss,
        } if lyric_words_total > 0 else None,
        "lyrics_evaluation": lyric_results,
        "note_details": note_results,
        "feedback": feedback,
    }


def _resolve_song_data(source: dict | str | Path) -> dict:
    """Normalize input into an extracted song data dictionary."""
    if isinstance(source, dict):
        if "midi_pitches" in source and "times" in source:
            return source
        raise ValueError("Provided dict is missing required 'midi_pitches' or 'times' fields.")

    path_obj = Path(source)
    if not path_obj.exists():
        raise FileNotFoundError(f"File not found: {source}")

    if path_obj.suffix.lower() == ".json":
        return load_song_data(path_obj)
    else:
        # Extract on the fly
        return extract_song_data(path_obj)


def _align_pitch_contours(
    ref_midi: np.ndarray,
    cov_midi: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Align two pitch contour arrays using Dynamic Time Warping (DTW)."""
    # Downsample slightly if arrays are very large to speed up DTW
    step = 2 if len(ref_midi) > 3000 or len(cov_midi) > 3000 else 1
    r_sub = ref_midi[::step]
    c_sub = cov_midi[::step]

    # Feature representation: continuous MIDI value (dim 1)
    X = np.expand_dims(r_sub, axis=0)
    Y = np.expand_dims(c_sub, axis=0)

    # DTW alignment
    _, wp = librosa.sequence.dtw(X=X, Y=Y, metric="euclidean")

    # Reverse warping path so it goes forward in time
    wp = wp[::-1]
    warp_ref_idx = wp[:, 0] * step
    warp_cov_idx = wp[:, 1] * step

    # Ensure indices stay within bounds
    warp_ref_idx = np.clip(warp_ref_idx, 0, len(ref_midi) - 1)
    warp_cov_idx = np.clip(warp_cov_idx, 0, len(cov_midi) - 1)

    return warp_ref_idx, warp_cov_idx


def _empty_evaluation_result(reason: str) -> dict:
    return {
        "overall_score": 0.0,
        "letter_grade": "F",
        "pitch_score": 0.0,
        "timing_score": 0.0,
        "note_score": 0.0,
        "octave_detected_shift": 0,
        "pitch_tendency": "No Voice Detected",
        "mean_cents_error": 999.0,
        "notes_hit_summary": {
            "total_notes": 0,
            "hit_count": 0,
            "close_count": 0,
            "miss_count": 0,
        },
        "note_details": [],
        "feedback": [reason],
    }
