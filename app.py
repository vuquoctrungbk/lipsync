"""Gradio app: still image + Vietnamese audio -> green-screen talking-head video.

Run:  .\.venv\Scripts\python.exe app.py   (or run_app.bat)
Local single-user UI, bound to 127.0.0.1.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Make the local package importable when launched from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import gradio as gr  # noqa: E402

from lipsync.config import DEFAULT_GREEN_RGB, OUTPUTS_DIR, RenderConfig  # noqa: E402
from lipsync.hardware import detect_device  # noqa: E402
from lipsync.pipeline import Pipeline  # noqa: E402

_PIPE: Pipeline | None = None


def _pipeline() -> Pipeline:
    global _PIPE
    if _PIPE is None:
        _PIPE = Pipeline()
    return _PIPE


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    if isinstance(value, str) and value.startswith("#") and len(value) == 7:
        return (int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16))
    return DEFAULT_GREEN_RGB


# UI label -> RenderConfig.output_format
_FORMAT_CHOICES = {
    "Green MP4": "green_mp4",
    "WebM alpha (CapCut)": "webm_alpha",
    "Both": "both",
}

# UI label -> RenderConfig.matting_engine
_ENGINE_CHOICES = {
    "RVM (fast, personal use — GPL-3)": "rvm",
    "BiRefNet (commercial-safe, slower)": "birefnet",
}

# Presets only PRE-FILL existing controls on dropdown change; the user can
# still override anything before clicking Generate.
_PRESETS = {
    "Draft (256, no enhancer)": {"face_size": 256, "enhancer": False},
    "High (512 + enhancer)": {"face_size": 512, "enhancer": True},
    "Custom": None,
}


def apply_preset(name):
    p = _PRESETS.get(name)
    if not p:  # Custom: touch nothing
        return gr.update(), gr.update()
    return gr.update(value=p["face_size"]), gr.update(value=p["enhancer"])


def generate(image, audio, face_size, preprocess, still, enhancer,
             expression, pose, green_hex, out_format, engine_label,
             commercial, progress=gr.Progress()):
    if not image:
        raise gr.Error("Please upload a character image.")
    if not audio:
        raise gr.Error("Please upload a voice audio file.")

    cfg = RenderConfig(
        face_size=int(face_size),
        preprocess=preprocess,
        still_mode=bool(still),
        use_enhancer=bool(enhancer),
        expression_scale=float(expression),
        pose_style=int(pose),
        green_rgb=_hex_to_rgb(green_hex),
        output_format=_FORMAT_CHOICES.get(out_format, "green_mp4"),
        matting_engine=_ENGINE_CHOICES.get(engine_label, "rvm"),
        commercial_safe=bool(commercial),
    )
    try:
        res = _pipeline().run(
            image, audio, cfg,
            progress=lambda f, m: progress(f, desc=m),
        )
    except Exception as exc:  # render failure -> clean toast (trace still logged)
        raise gr.Error(f"{type(exc).__name__}: {exc}") from exc
    t = res["timings"]
    outputs = res.get("outputs", {"green_mp4": res["output"]})
    green = outputs.get("green_mp4")
    webm = outputs.get("webm_alpha")

    lines = [
        f"Done on **{res['device']}** — animate {t.get('animate_s')}s, "
        f"composite {t.get('composite_s')}s. {res['vram']}",
        "",
        "Outputs:",
    ]
    lines += [f"- `{p}`" for p in outputs.values()]
    for w in res.get("warnings", []):
        lines.append(f"\n⚠️ {w}")
    # gr.Video cannot preview transparency — the WebM goes to the File slot.
    return (str(green) if green else None,
            str(webm) if webm else None,
            "\n".join(lines))


def resume_render(progress=gr.Progress()):
    """Continue the newest interrupted long render from its stored inputs.

    Completed segments are reused; compositing restarts (not checkpointable).
    """
    info = _pipeline().latest_resumable()
    if info is None:
        raise gr.Error("No interrupted render found to resume.")

    raw = dict(info["cfg"])
    raw["green_rgb"] = tuple(raw.get("green_rgb", DEFAULT_GREEN_RGB))
    raw["output_dir"] = Path(raw.get("output_dir", str(OUTPUTS_DIR)))
    fields = RenderConfig.__dataclass_fields__
    cfg = RenderConfig(**{k: v for k, v in raw.items() if k in fields})

    try:
        res = _pipeline().run(
            info["image"], info["audio"], cfg,
            progress=lambda f, m: progress(f, desc=m),
        )
    except Exception as exc:  # render failure -> clean toast (trace still logged)
        raise gr.Error(f"{type(exc).__name__}: {exc}") from exc
    t = res["timings"]
    outputs = res.get("outputs", {})
    lines = [
        f"Resumed render from {info['created']} "
        f"({info['segments_done']}/{info['segments_total']} segments were already done; "
        f"compositing restarted). Done on **{res['device']}** — "
        f"animate {t.get('animate_s')}s, composite {t.get('composite_s')}s.",
        "",
        "Outputs:",
    ]
    lines += [f"- `{p}`" for p in outputs.values()]
    for w in res.get("warnings", []):
        lines.append(f"\n⚠️ {w}")
    green = outputs.get("green_mp4")
    webm = outputs.get("webm_alpha")
    return (str(green) if green else None,
            str(webm) if webm else None,
            "\n".join(lines))


def analyze_drift(video_path, progress=gr.Progress()):
    """Opt-in per-60s LSE-D drift scan of a rendered video (never automatic —
    SyncNet costs ~1 min of GPU per minute of video)."""
    if not video_path:
        raise gr.Error("Render a video first (or load one into the result slot).")
    progress(0.05, desc="scoring per-60s windows with SyncNet (takes minutes)")
    script = Path(__file__).resolve().parent / "scripts" / "sync_metrics.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--video", str(video_path), "--window", "60"],
        capture_output=True, text=True, timeout=7200,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "no output")[-800:]
        return f"⚠️ drift analysis failed:\n```\n{tail}\n```"
    return f"```\n{proc.stdout.strip()}\n```"


def build_ui() -> gr.Blocks:
    info = detect_device()
    badge = (f"GPU: {info.name} ({info.total_vram_gb} GB)"
             if info.device == "cuda" else "Running on CPU (slow)")

    with gr.Blocks(title="Vietnamese Lip-Sync (Green Screen)") as demo:
        gr.Markdown(
            "# Lip-Sync AI — Green Screen\n"
            "Ảnh nhân vật + file giọng nói (tiếng Việt) → video nhép miệng trên nền xanh.\n\n"
            f"`{badge}`"
        )
        with gr.Row():
            with gr.Column():
                image = gr.Image(label="Character image", type="filepath", sources=["upload"])
                audio = gr.Audio(label="Voice audio (Vietnamese)", type="filepath", sources=["upload"])
                preset = gr.Dropdown(list(_PRESETS), value="Draft (256, no enhancer)",
                                     label="Quality preset (pre-fills the settings below)")
                with gr.Accordion("Settings", open=False):
                    face_size = gr.Radio([256, 512], value=256, label="Face render size (512 = sharper, slower)")
                    preprocess = gr.Radio(["full", "crop", "resize"], value="full",
                                          label="Framing (full = whole portrait, recommended for green screen)")
                    still = gr.Checkbox(value=True, label="Still mode (less head motion, more stable)")
                    enhancer = gr.Checkbox(value=False, label="GFPGAN face enhance (slower; downloads weights on first use)")
                    expression = gr.Slider(0.5, 1.5, value=1.0, step=0.05, label="Expression scale")
                    pose = gr.Slider(0, 45, value=0, step=1, label="Pose style")
                    green = gr.ColorPicker(value="#00B140", label="Background color (key color)")
                    engine = gr.Dropdown(
                        list(_ENGINE_CHOICES), value=next(iter(_ENGINE_CHOICES)),
                        label="Matting engine")
                    commercial = gr.Checkbox(
                        value=False,
                        label="Commercial-safe mode (forces BiRefNet — never loads GPL RVM)")
                out_format = gr.Radio(
                    list(_FORMAT_CHOICES), value="Green MP4",
                    label="Output format (WebM alpha = true transparency for CapCut; slower VP9 encode)",
                )
                run_btn = gr.Button("Generate", variant="primary")
                resume_btn = gr.Button(
                    "Resume interrupted render",
                    variant="secondary",
                    )
            with gr.Column():
                out_video = gr.Video(label="Green-screen result (MP4 preview)")
                out_file = gr.File(label="WebM alpha download (players show no transparency — use in your editor)")
                status = gr.Markdown()
                drift_btn = gr.Button("Analyze sync drift (per-60s LSE-D — slow, optional)")
                drift_md = gr.Markdown()

        preset.change(apply_preset, inputs=[preset], outputs=[face_size, enhancer])

        # Shared concurrency_id: gradio 4 limits per-listener, so without it
        # Generate + Resume could render simultaneously (chdir race, double
        # GPU load). The Pipeline render lock backstops non-UI callers too.
        # Drift analysis shares the id — SyncNet uses the same GPU.
        run_btn.click(
            generate,
            inputs=[image, audio, face_size, preprocess, still, enhancer,
                    expression, pose, green, out_format, engine, commercial],
            outputs=[out_video, out_file, status],
            concurrency_id="gpu-render", concurrency_limit=1,
        )
        resume_btn.click(resume_render, inputs=[], outputs=[out_video, out_file, status],
                         concurrency_id="gpu-render", concurrency_limit=1)
        drift_btn.click(analyze_drift, inputs=[out_video], outputs=[drift_md],
                        concurrency_id="gpu-render", concurrency_limit=1)
    return demo


if __name__ == "__main__":
    build_ui().queue().launch(server_name="127.0.0.1", server_port=7860, inbrowser=False)
