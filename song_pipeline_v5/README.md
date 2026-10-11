# Song Pipeline v5: Pitch and Cover Accuracy Analysis

This offline command-line pipeline analyzes reference songs and evaluates a singer's cover. It extracts vocal pitch contours and stable notes, can transcribe lyrics with Whisper and align words to notes, then compares a cover for pitch, timing, lyric, and note accuracy. It is separate from the live ngrok-enabled multiplayer app in `pitch_battle/`.

## Modules

- `audio_extractor.py`: Loads audio, separates harmonic content, tracks fundamental pitch, segments notes, and saves/reloads feature JSON.
- `lyrics_transcriber.py`: Uses Whisper to transcribe lyrics with word timing and align lyric words to expected pitches.
- `cover_evaluator.py`: Aligns reference and cover performances, including timing variation and octave shifts, and reports accuracy and feedback.
- `compare.py`: Command-line entry point for extraction, saved-cover evaluation, and microphone recording.

Reference tracks and cached features live in `Song Library/`.

## Setup

From this directory, install the pipeline dependencies in your active Python environment:

```bash
pip install -r requirements.txt
```

## Run

Run commands from `song_pipeline_v5/`:

```bash
# Self-comparison using the bundled Alphabet Song reference
python compare.py

# Compare a saved cover against a bundled reference
python compare.py --reference "Song Library/alphabet_song.json" --cover "path/to/my_cover.wav"

# Record a 30-second cover from the default microphone
python compare.py --reference "Song Library/alphabet_song.json" --record 30

# Extract and save features for an audio reference
python compare.py --reference "path/to/song.mp3" --extract-only
```

The default reference is `Song Library/alphabet_song.json`. Audio references are analyzed and cached as JSON beside the source file. Lyric transcription support uses Whisper from `lyrics_transcriber.py` when lyric inclusion is enabled by the extraction workflow.
