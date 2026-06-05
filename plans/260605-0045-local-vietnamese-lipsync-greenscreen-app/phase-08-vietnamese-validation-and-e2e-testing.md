# Phase 08 — Vietnamese Validation + E2E Testing

## Context Links

- Plan overview: [plan.md](plan.md)
- SadTalker verification: `plans/reports/researcher-260605-0040-sadtalker-adversarial-verification-report.md` (sec 4 Vietnamese untested -> pilot required; sec 8 failure modes)
- Tech-stack report: sec 8 (pilot 10–30 s, frame-by-frame; if off >=2 frames, flag)
- Depends on: Phase 06 (`run_pipeline`), Phase 07 (UI). Gates "v1 done".

## Overview

- **Priority:** P1 (this is the DONE gate)
- **Status:** pending
- **Description:** Prove the pipeline works end-to-end AND that Vietnamese lip-sync is acceptable on a real Vietnamese clip, since SadTalker's Vietnamese accuracy is UNVERIFIED. Includes a Vietnamese pilot with frame-by-frame mouth inspection, automated E2E smoke tests, perf measurement, and OOM/edge-case coverage. v1 cannot be declared done until the pilot passes.

## Key Insights

- SadTalker audio encoder (mel + wav2vec2) is language-agnostic, but NO Vietnamese benchmark exists (verified). Empirical pilot is mandatory, not optional.
- Pilot protocol (from reports): 10–30 s clip of the REAL target speaker; inspect mouth frame-by-frame; if mouth lags/leads audio by >= ~2 frames consistently, the engine is failing on Vietnamese.
- MuseTalk is the documented fallback IF the pilot fails — but MuseTalk is OUT of v1 scope. So pilot failure = escalate to user (decision: accept quality, or fund the deferred MuseTalk engine). Do NOT silently swap engines.
- Automated tests must NOT mock the models for the E2E path — verification report's whole point is empirical behavior. Unit tests may stub I/O; the green/sync acceptance is a real run + human/heuristic frame check.

## Requirements

### Functional
- Vietnamese pilot run: real 10–30 s VN clip + a front-facing portrait -> green MP4; documented frame-by-frame mouth/audio alignment verdict.
- Automated E2E smoke: a short (~3–5 s) clip through `run_pipeline`, asserting: output exists, has video+audio, duration ≈ input, background is solid green (sample center-edge pixels ≈ green within tolerance), N frames > 0.
- Unit tests for pure modules: `hardware.get_device_and_precision` (cuda->fp16 / cpu->fp32 via monkeypatch), `audio_preprocess` format guarantees (16k/mono/pcm_s16), `config` validation, `hex_to_rgb`.
- Perf measurement: seconds-per-output-second + per-stage timings + VRAM peaks (read from `PipelineResult`), recorded for 256 and 512.
- Edge cases: no-face image (clear error), over-length audio (cap error), silent audio (no crash), corrupt file (clear error), enhancer on/off.

### Non-functional
- Tests live under `tests/`; runnable via `pytest`. Mark the slow real-run E2E with `@pytest.mark.slow` so the fast suite (unit) runs quickly.
- No fake data to "pass" the green/sync checks — those use a real pipeline run.

## Architecture

```
tests/
├── test_hardware_precision.py        # device/precision selection (monkeypatched)
├── test_audio_preprocess.py          # 16k mono pcm_s16 guarantees, duration cap
├── test_config_and_helpers.py        # AppConfig validation, hex_to_rgb
├── test_pipeline_e2e_smoke.py        # @slow: real short run -> green + sync asserts
└── assets/                           # tiny sample portrait + ~3s wav (commercial-safe)
docs/ (validation notes recorded here in Phase 09)
```

Green check heuristic: sample a grid of border pixels of several frames; assert mean color within tolerance of `green_rgb` -> proves background actually green (not source image).

## Related Code Files

**Create:**
- `tests/test_hardware_precision.py`
- `tests/test_audio_preprocess.py`
- `tests/test_config_and_helpers.py`
- `tests/test_pipeline_e2e_smoke.py`
- `tests/assets/` (sample portrait + short clip; commercial-safe sources only)

**Modify:** none (tests read public APIs).

**Delete:** none.

## Implementation Steps

1. **Unit: hardware precision.** Monkeypatch `torch.cuda.is_available`/`get_device_properties` to simulate 12 GB -> assert `fp16`; 24 GB -> `fp32`; no-cuda -> `(cpu, fp32)`. Real-box assert (non-mocked) optionally under `@slow`: returns `(cuda:0, fp16)`.

