#!/usr/bin/env python3
"""Hybrid render job for the Google Colab CLI (`colab exec -f ...`).

Self-contained on a fresh Colab runtime: pinned repos + two isolated
Python 3.11 venvs (Ditto torch 2.3.1 vs LatentSync torch 2.5.1 — the stacks
cannot share one env) + HF checkpoints + Ditto motion -> LatentSync-256
mouth-refine. Mirrors the job-runner of tools/colab/lipsync_render.ipynb
(code-reviewed); I/O goes through `colab upload` / `colab download` instead
of Drive.

Flow from the local machine (Windows: prefix with `wsl`):
    colab new --gpu T4
    colab upload MC_Nam.png /content/job/image.png
    colab upload voice.wav  /content/job/audio.wav
    colab exec -f tools/colab/colab_render_job.py
    colab download /content/job/out/hybrid_256.mp4 outputs/colab/hybrid_256.mp4
    colab download /content/job/out/timings.json   outputs/colab/timings.json
    colab stop

Optional /content/job/params.json: {"inference_steps": 20, "guidance_scale": 1.5}
Then locally: scripts/matte_video.py --video outputs/colab/hybrid_256.mp4
"""
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

DITTO_COMMIT = "c3e47ee"       # antgroup/ditto-talkinghead — spike-proven 2026-07
LATENTSYNC_COMMIT = "a229c39"  # bytedance/LatentSync — one codebase for 1.5 (256) & 1.6 (512)

SRC_DITTO = Path("/content/ditto-src")
SRC_LS = Path("/content/latentsync-src")
VENVS = Path("/content/venvs")
DITTO_PY = VENVS / "ditto/bin/python"
LS_PY = VENVS / "latentsync/bin/python"

JOB_DIR = Path("/content/job")     # `colab upload` targets land here
OUT_DIR = JOB_DIR / "out"          # `colab download` picks results up here
WORK = Path("/content/work")       # render scratch on the VM's local disk

TIMINGS: dict = {}

# The Jupyter kernel leaks MPLBACKEND=module://matplotlib_inline... into child
# envs; the isolated venvs lack matplotlib_inline -> force headless Agg.
# (Hit for real on the 2026-07-11 T4 gate run.)
SUBPROC_ENV = {**os.environ, "MPLBACKEND": "Agg"}


def sh(*cmd) -> None:
    # Stream raw logs (tqdm/stacktraces included) — never filter long renders.
    cmd = [str(c) for c in cmd]
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def run_stage(name: str, cmd: list, cwd: Path) -> float:
    # Long verbose subprocesses must NOT stream over the exec websocket (a huge
    # tqdm backlog stalls the client into TimeoutError) and must NOT go to the
    # kernel's raw fd (invisible to the CLI). Log to a VM file; print the tail
    # only on failure. Returns elapsed seconds.
    log_path = WORK / f"{name}.log"
    t0 = time.monotonic()
    with open(log_path, "w") as log:
        rc = subprocess.run([str(c) for c in cmd], cwd=cwd, stdout=log,
                            stderr=subprocess.STDOUT, env=SUBPROC_ENV).returncode
    elapsed = round(time.monotonic() - t0, 1)
    if rc != 0:
        tail = subprocess.run(["tail", "-30", str(log_path)],
                              capture_output=True, text=True).stdout
        print(f"== {name} FAILED (rc={rc}) — {log_path} tail ==")
        print(tail)
        raise RuntimeError(f"{name} failed with rc={rc}; see {log_path}")
    print(f"{name} OK in {elapsed}s")
    return elapsed


class VramPoller:
    # Poll nvidia-smi every 5 s to catch the VRAM peak (the T4 gate metric).
    def __init__(self):
        self.peak_mb = 0
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._loop, daemon=True)

    def _loop(self):
        while not self._stop.is_set():
            try:
                out = subprocess.run(
                    ["nvidia-smi", "--query-gpu=memory.used",
                     "--format=csv,noheader,nounits"],
                    capture_output=True, text=True,
                ).stdout.strip()
                if out:
                    self.peak_mb = max(self.peak_mb, int(out.splitlines()[0]))
            except (ValueError, OSError):
                pass  # one bad poll must not kill the thread
            self._stop.wait(5)

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._t.join(timeout=10)


