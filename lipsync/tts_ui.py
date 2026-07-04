"""Gradio tab "Văn bản → Giọng nói" — UI-only module so app.py stays lean.

Flow (preview-before-render): nhập text → chọn giọng (preset SDK hoặc clone từ
audio tải lên) → "Tạo & nghe thử" → nghe → Generate dùng đúng wav preview đó.
The preview wav rides through the exact same path as an uploaded audio file
(Pipeline.run -> prepare_audio) — nothing downstream knows TTS exists.
"""
from __future__ import annotations

from dataclasses import dataclass

import gradio as gr

from . import tts_bridge
from .audio_preprocess import probe_duration, wav_duration_seconds
from .config import MAX_AUDIO_SECONDS

CLONE_SENTINEL = "— Clone từ audio tải lên (5–10s) —"

# Single engine this round (D2 amendment: Chatterbox has no Vietnamese; the
# dropdown stays so round 2 just appends choices).
ENGINE_CHOICES = {"VieNeu v3 Turbo (48kHz, chạy CPU)": ("vieneu", "v3turbo")}

# Reference-clip sanity window (seconds): hard reject outside 2-30, advise 3-15.
_REF_HARD_MIN, _REF_HARD_MAX = 2.0, 30.0
_REF_SOFT_MIN, _REF_SOFT_MAX = 3.0, 15.0


@dataclass
class TTSTabHandles:
    """What app.py needs to wire Generate: the last preview wav + nothing else."""
    tts_wav_state: gr.State