2. **Unit: audio preprocess.** Feed a generated MP3 and a 44.1 kHz stereo WAV; assert output ffprobe = `sample_rate 16000, channels 1, codec pcm_s16le`. Assert over-length raises; no-audio file raises `ValueError`.

3. **Unit: config + helpers.** Assert `AppConfig(face_region=300)` raises; `hex_to_rgb('#00B140') == (0,177,64)`; bad hex falls back/raises cleanly.

4. **E2E smoke (`@slow`).** Run `run_pipeline(sample_portrait, sample_3s_wav, AppConfig(face_region=256))`. Assert: file exists; ffprobe shows 1 video + 1 audio stream; duration within 0.3 s of input; frame count > 0; sampled border pixels ≈ `green_rgb` (tolerance ~±25/channel). This is the automated green-background proof.

5. **Vietnamese pilot (manual + recorded).** Use a real 10–30 s Vietnamese clip of the target speaker + a front portrait. Run via the UI (Phase 07). Extract frames (`ffmpeg`), inspect mouth shape vs audio at ~5 sampled phoneme transitions. Record verdict: PASS (sync within ~1 frame, mouth closes on stops/plosives) or FAIL (consistent >=2-frame drift or wrong viseme). Save a short notes file + 3–4 annotated frames into `docs/` (Phase 09 links it).

6. **Perf table.** From `PipelineResult.timings`/`vram_peaks`, record per-stage seconds + VRAM peak at 256 and 512, plus seconds-per-output-second. Confirm peak never sums SadTalker+BiRefNet (sequential-load proof).

7. **Edge cases.** Run: no-face image (expect friendly error), 130 s audio (expect cap error), 1 s silence (expect output, mouth mostly closed, no crash), corrupt jpg (expect error). Document outcomes.

8. **Pilot-fail escalation.** If the VN pilot FAILS: do NOT swap to MuseTalk (out of scope). Document the failure with evidence and ESCALATE to the user with options: (a) accept current quality, (b) tune (try 512 / enhancer / different portrait / cleaner audio), (c) approve the deferred MuseTalk engine as a follow-up. Record the chosen path.

## Todo List

- [ ] Write unit tests (hardware, audio, config/helpers)
- [ ] Write `@slow` E2E smoke with green-pixel + stream + duration asserts
- [ ] Assemble commercial-safe `tests/assets/` (portrait + ~3s clip)
- [ ] Run automated suite; all green
- [ ] Run Vietnamese pilot (10–30 s real speaker); frame-by-frame mouth inspection
- [ ] Record verdict + annotated frames into docs/
- [ ] Capture perf table (256 + 512: timings, VRAM peaks, sec/out-sec)
- [ ] Run edge cases (no-face, over-length, silence, corrupt)
- [ ] If pilot fails: document + escalate (do NOT auto-swap engine)

## Success Criteria

- Fast unit suite passes (`pytest -m "not slow"`).
- `@slow` E2E produces a valid green MP4 with audio; green-pixel heuristic passes.
- Vietnamese pilot verdict = PASS (mouth synced within ~1 frame at sampled transitions), OR a documented escalation with evidence if FAIL.
- Perf numbers recorded; VRAM logs prove sequential model load (no dual-engine peak).
- All edge cases produce friendly errors or correct output, never an unhandled crash.

## Risk Assessment

| Risk | Likelihood x Impact | Mitigation |
|------|---------------------|------------|
| Vietnamese sync poor | Medium x High | Mandatory pilot gate; escalate w/ options (incl. deferred MuseTalk); do not silently ship |
| Green heuristic false pass/fail | Low x Medium | Sample multiple frames + border grid; human spot-check the pilot |
| E2E too slow for CI | Medium x Low | `@slow` marker; unit suite is the fast gate |
| Edge case crashes | Medium x Medium | Explicit error tests; fixed in source before sign-off |
| No commercial-safe test assets | Low x Low | Generate a synthetic portrait + TTS/own-voice clip; document source/license |

## Security Considerations

- Test assets must be commercial-safe (own/synthetic/CC0); record provenance — no scraped faces, no NC audio.
- Tests write only to temp/outputs; no network calls in the unit suite (models already local).

## Next Steps

- On PASS (or accepted escalation): unblocks Phase 09 (packaging + docs include the validation notes + perf table).
- On FAIL + user picks MuseTalk: opens a NEW follow-up plan (engine-2), outside this v1 plan.