def gpu_name() -> str:
    try:
        return subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True,
        ).stdout.strip()
    except FileNotFoundError:
        return ""


def ensure_tools() -> None:
    t0 = time.monotonic()
    sh("pip", "install", "-q", "uv", "huggingface_hub")
    TIMINGS["tools_s"] = round(time.monotonic() - t0, 1)


def ensure_repos() -> None:
    t0 = time.monotonic()
    if not (SRC_DITTO / ".git").exists():
        sh("git", "clone", "https://github.com/antgroup/ditto-talkinghead", SRC_DITTO)
    if not (SRC_LS / ".git").exists():
        sh("git", "clone", "https://github.com/bytedance/LatentSync", SRC_LS)
    # checkout runs EVERY time — a clone-ok-checkout-failed rerun must still pin
    sh("git", "-C", SRC_DITTO, "checkout", DITTO_COMMIT)
    sh("git", "-C", SRC_LS, "checkout", LATENTSYNC_COMMIT)
    TIMINGS["clone_s"] = round(time.monotonic() - t0, 1)


def ensure_venvs() -> None:
    # Sentinel .install-complete: a venv only counts once EVERY package is in —
    # a mid-install failure means the rerun reinstalls instead of using a husk.
    t0 = time.monotonic()
    ditto_ok = VENVS / "ditto/.install-complete"
    if not ditto_ok.exists():
        sh("uv", "venv", VENVS / "ditto", "--python", "3.11")
        sh("uv", "pip", "install", "--python", DITTO_PY,
           "--extra-index-url", "https://download.pytorch.org/whl/cu121",
           "torch==2.3.1", "torchaudio==2.3.1",
           "numpy==1.26.4", "librosa==0.11.0", "mediapipe==0.10.35",
           # 1.22 = last CUDA-12 line; 1.27 links libcudart.so.13 and dies on
           # Colab's CUDA 12.8 image (local Windows 1.27 works — don't mirror it)
           "onnxruntime-gpu==1.22.0",
           "opencv-python-headless", "scikit-image", "einops", "filetype",
           "colored", "imageio-ffmpeg", "tqdm", "cython")
        ditto_ok.touch()
    TIMINGS["venv_ditto_s"] = round(time.monotonic() - t0, 1)

    t0 = time.monotonic()
    ls_ok = VENVS / "latentsync/.install-complete"
    if not ls_ok.exists():
        sh("uv", "venv", VENVS / "latentsync", "--python", "3.11")
        sh("uv", "pip", "install", "--python", LS_PY, "-r", SRC_LS / "requirements.txt")
        ls_ok.touch()
    TIMINGS["venv_latentsync_s"] = round(time.monotonic() - t0, 1)


def ensure_checkpoints() -> None:
    t0 = time.monotonic()
    from huggingface_hub import hf_hub_download, snapshot_download

    # Ditto PyTorch path — T4 is Turing; the prebuilt TRT in the repo is Ampere_Plus.
    snapshot_download(
        "digital-avatar/ditto-talkinghead",
        allow_patterns=["ditto_pytorch/**", "ditto_cfg/**"],
        local_dir=SRC_DITTO / "checkpoints",
    )
    # LatentSync-1.5 = the 256 model (1.6 = 512); same code, different ckpt + config.
    for fname in ("latentsync_unet.pt", "whisper/tiny.pt"):
        hf_hub_download("ByteDance/LatentSync-1.5", fname, local_dir=SRC_LS / "checkpoints")
    TIMINGS["checkpoints_s"] = round(time.monotonic() - t0, 1)