def _counter_md(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    est = tts_bridge.estimate_seconds(text)
    if est > MAX_AUDIO_SECONDS:
        return (f"⚠️ **{len(text)} ký tự ≈ {est:.0f}s — vượt giới hạn "
                f"{MAX_AUDIO_SECONDS}s.** Hãy rút ngắn còn ~"
                f"{int(MAX_AUDIO_SECONDS * tts_bridge.CHARS_PER_SECOND)} ký tự.")
    return f"{len(text)} ký tự ≈ {est:.0f}s / tối đa {MAX_AUDIO_SECONDS}s"


def _resolve_voice(voice_label: str, ref_path: str | None,
                   ref_transcript: str | None) -> tts_bridge.Voice:
    if voice_label == CLONE_SENTINEL:
        if not ref_path:
            raise gr.Error("Tải lên một đoạn audio 5–10s để clone giọng.")
        dur = probe_duration(ref_path)
        if dur is not None and not (_REF_HARD_MIN <= dur <= _REF_HARD_MAX):
            raise gr.Error(
                f"Audio mẫu dài {dur:.1f}s — cần trong khoảng "
                f"{_REF_HARD_MIN:.0f}–{_REF_HARD_MAX:.0f}s (tốt nhất "
                f"{_REF_SOFT_MIN:.0f}–{_REF_SOFT_MAX:.0f}s).")
        transcript = (ref_transcript or "").strip() or None
        return tts_bridge.Voice(label="clone", wav=ref_path, transcript=transcript)
    for v in tts_bridge.list_voices("vi"):
        if v.label == voice_label:
            return v
    raise gr.Error(f"Không tìm thấy giọng: {voice_label}")


def _preview(text, engine_label, voice_label, ref_audio, ref_transcript,
             progress=gr.Progress()):
    engine, model = ENGINE_CHOICES.get(engine_label, ("vieneu", "v3turbo"))
    voice = _resolve_voice(voice_label, ref_audio, ref_transcript)
    est = tts_bridge.estimate_seconds(text or "")
    progress(0.1, desc=f"đang tổng hợp giọng nói (~{max(est, 5):.0f}s audio, "
                       "tốc độ ≈ realtime trên CPU)")
    try:
        wav = tts_bridge.synthesize(text, voice, engine=engine, model=model)
    except tts_bridge.TTSError as exc:  # clean Vietnamese toast
        raise gr.Error(exc.user_message) from exc
    try:  # cosmetic only — synthesize() already validated this wav
        dur = wav_duration_seconds(wav)
    except Exception:
        dur = est
    status = (f"✅ Đã tạo **{dur:.1f}s** audio (ước tính {est:.0f}s) — giọng "
              f"*{voice_label}*, {engine_label}.\n\n"
              "Nghe thử; ưng thì bấm **Generate**. Đổi văn bản/giọng/audio mẫu "
              "sẽ cần tạo lại.")
    return wav, status, str(wav)


_STALE_HINT = "⚠️ Thiết lập đã thay đổi — bấm **Tạo & nghe thử** lại trước khi Generate."


def _invalidate_preview():
    """Any input change makes the previewed wav stale: clear state + player."""
    return None, gr.update(value=None), _STALE_HINT


def _on_voice_change(voice_label: str):
    is_clone = voice_label == CLONE_SENTINEL
    return (gr.update(visible=is_clone), gr.update(visible=is_clone),
            *_invalidate_preview())


def build_tts_tab() -> TTSTabHandles:
    """Build the tab contents (call inside a `with gr.Tab(...)` block)."""
    tts_wav_state = gr.State(None)

    if not tts_bridge.tts_available():
        gr.Markdown(
            "**Môi trường TTS chưa được cài.** Chạy lệnh sau rồi khởi động lại app:\n\n"
            "```\npowershell -ExecutionPolicy Bypass -File scripts\\setup_tts_env.ps1\n```\n"
            "(Tải ~522MB model tiếng Việt trong lần chạy đầu — cần internet một lần.)"
        )
        return TTSTabHandles(tts_wav_state=tts_wav_state)

    voices = tts_bridge.list_voices("vi")
    voice_choices = [v.label for v in voices] + [CLONE_SENTINEL]

    text = gr.Textbox(
        label="Văn bản (tiếng Việt)", lines=8,
        placeholder="Nhập nội dung cần đọc… (viết số/chữ viết tắt thành chữ "
                    "để đọc chuẩn, ví dụ: 'hai mươi lăm' thay vì '25')")
    counter = gr.Markdown()
    language = gr.Dropdown(["Tiếng Việt"], value="Tiếng Việt", interactive=False,
                           label="Ngôn ngữ (đa ngôn ngữ ở vòng nâng cấp sau)")
    engine = gr.Dropdown(list(ENGINE_CHOICES), value=next(iter(ENGINE_CHOICES)),
                         label="Engine TTS")
    voice = gr.Dropdown(voice_choices, value=voice_choices[0], label="Giọng đọc")
    ref_audio = gr.Audio(label="Audio mẫu để clone (5–10s, giọng cần bắt chước)",
                         type="filepath", sources=["upload"], visible=False)
    ref_transcript = gr.Textbox(
        label="Transcript audio mẫu (không bắt buộc — điền giúp clone chính xác hơn)",
        lines=2, visible=False)
    preview_btn = gr.Button("🔊 Tạo & nghe thử", variant="secondary")
    preview_audio = gr.Audio(label="Nghe thử (bản audio sẽ dùng để render)",
                             type="filepath", interactive=False)
    status = gr.Markdown()

    # Stale-preview guard: ANY input change (text/voice/engine/ref) clears the
    # state AND the player, so Generate can never render an outdated take.
    voice.change(_on_voice_change, inputs=[voice],
                 outputs=[ref_audio, ref_transcript, tts_wav_state,
                          preview_audio, status])
    text.change(lambda t: (_counter_md(t), *_invalidate_preview()), inputs=[text],
                outputs=[counter, tts_wav_state, preview_audio, status])
    for comp in (engine, ref_audio, ref_transcript):
        comp.change(_invalidate_preview, inputs=None,
                    outputs=[tts_wav_state, preview_audio, status])
    # Shares the render queue: TTS is CPU-heavy (ONNX) and the render's
    # ffmpeg/paste-back stages are too — one at a time keeps both predictable.
    preview_btn.click(
        _preview,
        inputs=[text, engine, voice, ref_audio, ref_transcript],
        outputs=[preview_audio, status, tts_wav_state],
        concurrency_id="gpu-render", concurrency_limit=1,
    )
    return TTSTabHandles(tts_wav_state=tts_wav_state)
