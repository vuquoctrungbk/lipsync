"""Composite an animated portrait into green-screen MP4 and/or alpha WebM.

One streaming pass: frames are decoded once, matted ONCE (matting is the
expensive stage), and fanned out to per-format sinks — so `both` mode costs
one matte loop, not two. Memory stays flat regardless of clip length.

The `composite()` driver is the single owner of ``matte.reset_clip()`` (clip
start + ``finally``), so recurrent engines (RVM) start every clip clean even
after a crash.

Per-sink failure isolation: a dying sink is closed, its error becomes a
warning, and the remaining sinks keep streaming — a WebM encoder crash must
never destroy the green MP4 of a multi-hour render. Sinks write temp names
and rename on success, so a killed render never leaves a plausible-looking
partial output.
"""
from __future__ import annotations

import itertools
import os
import subprocess
from pathlib import Path
from typing import Callable, Iterator, Optional

import imageio
import numpy as np

from .config import RenderConfig
from .ffmpeg_utils import FFmpegError, ffmpeg_exe, run
from .matting_base import MattingEngine

ProgressFn = Optional[Callable[[float, str], None]]

OUTPUT_FORMATS = ("green_mp4", "webm_alpha", "both")


class CompositeError(Exception):
    pass


class SinkError(Exception):
    """One sink failed; the driver catches this and keeps the others alive."""

    def __init__(self, fmt: str, msg: str):
        super().__init__(f"[{fmt}] {msg}")
        self.fmt = fmt


def iter_video_frames(video: str | Path) -> tuple[Iterator[np.ndarray], float, int | None]:
    """Open a video for streaming. Returns (frame iterator, fps, total|None).

    The iterator owns the reader and closes it when exhausted or .close()d —
    phase-4 chunking substitutes its own segment-frame iterator here.
    """
    reader = imageio.get_reader(str(video), "ffmpeg")
    fps = float(reader.get_meta_data().get("fps") or 25)
    try:
        total = reader.count_frames()
    except Exception:  # noqa: BLE001 - frame count is best-effort for progress
        total = None

    def gen():
        try:
            for frame in reader:
                yield frame
        finally:
            reader.close()

    return gen(), fps, total


def _prep(frame: np.ndarray) -> np.ndarray:
    """Strip an alpha channel and crop to even dims (yuv420p/yuva420p need it)."""
    if frame.ndim == 3 and frame.shape[2] == 4:
        frame = frame[..., :3]
    h, w = frame.shape[:2]
    return frame[:h - (h % 2), :w - (w % 2)]


class _GreenSink:
    """Foreground over solid green -> H.264 MP4 + AAC audio remux."""

    fmt = "green_mp4"

    def __init__(self, cfg: RenderConfig, audio_path: str | Path, out_path: Path,
                 work_dir: Path, fps: float):
        self.audio = Path(audio_path)
        self.final = out_path
        self.tmp = out_path.with_suffix(".tmp.mp4")
        self.silent = work_dir / "green_silent.mp4"
        self.green = np.array(cfg.green_rgb, dtype=np.float32)
        self.warnings: list[str] = []
        self.writer = imageio.get_writer(
            str(self.silent), fps=fps, codec="libx264", macro_block_size=None,
            output_params=["-crf", str(cfg.crf), "-pix_fmt", "yuv420p"],
        )

    def write(self, rgb: np.ndarray, alpha: np.ndarray) -> None:
        a = alpha[..., None]
        comp = rgb.astype(np.float32) * a + self.green * (1.0 - a)
        try:
            self.writer.append_data(np.clip(comp, 0, 255).astype(np.uint8))
        except (OSError, IOError) as exc:  # imageio pipe/disk failure
            raise SinkError(self.fmt, f"H.264 encode failed: {exc}")

    def finalize(self) -> Path:
        self.writer.close()
        try:
            run([
                ffmpeg_exe(), "-y", "-i", str(self.silent), "-i", str(self.audio),
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
                str(self.tmp),
            ])
        except FFmpegError as exc:
            # Audio missing/incompatible -> emit video-only rather than fail
            # the run, but the degradation must reach the user.
            cause = (str(exc).splitlines() or ["unknown"])[-1][:200]
            self.warnings.append(
                f"audio mux failed — green output is video-only (silent). Cause: {cause}")
            run([ffmpeg_exe(), "-y", "-i", str(self.silent), "-c:v", "copy", str(self.tmp)])
        if not self.tmp.exists() or self.tmp.stat().st_size == 0:
            raise SinkError(self.fmt, "green-screen mux produced no output")
        os.replace(self.tmp, self.final)
        return self.final

    def abort(self) -> None:
        try:
            self.writer.close()
        except Exception:  # noqa: BLE001 - abort must never raise
            pass
        try:
            self.tmp.unlink(missing_ok=True)
        except OSError:
            pass