def find_image() -> Path | None:
    for pat in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
        hits = sorted(JOB_DIR.glob(pat))
        if hits:
            return hits[0]
    return None


def load_params() -> dict:
    pj = JOB_DIR / "params.json"
    params = json.loads(pj.read_text()) if pj.exists() else {}
    if not isinstance(params, dict):
        raise ValueError(f"params.json must be a JSON object, got {type(params).__name__}")
    return params


def render() -> None:
    image, audio = find_image(), JOB_DIR / "audio.wav"
    if image is None or not audio.exists():
        raise FileNotFoundError(
            f"need an image (*.png/jpg/webp) and audio.wav in {JOB_DIR} — "
            "use `colab upload <local> /content/job/<name>` first")
    params = load_params()
    steps = params.get("inference_steps", 20)
    guidance = params.get("guidance_scale", 1.5)
    max_width = params.get("max_width", 960)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    ditto_mp4, hybrid_mp4 = WORK / "ditto_raw.mp4", WORK / "hybrid_256.mp4"
    TIMINGS["gpu"] = gpu_name()
    TIMINGS["params"] = {"inference_steps": steps, "guidance_scale": guidance}

    with VramPoller() as vram:
        # 1) Ditto: image + audio -> motion video (keeps the source resolution)
        TIMINGS["ditto_s"] = run_stage("ditto", [
            DITTO_PY, "inference.py",
            "--data_root", "./checkpoints/ditto_pytorch",
            "--cfg_pkl", "./checkpoints/ditto_cfg/v0.4_hubert_cfg_pytorch.pkl",
            "--audio_path", audio,
            "--source_path", image,
            "--output_path", ditto_mp4], SRC_DITTO)

        # Downscale before LatentSync: its final video-write buffers every
        # frame at source resolution — 1402px frames OOM-killed the 12.7GB-RAM
        # free VM (gate run 2026-07-11). Faces are refined at 256 either way.
        # params.json {"max_width": 0} disables.
        ls_input = ditto_mp4
        if max_width:
            ls_input = WORK / "ditto_scaled.mp4"
            TIMINGS["downscale_s"] = run_stage("downscale", [
                "ffmpeg", "-y", "-loglevel", "error", "-i", ditto_mp4,
                "-vf", f"scale='min({max_width},iw)':-2",
                "-c:v", "libx264", "-crf", "16", "-an", ls_input], WORK)

        # 2) LatentSync-256: refine the mouth to match the audio
        TIMINGS["latentsync_s"] = run_stage("latentsync", [
            LS_PY, "-m", "scripts.inference",
            "--unet_config_path", "configs/unet/stage2.yaml",
            "--inference_ckpt_path", "checkpoints/latentsync_unet.pt",
            "--inference_steps", steps,
            "--guidance_scale", guidance,
            "--enable_deepcache",
            "--video_path", ls_input,
            "--audio_path", audio,
            "--video_out_path", hybrid_mp4], SRC_LS)
    TIMINGS["vram_peak_mb"] = vram.peak_mb

    shutil.copy2(ditto_mp4, OUT_DIR / "ditto_raw.mp4")
    shutil.copy2(hybrid_mp4, OUT_DIR / "hybrid_256.mp4")
    (OUT_DIR / "timings.json").write_text(json.dumps(TIMINGS, indent=2))


def main() -> int:
    gpu = gpu_name()
    if not gpu:
        print("ERROR: no GPU on this runtime — create it with `colab new --gpu T4`")
        return 1
    print("GPU:", gpu)

    ensure_tools()
    ensure_repos()
    ensure_venvs()
    ensure_checkpoints()
    render()

    print("\n=== DONE ===")
    print(json.dumps(TIMINGS, indent=2))
    print(f"\nresults in {OUT_DIR} — fetch with:")
    print("  colab download /content/job/out/hybrid_256.mp4 outputs/colab/hybrid_256.mp4")
    print("  colab download /content/job/out/timings.json   outputs/colab/timings.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
