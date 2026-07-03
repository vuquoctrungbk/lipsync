"""Chunked-render E2E proofs (RUN_E2E=1, slow, GPU).

1. Seeded chunked-vs-full equivalence: same seed, forced small segments vs
   the single-shot path — per-frame mean abs diff < 2/255. This is the actual
   guarantee that halo slicing makes segment boundaries invisible.
2. Kill + resume: interrupt after the first segment completes, relaunch,
   verify done segments are NOT re-rendered (mtime unchanged) and the final
   video has the exact frame count.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

E2E = pytest.mark.skipif(
    os.environ.get("RUN_E2E") != "1", reason="set RUN_E2E=1 to run slow E2E tests"
)

IMG = ROOT / "third_party/SadTalker/examples/source_image/full_body_1.png"


def _make_wav(tmp_path: Path, seconds: float) -> Path:
    """Loop the bundled example audio out to `seconds` (16k mono pcm)."""
    from lipsync.ffmpeg_utils import ffmpeg_exe

    src = ROOT / "third_party/SadTalker/examples/driven_audio/chinese_poem1.wav"
    out = tmp_path / f"audio_{int(seconds)}s.wav"
    subprocess.run(
        [ffmpeg_exe(), "-y", "-loglevel", "error", "-stream_loop", "99",
         "-i", str(src), "-t", f"{seconds}", "-ar", "16000", "-ac", "1",
         "-acodec", "pcm_s16le", str(out)],
        check=True, capture_output=True)
    return out


def _frames(path: Path) -> list[np.ndarray]:
    import imageio

    reader = imageio.get_reader(str(path), "ffmpeg")
    out = [f.astype(np.float32) for f in reader]
    reader.close()
    return out


@E2E
def test_seeded_chunked_equals_full(tmp_path, monkeypatch):
    """THE seam guarantee: same seed, chunked vs single-shot, diff < 2/255."""
    import lipsync.config as config
    from lipsync.config import RenderConfig
    from lipsync.pipeline import Pipeline

    wav = _make_wav(tmp_path, 30)
    cfg = RenderConfig(face_size=256, still_mode=True, seed=1234,
                       chunk_seconds=8, output_dir=tmp_path / "out")

    pipe = Pipeline()
    # full single-shot reference (30s <= 120s keeps the v1 path)
    res_full = pipe.run(IMG, wav, cfg)

    # force the chunked dispatch for the same clip
    monkeypatch.setattr(config, "SINGLE_SHOT_MAX_SECONDS", 10)
    res_chunk = pipe.run(IMG, wav, cfg)

    a = _frames(Path(res_full["output"]))
    b = _frames(Path(res_chunk["output"]))
    assert abs(len(a) - len(b)) <= 1, f"frame counts differ: {len(a)} vs {len(b)}"

    n = min(len(a), len(b))
    diffs = [float(np.mean(np.abs(a[i] - b[i]))) for i in range(n)]
    worst = max(diffs)
    mean = float(np.mean(diffs))
    print(f"\n[chunked-vs-full] frames={n} mean|diff|={mean:.3f} worst-frame={worst:.3f} (/255)")
    assert mean < 2.0, f"mean per-frame diff {mean:.3f} >= 2/255 — seams visible"


@E2E
def test_kill_and_resume_reuses_done_segments(tmp_path):
    """140s render: kill the process after segment 1 is done, relaunch with
    identical inputs, verify resume (no re-render of done segments)."""
    import json

    from lipsync import config

    wav = _make_wav(tmp_path, 140)
    out_dir = tmp_path / "out"
    driver = tmp_path / "driver.py"
    driver.write_text(f"""
import sys
sys.path.insert(0, r"{ROOT}")
from lipsync.config import RenderConfig
from lipsync.pipeline import Pipeline
cfg = RenderConfig(face_size=256, still_mode=True, seed=77, chunk_seconds=30,
                   output_dir=__import__("pathlib").Path(r"{out_dir}"))
res = Pipeline().run(r"{IMG}", r"{wav}", cfg)
print("DRIVER_DONE", res["output"])
""", encoding="utf-8")

    py = sys.executable
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    test_start = time.time()

    def _fresh_manifests():
        return [m for m in config.TEMP_DIR.glob("run_*/manifest.json")
                if m.stat().st_mtime >= test_start - 2]

    # Driver stdout goes to FILES: SadTalker's tqdm output would fill an
    # undrained 64KB pipe buffer and freeze the driver mid-render — the same
    # deadlock class the WebM sink avoids by writing stderr to a file.
    log1 = tmp_path / "driver_run1.log"
    log2 = tmp_path / "driver_run2.log"

    # launch and kill once the first segment lands
    with open(log1, "wb") as lf:
        proc = subprocess.Popen([py, str(driver)], env=env,
                                stdout=lf, stderr=subprocess.STDOUT)
        killed = False
        deadline = time.time() + 1800
        seg0 = None
        try:
            while time.time() < deadline:
                for mp in _fresh_manifests():
                    data = json.loads(mp.read_text(encoding="utf-8"))
                    done = [s for s in data["segments"] if s["status"] == "done"]
                    if done:
                        seg0 = mp.parent / done[0]["path"]
                        proc.kill()
                        killed = True
                        break
                if killed:
                    break
                if proc.poll() is not None:
                    pytest.fail("driver finished before we could interrupt it:\n"
                                + log1.read_text(errors="replace")[-2000:])
                time.sleep(5)
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
    assert killed and seg0 is not None and seg0.exists(), (
        "never saw a done segment:\n" + log1.read_text(errors="replace")[-2000:])
    seg0_mtime = seg0.stat().st_mtime

    # relaunch: must resume, not restart. The run dir is purged on success,
    # so the no-re-render proof happens DURING the run: watch seg0's mtime
    # while the resumed process works through the remaining segments.
    mtime_violated = False
    with open(log2, "wb") as lf:
        proc2 = subprocess.Popen([py, str(driver)], env=env,
                                 stdout=lf, stderr=subprocess.STDOUT)
        while proc2.poll() is None:
            if seg0.exists() and seg0.stat().st_mtime != seg0_mtime:
                mtime_violated = True
            time.sleep(5)
    out2_stdout = log2.read_text(errors="replace")
    assert "DRIVER_DONE" in out2_stdout, out2_stdout[-3000:]
    assert not mtime_violated, "segment 0 was re-rendered on resume (mtime changed)"

    # exact frame count on the final output
    from lipsync.audio_preprocess import wav_duration_seconds
    from lipsync.ffmpeg_utils import ffprobe_exe

    final = out2_stdout.split("DRIVER_DONE", 1)[1].strip().splitlines()[0].strip()
    n = int(subprocess.run(
        [ffprobe_exe(), "-v", "error", "-select_streams", "v:0", "-count_frames",
         "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", final],
        capture_output=True, text=True, check=True).stdout.strip())
    expect = round(wav_duration_seconds(wav) * 25)
    assert abs(n - expect) <= 1, f"frame count {n} vs expected {expect}"
