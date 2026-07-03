"""Thin wrappers around the ffmpeg / ffprobe executables.

ffmpeg 8.0 is installed on PATH; imageio-ffmpeg's bundled binary is the fallback.
"""
from __future__ import annotations

import re
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


def has_ffprobe() -> bool:
    """imageio-ffmpeg ships NO ffprobe — callers must fail loud, not guess."""
    return ffprobe_exe() is not None


@lru_cache(maxsize=None)
def has_encoder(name: str) -> bool:
    """True if the RESOLVED ffmpeg build ships the named encoder.

    The imageio-ffmpeg fallback binary may lack libvpx-vp9 — callers gate
    WebM-alpha export on this instead of failing mid-encode.
    """
    try:
        proc = subprocess.run(
            [ffmpeg_exe(), "-hide_banner", "-encoders"],
            capture_output=True, text=True,
        )
    except OSError:
        return False
    if proc.returncode != 0:
        return False
    # encoder rows look like " V....D libvpx-vp9    libvpx VP9 (codec vp9)"
    return bool(re.search(rf"^\s*\S+\s+{re.escape(name)}\s", proc.stdout, re.MULTILINE))


def run(cmd: list[str]) -> None:
    """Run an ffmpeg/ffprobe command, raising with stderr tail on failure."""
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or "")[-1500:]
        raise FFmpegError(f"command failed (exit {proc.returncode}):\n{' '.join(cmd)}\n{tail}")
