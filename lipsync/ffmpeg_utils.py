"""Thin wrappers around the ffmpeg / ffprobe executables.

ffmpeg 8.0 is installed on PATH; imageio-ffmpeg's bundled binary is the fallback.
"""
from __future__ import annotations

import shutil
import subprocess
from functools import lru_cache


class FFmpegError(Exception):
    pass


@lru_cache(maxsize=1)
def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # noqa: BLE001
        raise FFmpegError("ffmpeg not found on PATH and imageio-ffmpeg unavailable") from exc


@lru_cache(maxsize=1)
def ffprobe_exe() -> str | None:
    return shutil.which("ffprobe")


def run(cmd: list[str]) -> None:
    """Run an ffmpeg/ffprobe command, raising with stderr tail on failure."""
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or "")[-1500:]
        raise FFmpegError(f"command failed (exit {proc.returncode}):\n{' '.join(cmd)}\n{tail}")
