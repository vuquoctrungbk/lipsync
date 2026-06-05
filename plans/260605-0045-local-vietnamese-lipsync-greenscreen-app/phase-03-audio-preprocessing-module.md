# Phase 03 — Audio Preprocessing Module

## Context Links

- Plan overview: [plan.md](plan.md)
- Architecture report: `plans/reports/researcher-260605-0035-app-architecture-inference-optimization-report.md` (sec 5: 16 kHz mono, mel-spectrogram language-agnostic)
- SadTalker verification: sec 4 (mel + wav2vec2, no VN-specific preprocessing)
- Depends on: Phase 01 (ffmpeg present, pydub/librosa installed), Phase 02 (`AppConfig.audio_sr`)

## Overview

- **Priority:** P1
- **Status:** pending
- **Description:** Normalize any user-supplied audio (MP3/WAV/M4A/etc.) into the exact format SadTalker expects: 16 kHz, mono, PCM s16 WAV. Validate non-empty, finite duration, and reasonable length. NO Vietnamese-specific processing (mel+wav2vec2 is language-agnostic — verified).

## Key Insights

- SadTalker's audio encoder consumes 16 kHz mono. Feeding 44.1 kHz stereo silently degrades sync; explicit resample is mandatory.
- ffmpeg 8.0 is already on PATH — use it for the transcode (robust to many input codecs) rather than pure-Python decode (KISS, fewer codec edge cases).
- Vietnamese needs zero special handling here. Do NOT add VN ASR / phoneme tooling (YAGNI; would also risk pulling extra deps).
- Duration guard: very long audio drives SadTalker VRAM/time up (verification report cites OOM on 4m48s). Warn/cap at a configurable max (default 120 s for v1) and surface to UI.

## Requirements

### Functional
- `preprocess_audio(input_path, out_dir, sr=16000) -> PreparedAudio` returning the 16k mono WAV path + duration (s) + sample count.
- Transcode via ffmpeg: `-ac 1 -ar 16000 -c:a pcm_s16le`.
- Validate: file exists, ffprobe duration > 0.1 s, duration <= max_seconds (configurable), audio stream present.
- Deterministic output filename in temp dir (hash or uuid) to avoid collisions across requests.

### Non-functional
- < 200 LoC.
- No new pip deps beyond Phase 01 (uses subprocess to ffmpeg + optional `librosa.load` for the duration assert).
- Clear, user-facing error messages (bubble to Gradio): "No audio stream", "Audio too long (Ns > max)".

## Architecture

```
src/lipsync/
└── audio_preprocess.py     # ffmpeg transcode + validation -> PreparedAudio dataclass
```

Data flow:
```
input audio (any) --ffprobe--> validate (stream/duration)
                  --ffmpeg---> 16kHz mono pcm_s16 WAV in temp/
                  --return---> PreparedAudio(path, duration_s, sr)
```

## Related Code Files

**Create:**
- `src/lipsync/audio_preprocess.py`

**Modify:** none.

**Delete:** none.

## Implementation Steps

1. **Locate ffmpeg/ffprobe.** Prefer system PATH (verified present). Fall back to `imageio_ffmpeg.get_ffmpeg_exe()` if PATH lookup fails. Define `_ffmpeg()` / `_ffprobe()` resolvers.

2. **`PreparedAudio` dataclass:** `path: Path`, `duration_s: float`, `sample_rate: int`.

3. **Validate input with ffprobe** (no decode of whole file):
   ```python
   # ffprobe -v error -show_entries format=duration:stream=codec_type
   #   -of default=nw=1 input
   ```
   Assert at least one `codec_type=audio`; parse `duration`. Raise `ValueError` with a friendly message if missing or <= 0.1 s.

4. **Enforce max duration** (param `max_seconds`, default 120). If exceeded, raise `ValueError("Audio too long: {d:.1f}s > {max}s")`. This is the v1 guard against long-audio OOM; revisit only if longer clips are needed (YAGNI now).

5. **Transcode to 16k mono WAV:**
   ```python
   out = out_dir / f"audio_{uuid4().hex}.wav"
   subprocess.run([ffmpeg, "-y", "-i", str(input_path),
                   "-vn", "-ac", "1", "-ar", str(sr),
                   "-c:a", "pcm_s16le", str(out)],
                  check=True, capture_output=True)
   ```
   On `CalledProcessError`, raise `RuntimeError` including ffmpeg stderr tail.

6. **Post-transcode sanity:** assert `out` exists and size > 1 KB; re-probe duration; return `PreparedAudio`.

7. **Cleanup contract:** caller (Phase 06 pipeline) owns temp lifecycle; this module only writes into the provided `out_dir`. Document that.

## Todo List

- [ ] Write `audio_preprocess.py` with `_ffmpeg/_ffprobe` resolvers
- [ ] Implement ffprobe validation (audio stream + duration)
- [ ] Implement max-duration guard (default 120 s)
- [ ] Implement ffmpeg transcode to 16k mono pcm_s16le
- [ ] Implement post-transcode sanity + `PreparedAudio` return
- [ ] Manual test: feed an MP3 and a 44.1 kHz stereo WAV; confirm both -> 16k mono

## Success Criteria

- Given any common audio (MP3/WAV/M4A), output is a valid 16 kHz mono PCM s16 WAV (verified via `ffprobe`: `sample_rate=16000`, `channels=1`, `codec_name=pcm_s16le`).
- Empty/no-audio file raises a clear `ValueError`, not a stack-trace crash.
- Over-length audio raises the friendly cap message.
- `duration_s` matches ffprobe within 0.1 s.
- File < 200 LoC.

## Risk Assessment

| Risk | Likelihood x Impact | Mitigation |
|------|---------------------|------------|
| Exotic input codec unsupported | Low x Medium | ffmpeg 8.0 covers near-all; surface stderr on failure |
| ffprobe not on PATH | Low x Low | Fallback to imageio_ffmpeg; ffmpeg also bundles ffprobe |
| Long audio -> downstream OOM | Medium x High | Hard duration cap (120 s) here, before SadTalker |
| Filename collision across requests | Low x Medium | uuid4 in output name |

## Security Considerations

- Pass file paths as a subprocess **arg list** (never `shell=True`) — prevents shell injection from crafted filenames.
- Write only inside the caller-provided temp dir; never overwrite source.
- Validate numeric `sr`/`max_seconds` are ints before interpolating into the ffmpeg arg list.

## Next Steps

- Unblocks Phase 04 (SadTalker animate consumes the 16k mono WAV) and Phase 06 (orchestration calls this first).
