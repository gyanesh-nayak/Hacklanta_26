"""CLI runner to extract song features and evaluate song cover accuracy."""

import argparse
import sys
import time
from pathlib import Path
import numpy as np
import sounddevice as sd
import soundfile as sf

from audio_extractor import extract_song_data, save_song_data, load_song_data
from cover_evaluator import evaluate_cover


def record_microphone(duration: float = 30.0, sr: int = 22050, output_path: str = "temp_recording.wav") -> str:
    """Record audio from the default microphone."""
    print(f"\n[Microphone] Recording {duration:.1f} seconds... (Press Ctrl+C to stop early)")
    print("Sing or play your cover now!\n")
    frames = []

    try:
        def callback(indata, frame_count, time_info, status):
            if status:
                print(status, file=sys.stderr)
            frames.append(indata.copy())

        with sd.InputStream(samplerate=sr, channels=1, dtype="float32", callback=callback):
            start = time.time()
            while time.time() - start < duration:
                time.sleep(0.1)
                elapsed = time.time() - start
                print(f"\rRecording: {elapsed:04.1f}s / {duration:04.1f}s", end="", flush=True)

    except KeyboardInterrupt:
        print("\nRecording stopped by user.")

    print("\nProcessing recorded audio...")
    if not frames:
        raise RuntimeError("No audio captured from microphone.")

    audio_data = np.concatenate(frames, axis=0)
    sf.write(output_path, audio_data, sr)
    return output_path


