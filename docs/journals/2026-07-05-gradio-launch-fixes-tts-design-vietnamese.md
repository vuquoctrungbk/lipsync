# Gradio Launch Fixes + TTS Design Brainstorm (Vietnamese-First)

**Date**: 2026-07-05 04:30
**Severity**: Medium (launch blocker fixed; TTS design approved pending user confirmation)
**Component**: app.py launch (gradio_client, fastapi/starlette); TTS text-input design (research phase)
**Status**: Activity 1 resolved (merged PR #4 commit 0da3228); Activity 2 pending user confirmation

## What Happened

Session 2026-07-04 evening → 05 morning: two concurrent activities. **Activity 1 (shipped)**: fixed broken `python app.py` launch. Two independent bugs: (a) gradio_client 1.3.0 crashed on boolean JSON-Schema nodes (`TypeError: argument of type 'bool' is not iterable`), fixed with shim `lipsync/gradio_schema_compat.py` + regression test; (b) unpinned fastapi/starlette drifted to 0.136.3/1.2.1, breaking gradio 4.44.1's TemplateResponse call (`unhashable type: 'dict'`), fixed by pinning web-serving cluster in requirements.txt. Verified HTTP 200, 57 tests passed. **Activity 2 (design only)**: TTS text-input upgrade brainstorm. Research shortlisted Chatterbox Multilingual V3 (MIT, 21+ langs incl Vietnamese, zero-shot clone ≈10s) + VieNeu-TTS (Apache-2.0, Vietnamese specialist, clone 3–5s, CPU ONNX capable). Architecture: isolated venv `tools/tts/.venv` + subprocess CLI (mirroring syncnet precedent) to avoid repeating dep fragility. Design: TTS as pre-stage producing wav → existing `prepare_audio()`. Full report in `plans/reports/brainstorm-260704-2348-tts-text-input-vietnamese-first-report.md`.

## The Brutal Truth

Activity 1: unpinned fastapi/starlette is the real lesson. We fragmented the web-serving cluster deliberately (each lib pinned independently) and ignored the interaction surface. One minor bump two weeks ago silently broke the whole app. No deprecation warning, no error during install — just gradio's TemplateResponse hitting an API that changed. Gradio 4.44.1 is pinned; the *ecosystem* around it drifted. This is embarrassing because the same team just shipped RVM/SyncNet with pinned transformers/torch to avoid exactly this.

Activity 2 brainstorm was productive but depends on four assumed user defaults (voice preset strategy, preview-before-render UX, zero-shot clone sampling, pilot asset plan). User was AFK during both question rounds; no plan created until confirmation received.

## Technical Details

**Bug (a)**: gradio_client 1.3.0 tries `if "required" in schema.keys()` on a boolean node. The node is `{"type": "boolean"}` (no keys() method). Shim intercepts schema at schema_to_parameter() and wraps booleans as `{"type": "boolean", "_is_boolean": true}`. Regression test: test_gradio_schema_boolean_node().

**Bug (b)**: fastapi 0.136.3 changed TemplateResponse signature. gradio 4.44.1 calls `TemplateResponse(..., context: dict)`. fastapi now tries `dict(context)`, which fails because context has duplicate keys (unhashable). Pinning: fastapi==0.115.6, starlette==1.0.4, uvicorn==0.32.1 (the last pre-0.136 cluster that ships with gradio 4.44.1).

**Activity 2 research**: verified 2026 TTS landscape. Rejected CosyVoice/Kokoro (no Vietnamese), XTTS/F5-TTS (restrictive licenses). Chatterbox chosen for multilingual + zero-shot; VieNeu chosen as specialist fallback. Same-venv install explicitly rejected: transformers 4.38.2 vs Chatterbox ≥4.46; torch 2.1.2 vs ≥2.4. Isolated venv eliminates conflict risk shown in Activity 1.

## Lessons Learned

1. **Ecosystem pinning is infrastructure**. One lib pinned ≠ cluster stable. Map the interaction surface and pin as a group or accept drift.
2. **Repeated fragility patterns signal design debt**. We pinned transformers/torch for RVM; should have pinned web stack from day one. Future: one "stable core" requirements.txt for all versioned deps.
3. **TTS architecture mirrors existing precedent for good reason**. Subprocess CLI + isolated venv not just "nice to have" — it's the blocker avoidance learned from this session.

## Next Steps

- Confirm user defaults on TTS design (voice presets, preview UX, clone sampling strategy, pilot asset plan). See brainstorm report Q1–Q4.
- Create plan in `plans/260705-tts-text-input-vietnamese/` once confirmed.
- Activity 1: merged PR #4 (merge commit 3908078). Close issue, mark app.py stable pending TTS integration.

## Addendum: implementation plan created

User confirmed all brainstorm decisions D1–D7 (2026-07-05): Vietnamese-first; dual engine Chatterbox Multilingual V3 (MIT) + VieNeu-TTS v3 Turbo (Apache-2.0, early-access accepted); presets + zero-shot clone; MIT/Apache only; isolated venv `tools/tts/.venv` + subprocess CLI; edge-tts permanently rejected. Plan created: `plans/260705-0028-tts-text-input-vietnamese-first/` — 4 phases: (1) TTS env + engines + CLI contract + RTX 3060 benchmark GO/NO-GO, (2) lipsync/tts_bridge.py + 600s guard + mocked tests, (3) Gradio tab "Văn bản → Giọng nói" + preview-before-render, (4) Vietnamese A/B eval (6 thanh điệu) → default engine + docs/NOTICE. Four Claude Tasks hydrated with 1→2→3→4 dependency chain; plan active. Implementation NOT started; user AFK at handoff.

## Addendum 2: TTS feature cooked (phases 1–3 done, phase 4 awaiting user listening)

Cook session executed plan/260705-0028-tts-text-input-vietnamese-first/ overnight (user AFK; /cook invoked). **Critical mid-cook discovery**: Chatterbox Multilingual has NO Vietnamese in official weights (GitHub README + HF card); source (resemble.ai marketing page) was wrong. D2 amended to VieNeu-only this round; Chatterbox deferred to round 2 (en/fr/ko/ja); documented in plan.md + brainstorm report, pending user ack.

**Phase 1 done**: isolated venv tools/tts/.venv, vieneu==3.0.11 (Apache-2.0, ONNX torch-free CPU), tts_cli.py frozen JSON contract, Xeon benchmark RTF 1.06–1.17, 48kHz, load 9.7s, weights 522MB repo-local. 10 bundled preset voices discovered in SDK. GO.

**Phase 2 done**: lipsync/tts_bridge.py (subprocess wrapper, 600s fail-closed cap, Vietnamese errors) + 15 unit tests, zero new main-venv deps.

**Phase 3 done**: lipsync/tts_ui.py tab "Văn bản → Giọng nói" (preview-before-render, preset+clone, stale-preview invalidation). Real-GPU E2E: TTS wav → generate(input_mode="tts") → valid green MP4 (h264+AAC, 4.48s). Code review 11 findings (0 Critical, 1 High: user-writable voice sidecar encoding crash), all fixed same session; suite 78 passed/9 skipped; HTTP 200 clean. requirements.lock (61 pins) added; `!voices/**` gitignore negation removed.

**Phase 4 ready**: eval (outputs/tts-eval/, 3 takes, 6-tone script), docs+NOTICE updated (VieNeu Apache-2.0, Perth watermark, clone-consent). **LISTENING VERDICT PENDING USER** — report template at plans/reports/phase-04-tts-vi-ab-listening-report.md.

Nothing committed yet. Traps learned: (1) PowerShell CWD drifted mid-session — use absolute paths for venv; (2) vieneu presets load only after full model load — pinning wheel makes hardcoding safe; (3) marketing pages ≠ license/language docs — verify claims vs HF card + repo README.

---

**Status**: DONE
**Summary**: Gradio launch fixed (app.py stable); TTS design confirmed and implementation plan created with 4-phase dependency chain.