class _WebmSink:
    """RGBA (straight alpha) piped to ffmpeg -> VP9 yuva420p WebM + Opus audio.

    stderr goes to a FILE, never a PIPE: an undrained stderr pipe fills the
    small Windows buffer on a long VP9 encode, ffmpeg stops reading stdin, and
    stdin.write blocks forever (silent hang).
    """

    fmt = "webm_alpha"

    def __init__(self, audio_path: str | Path, out_path: Path, work_dir: Path,
                 fps: float, size: tuple[int, int]):
        w, h = size
        self.final = out_path
        self.tmp = out_path.with_suffix(".tmp.webm")
        self.stderr_path = work_dir / "webm_ffmpeg_stderr.log"
        self._stderr_f = open(self.stderr_path, "wb")
        self.warnings: list[str] = []
        try:
            self.proc = self._spawn(audio_path, fps, w, h)
        except BaseException:
            self._stderr_f.close()
            raise

    def _spawn(self, audio_path: str | Path, fps: float, w: int, h: int) -> subprocess.Popen:
        return subprocess.Popen(
            [
                ffmpeg_exe(), "-y", "-hide_banner", "-nostats", "-loglevel", "error",
                "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{w}x{h}",
                "-r", f"{fps}", "-i", "pipe:0",
                "-i", str(audio_path),
                "-map", "0:v", "-map", "1:a",
                "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
                "-crf", "33", "-b:v", "0", "-row-mt", "1",
                "-c:a", "libopus", "-shortest", str(self.tmp),
            ],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=self._stderr_f,
        )

    def write(self, rgb: np.ndarray, alpha: np.ndarray) -> None:
        # straight (unassociated) alpha — VP9/WebM expects unpremultiplied
        a8 = np.clip(alpha * 255.0 + 0.5, 0, 255).astype(np.uint8)
        rgba = np.dstack([rgb, a8])
        try:
            self.proc.stdin.write(rgba.tobytes())
        except (BrokenPipeError, OSError) as exc:
            # ffmpeg died mid-stream; collect its stderr for the warning
            raise SinkError(self.fmt, self._shutdown(f"ffmpeg stopped reading frames ({exc})"))

    def _stderr_tail(self) -> str:
        try:
            return self.stderr_path.read_bytes()[-1500:].decode("utf-8", "replace").strip()
        except OSError:
            return "(stderr unavailable)"

    def _shutdown(self, msg: str) -> str:
        try:
            self.proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            self.proc.kill()
        self._stderr_f.close()
        return f"{msg}: {self._stderr_tail()}"

    def finalize(self) -> Path:
        try:
            self.proc.stdin.close()
            rc = self.proc.wait()
        finally:
            self._stderr_f.close()
        if rc != 0:
            raise SinkError(self.fmt, f"VP9 encode failed (exit {rc}): {self._stderr_tail()}")
        if not self.tmp.exists() or self.tmp.stat().st_size == 0:
            raise SinkError(self.fmt, "VP9 encode produced no output")
        os.replace(self.tmp, self.final)
        return self.final

    def abort(self) -> None:
        try:
            if self.proc.poll() is None:
                try:
                    self.proc.stdin.close()
                except Exception:  # noqa: BLE001
                    pass
                self.proc.kill()
                self.proc.wait(timeout=5)
        except Exception:  # noqa: BLE001 - abort must never raise
            pass
        try:
            self._stderr_f.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _formats(output_format: str) -> list[str]:
    if output_format == "both":
        return ["green_mp4", "webm_alpha"]
    if output_format in ("green_mp4", "webm_alpha"):
        return [output_format]
    raise CompositeError(
        f"unknown output_format {output_format!r}; choose from {sorted(OUTPUT_FORMATS)}")