def print_evaluation_report(results: dict, ref_name: str, cover_name: str):
    """Print an eye-catching, formatted accuracy report in terminal."""
    score = results["overall_score"]
    grade = results["letter_grade"]

    grade_colors = {
        "S": "\033[92m", # Green
        "A": "\033[92m",
        "B": "\033[94m", # Blue
        "C": "\033[93m", # Yellow
        "D": "\033[91m", # Red
        "F": "\033[91m",
    }
    reset = "\033[0m"
    bold = "\033[1m"
    color = grade_colors.get(grade, "")

    print("\n" + "=" * 56)
    print(f"{bold}        SONG COVER ACCURACY EVALUATION REPORT{reset}")
    print("=" * 56)
    print(f" Reference Track: {bold}{ref_name}{reset}")
    print(f" User Cover:      {bold}{cover_name}{reset}")
    print("-" * 56)

    print(f" Overall Score:    {color}{bold}{score:5.1f} / 100  [ Grade: {grade} ]{reset}")
    print(f" Pitch Accuracy:   {results['pitch_score']:5.1f} / 100")
    if results.get("lyric_score") is not None:
        print(f" Lyric Accuracy:   {results['lyric_score']:5.1f} / 100")
    print(f" Timing & Rhythm:  {results['timing_score']:5.1f} / 100")
    print(f" Note Hit Rate:    {results['note_score']:5.1f} / 100")
    print("-" * 56)

    if results.get("lyrics_hit_summary"):
        l_hit = results["lyrics_hit_summary"]
        print(f" Lyric Words:      Total: {l_hit['total_words']} | Hit: {l_hit['hit_count']} | Close: {l_hit['close_count']} | Miss: {l_hit['miss_count']}")

    notes_hit = results["notes_hit_summary"]
    total = notes_hit["total_notes"]
    hit = notes_hit["hit_count"]
    close = notes_hit["close_count"]
    miss = notes_hit["miss_count"]
    print(f" Notes Breakdown:  Total: {total} | Hit: {hit} | Close: {close} | Miss: {miss}")
    print(f" Pitch Tendency:   {results['pitch_tendency']} (avg error: {results['mean_cents_error']:.1f} cents)")
    if results["octave_detected_shift"] != 0:
        print(f" Octave Transpose: {results['octave_detected_shift']:+d} semitones (automatically adjusted)")
    print("-" * 56)

    # Lyric-Aligned Pitch Comparison Table
    lyrics_eval = results.get("lyrics_evaluation", [])
    if lyrics_eval:
        print(f"{bold}Lyric-by-Lyric Pitch Comparison:{reset}")
        print(f" {'Time':<11} | {'Lyric / Word':<14} | {'Expected':<8} | {'Sung':<8} | {'Cents':<7} | {'Result'}")
        print(" " + "-" * 62)

        displayed_count = 0
        status_badges = {
            "HIT": "\033[92m[HIT]\033[0m",
            "CLOSE": "\033[93m[CLOSE]\033[0m",
            "OFF_KEY": "\033[91m[OFF]\033[0m",
            "MISSED": "\033[90m[MISS]\033[0m",
        }

        for line in lyrics_eval:
            for w in line.get("words", []):
                t_str = f"{w['start']:04.1f}s-{w['end']:04.1f}s"
                word_txt = w['word'][:14]
                exp_note = w['expected_note']
                sung_note = w['sung_note'] or "-"
                cents_str = f"{w['cents_diff']:+5.1f}" if w['cents_diff'] is not None else "-"
                badge = status_badges.get(w['status'], w['status'])

                print(f" {t_str:<11} | {word_txt:<14} | {exp_note:<8} | {sung_note:<8} | {cents_str:<7} | {badge}")
                displayed_count += 1
                if displayed_count >= 18:
                    break
            if displayed_count >= 18:
                remaining = sum(len(l.get("words", [])) for l in lyrics_eval) - displayed_count
                if remaining > 0:
                    print(f"   ... and {remaining} more lyric words.")
                break
        print("-" * 56)

    print(f"{bold}Feedback & Insights:{reset}")
    for item in results["feedback"]:
        print(f"  • {item}")
    print("=" * 56 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Evaluate song cover pitch and timing accuracy against reference.")
    parser.add_argument(
        "--reference", "-r",
        default="Song Library/alphabet_song.json",
        help="Path to reference song (.json feature file or .mp3/.wav)"
    )
    parser.add_argument(
        "--cover", "-c",
        help="Path to user cover audio file (.mp3, .wav)"
    )
    parser.add_argument(
        "--record",
        type=float,
        nargs="?",
        const=30.0,
        help="Record cover from microphone for specified seconds (default: 30s)"
    )
    parser.add_argument(
        "--extract-only",
        action="store_true",
        help="Only extract features from --reference and save to .json"
    )

    args = parser.parse_args()

    # 1. Resolve reference
    ref_path = Path(args.reference)
    if not ref_path.exists():
        script_dir_ref = Path(__file__).parent / args.reference
        if script_dir_ref.exists():
            ref_path = script_dir_ref
        elif ref_path.with_suffix(".mp3").exists():
            ref_path = ref_path.with_suffix(".mp3")
        elif script_dir_ref.with_suffix(".mp3").exists():
            ref_path = script_dir_ref.with_suffix(".mp3")
        else:
            print(f"Error: Reference path '{args.reference}' does not exist.", file=sys.stderr)
            sys.exit(1)

    # If reference is audio, extract or load JSON cache
    json_cache = ref_path.with_suffix(".json")
    if ref_path.suffix.lower() == ".json":
        print(f"Loading reference data from: {ref_path}")
        ref_data = load_song_data(ref_path)
    elif json_cache.exists():
        print(f"Loading cached reference data from: {json_cache}")
        ref_data = load_song_data(json_cache)
    else:
        print(f"Extracting reference features from {ref_path} (this may take ~10-15s)...")
        ref_data = extract_song_data(ref_path)
        save_song_data(ref_data, json_cache)
        print(f"Saved reference cache to: {json_cache}")

    if args.extract_only:
        print("\nExtraction complete! Summary:")
        print(f"  Song: {ref_data['song_name']}")
        print(f"  Duration: {ref_data['duration_sec']}s")
        print(f"  Total notes: {ref_data['note_summary']['total_notes']}")
        print(f"  Median note: {ref_data['note_summary']['median_note']}")
        return

    # 2. Resolve cover audio
    cover_file = None
    if args.record is not None:
        cover_file = record_microphone(duration=args.record)
    elif args.cover:
        cover_file = args.cover
    else:
        print("\nNo cover audio provided. Testing with self-comparison on reference song...")
        print("(Pass --cover <path> to evaluate a cover file, or --record 30 to record from mic)\n")
        results = evaluate_cover(ref_data, ref_data)
        print_evaluation_report(results, ref_data["song_name"], f"{ref_data['song_name']} (Self-Test)")
        return

    # 3. Evaluate Cover
    print(f"\nAnalyzing cover audio: {cover_file}...")
    cover_data = extract_song_data(cover_file)
    results = evaluate_cover(ref_data, cover_data)

    print_evaluation_report(results, ref_data["song_name"], Path(cover_file).name)


if __name__ == "__main__":
    main()
