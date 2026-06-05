"""Gradio app: still image + Vietnamese audio -> green-screen talking-head video.

Run:  .\.venv\Scripts\python.exe app.py   (or run_app.bat)
Local single-user UI, bound to 127.0.0.1.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make the local package importable when launched from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import gradio as gr  # noqa: E402

from lipsync.config import DEFAULT_GREEN_RGB, RenderConfig  # noqa: E402
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


def generate(image, audio, face_size, preprocess, still, enhancer,
             expression, pose, green_hex, progress=gr.Progress()):
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
    )
    res = _pipeline().run(
        image, audio, cfg,
        progress=lambda f, m: progress(f, desc=m),
    )
    t = res["timings"]
    status = (
        f"Done on **{res['device']}** — animate {t.get('animate_s')}s, "
        f"composite {t.get('composite_s')}s. {res['vram']}"
    )
    return str(res["output"]), status


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
                with gr.Accordion("Settings", open=False):
                    face_size = gr.Radio([256, 512], value=256, label="Face render size (512 = sharper, slower)")
                    preprocess = gr.Radio(["full", "crop", "resize"], value="full",
                                          label="Framing (full = whole portrait, recommended for green screen)")
                    still = gr.Checkbox(value=True, label="Still mode (less head motion, more stable)")
                    enhancer = gr.Checkbox(value=False, label="GFPGAN face enhance (slower; downloads weights on first use)")
                    expression = gr.Slider(0.5, 1.5, value=1.0, step=0.05, label="Expression scale")
                    pose = gr.Slider(0, 45, value=0, step=1, label="Pose style")
                    green = gr.ColorPicker(value="#00B140", label="Background color (key color)")
                run_btn = gr.Button("Generate", variant="primary")
            with gr.Column():
                out_video = gr.Video(label="Green-screen result")
                status = gr.Markdown()

        run_btn.click(
            generate,
            inputs=[image, audio, face_size, preprocess, still, enhancer, expression, pose, green],
            outputs=[out_video, status],
        )
    return demo


if __name__ == "__main__":
    build_ui().queue().launch(server_name="127.0.0.1", server_port=7860, inbrowser=False)