def composite(frames: Iterator[np.ndarray], fps: float, matte: MattingEngine,
              cfg: RenderConfig, audio_path: str | Path, out_dir: str | Path,
              run_id: str, work_dir: str | Path, total: int | None = None,
              progress: ProgressFn = None,
              alpha_source: Iterator[np.ndarray] | None = None,
              ) -> tuple[dict[str, Path], list[str]]:
    """Stream frames through the matte once, fan out to the requested sinks.

    alpha_source: optional frame iterator the alpha is computed FROM instead
    of the rendered frames — e.g. a cleaner earlier encode whose background
    motion is identical (mouth-refine passes recompress hard, and compression
    noise makes the matte flicker). Must be frame-aligned with `frames` and
    the same size; it has to cover the whole clip.

    Returns ({format: final path}, warnings). Raises CompositeError only when
    NO output could be produced; single-sink failures become warnings.
    """
    warnings: list[str] = []
    fmts = _formats(cfg.output_format)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)

    sinks: list = []
    matte.reset_clip()
    try:
        try:
            first = _prep(next(iter(frames)))
        except StopIteration:
            raise CompositeError("animated video contained no frames")
        h, w = first.shape[:2]

        if "green_mp4" in fmts:
            sinks.append(_GreenSink(cfg, audio_path,
                                    out / f"lipsync_green_{run_id}.mp4", work, fps))
        if "webm_alpha" in fmts:
            sinks.append(_WebmSink(audio_path,
                                   out / f"lipsync_alpha_{run_id}.webm", work, fps, (w, h)))
        slow = "; VP9 ~0.5x realtime" if any(s.fmt == "webm_alpha" for s in sinks) else ""

        for i, frame in enumerate(itertools.chain([first], (_prep(f) for f in frames))):
            if frame.shape[:2] != (h, w):
                frame = frame[:h, :w]
            if alpha_source is None:
                alpha_input = frame
            else:
                try:
                    # _prep only evens the dims — no free cropping: a center
                    # mismatch silently shifting the alpha would be worse than
                    # failing loud.
                    alpha_input = _prep(next(alpha_source))
                except StopIteration:
                    raise CompositeError(
                        f"alpha_source ran out at frame {i + 1} — it must cover the whole clip")
                if alpha_input.shape[:2] != (h, w):
                    raise CompositeError(
                        f"alpha_source frame is {alpha_input.shape[1]}x{alpha_input.shape[0]} "
                        f"but the clip is {w}x{h} — scale one to match first "
                        "(e.g. ffmpeg -vf scale=W:-2)")
            alpha = matte.alpha_for(alpha_input)  # ONCE per frame, shared by every sink
            for sink in list(sinks):
                try:
                    sink.write(frame, alpha)
                except SinkError as exc:
                    warnings.append(str(exc))
                    sinks.remove(sink)
                    sink.abort()  # idempotent; reaps procs/partials the sink left
                    if not sinks:
                        raise CompositeError(
                            f"all output sinks failed; last error: {exc}")
            if progress and total:
                progress((i + 1) / total,
                         f"matting + encoding frame {i + 1}/{total}{slow}")

        outputs: dict[str, Path] = {}
        for sink in list(sinks):
            try:
                outputs[sink.fmt] = sink.finalize()
                warnings.extend(sink.warnings)
            except Exception as exc:  # noqa: BLE001 - ANY finalize failure
                # (BrokenPipe on stdin.close, mux FFmpegError, os.replace
                # PermissionError, ...) stays sink-scoped: aborting the other
                # sinks here would destroy their finished outputs.
                msg = exc if isinstance(exc, SinkError) else f"[{sink.fmt}] finalize failed: {exc}"
                warnings.append(str(msg))
                sink.abort()
        if not outputs:
            raise CompositeError(
                "no output produced (all sinks failed):\n" + "\n".join(warnings[-3:]))
        return outputs, warnings
    except BaseException:
        for sink in sinks:
            sink.abort()
        raise
    finally:
        matte.reset_clip()
        for source in (frames, alpha_source):
            close = getattr(source, "close", None)
            if close:
                close()
