"""Unit tests for lipsync/tts_bridge.py — subprocess fully mocked.

No TTS venv, no model, no GPU needed: the CLI boundary is faked with a
behavior-bearing stub that writes a real (tiny) wav and speaks the frozen
JSON contract, so these tests pin the contract from the main-venv side.
"""
from __future__ import annotations

import json
import subprocess
import types
import wave
from pathlib import Path

import numpy as np
import pytest

from lipsync import tts_bridge
from lipsync.tts_bridge import TTSError, Voice


def tiny_wav(path: Path, seconds: float = 0.5, sr: int = 16000) -> Path:
    """Write a real PCM wav of silence (duration honest to the header)."""
    n = int(seconds * sr)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.zeros(n, dtype=np.int16).tobytes())
    return path


def fake_run_factory(recorder: dict, *, returncode: int = 0,
                     stdout: str | None = None, stderr: str = "",
                     write_wav_seconds: float | None = 0.5,
                     raise_timeout: bool = False):
    """Build a subprocess.run stand-in that honors the CLI contract."""

    def fake_run(cmd, **kwargs):
        recorder["cmd"] = [str(c) for c in cmd]
        recorder["kwargs"] = kwargs
        if raise_timeout:
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0))
        out_wav = Path(recorder["cmd"][recorder["cmd"].index("--out") + 1])
        if write_wav_seconds is not None:
            tiny_wav(out_wav, seconds=write_wav_seconds)
        payload = stdout
        if payload is None:
            payload = ("some stderr-ish noise on stdout\n" + json.dumps({
                "ok": True, "out": str(out_wav), "duration_s": write_wav_seconds,
                "engine": "vieneu", "model": "v3turbo", "voice": "Ngọc Lan",
                "synth_s": 0.1, "load_s": 0.1, "chunks": 1}))
        return types.SimpleNamespace(returncode=returncode, stdout=payload, stderr=stderr)

    return fake_run


@pytest.fixture()
def bridged(monkeypatch, tmp_path):
    """TTS venv 'present' + temp dir isolated to tmp_path."""
    monkeypatch.setattr(tts_bridge, "TTS_PYTHON", tmp_path / "venv" / "python.exe")
    tiny_wav(tmp_path / "venv" / "python.exe.wav")  # ensure parent exists
    (tmp_path / "venv" / "python.exe").write_bytes(b"")  # existence is all that matters
    monkeypatch.setattr(tts_bridge, "TTS_TEMP_DIR", tmp_path / "temp-tts")
    return tmp_path


def test_synthesize_success_builds_contract_cmd(bridged, monkeypatch):
    rec: dict = {}
    monkeypatch.setattr(tts_bridge.subprocess, "run", fake_run_factory(rec))
    out = tts_bridge.synthesize("Xin chào Việt Nam.", Voice(label="x", preset="Ngọc Lan"))
    assert out.exists() and out.suffix == ".wav"
    cmd = rec["cmd"]
    assert "--engine" in cmd and cmd[cmd.index("--engine") + 1] == "vieneu"
    assert "--model" in cmd and cmd[cmd.index("--model") + 1] == "v3turbo"
    assert "--voice" in cmd and cmd[cmd.index("--voice") + 1] == "Ngọc Lan"
    assert "--voice-ref" not in cmd
    # text rides via file, never argv
    text_file = Path(cmd[cmd.index("--text-file") + 1])
    assert text_file.read_text(encoding="utf-8") == "Xin chào Việt Nam."
    assert rec["kwargs"]["encoding"] == "utf-8"


def test_synthesize_device_defaults_cpu_and_threads_through(bridged, monkeypatch):
    rec: dict = {}
    monkeypatch.setattr(tts_bridge.subprocess, "run", fake_run_factory(rec))
    tts_bridge.synthesize("Xin chào.", Voice(label="x", preset="Ngọc Lan"))
    cmd = rec["cmd"]
    assert cmd[cmd.index("--device") + 1] == "cpu"  # default = zero behavior change


def test_synthesize_device_cuda_passed(bridged, monkeypatch):
    rec: dict = {}
    monkeypatch.setattr(tts_bridge.subprocess, "run", fake_run_factory(rec))
    tts_bridge.synthesize("Xin chào.", Voice(label="x", preset="Ngọc Lan"),
                          device="cuda")
    cmd = rec["cmd"]
    assert cmd[cmd.index("--device") + 1] == "cuda"


def test_gpu_available_probe(monkeypatch, tmp_path):
    monkeypatch.setattr(tts_bridge, "TTS_PYTHON", tmp_path / "python.exe")
    (tmp_path / "python.exe").write_bytes(b"")
    monkeypatch.setattr(tts_bridge, "_gpu_cache", None)

    def fake_probe(cmd, **kwargs):
        return types.SimpleNamespace(returncode=0, stdout="1", stderr="")

    monkeypatch.setattr(tts_bridge.subprocess, "run", fake_probe)
    assert tts_bridge.gpu_available() is True
    # cached: a second call must not re-probe even if the prober would say no
    monkeypatch.setattr(tts_bridge.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="0", stderr=""))
    assert tts_bridge.gpu_available() is True


def test_gpu_unavailable_when_probe_says_no(monkeypatch, tmp_path):
    monkeypatch.setattr(tts_bridge, "TTS_PYTHON", tmp_path / "python.exe")
    (tmp_path / "python.exe").write_bytes(b"")
    monkeypatch.setattr(tts_bridge, "_gpu_cache", None)
    monkeypatch.setattr(tts_bridge.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="0", stderr=""))
    assert tts_bridge.gpu_available() is False


def test_gpu_unavailable_when_venv_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(tts_bridge, "TTS_PYTHON", tmp_path / "nope" / "python.exe")
    monkeypatch.setattr(tts_bridge, "_gpu_cache", None)
    probed = {"n": 0}
    def spy(*a, **k):
        probed["n"] += 1
        raise AssertionError("prober must not run when the venv is absent")
    monkeypatch.setattr(tts_bridge.subprocess, "run", spy)
    assert tts_bridge.gpu_available() is False
    assert probed["n"] == 0  # short-circuited on tts_available(), no subprocess


def test_synthesize_clone_voice_passes_ref_and_transcript(bridged, monkeypatch, tmp_path):
    rec: dict = {}
    monkeypatch.setattr(tts_bridge.subprocess, "run", fake_run_factory(rec))
    ref = tiny_wav(tmp_path / "mc.wav")
    tts_bridge.synthesize("Xin chào.", Voice(label="MC", wav=ref, transcript="xin chào"))
    cmd = rec["cmd"]
    assert cmd[cmd.index("--voice-ref") + 1] == str(ref)
    assert cmd[cmd.index("--voice-ref-text") + 1] == "xin chào"
    assert "--voice" not in cmd


def test_missing_venv_gives_setup_hint(monkeypatch, tmp_path):
    monkeypatch.setattr(tts_bridge, "TTS_PYTHON", tmp_path / "nope" / "python.exe")
    with pytest.raises(TTSError, match="setup_tts_env"):
        tts_bridge.synthesize("Xin chào.", Voice(label="x", preset="Ngọc Lan"))


def test_empty_text_rejected(bridged):
    with pytest.raises(TTSError, match="Văn bản trống"):
        tts_bridge.synthesize("   ", Voice(label="x", preset="Ngọc Lan"))


def test_overlong_text_rejected_before_synth(bridged, monkeypatch):
    called = {}
    monkeypatch.setattr(tts_bridge.subprocess, "run", fake_run_factory(called))
    text = "a" * int(900 * tts_bridge.CHARS_PER_SECOND)  # ~900s estimate > 780s (600*1.3) gate
    with pytest.raises(TTSError, match="quá dài"):
        tts_bridge.synthesize(text, Voice(label="x", preset="Ngọc Lan"))
    assert "cmd" not in called  # subprocess never launched


def test_result_wav_over_cap_is_rejected_and_deleted(bridged, monkeypatch):
    rec: dict = {}
    monkeypatch.setattr(tts_bridge.subprocess, "run", fake_run_factory(rec))
    monkeypatch.setattr(tts_bridge, "wav_duration_seconds", lambda p: 601.0)
    with pytest.raises(TTSError, match="vượt giới hạn 600"):
        tts_bridge.synthesize("Xin chào.", Voice(label="x", preset="Ngọc Lan"))
    out_wav = Path(rec["cmd"][rec["cmd"].index("--out") + 1])
    assert not out_wav.exists()  # fail-closed: oversized artifact removed


def test_cli_error_json_surfaces_message(bridged, monkeypatch):
    err = json.dumps({"ok": False, "kind": "engine_load", "error": "gated fallback"})
    monkeypatch.setattr(tts_bridge.subprocess, "run",
                        fake_run_factory({}, returncode=1, stdout=err,
                                         write_wav_seconds=None))
    with pytest.raises(TTSError, match="engine_load.*gated fallback"):
        tts_bridge.synthesize("Xin chào.", Voice(label="x", preset="Ngọc Lan"))


def test_garbage_stdout_reports_stderr_tail(bridged, monkeypatch):
    monkeypatch.setattr(tts_bridge.subprocess, "run",
                        fake_run_factory({}, returncode=2, stdout="usage: boom",
                                         stderr="argparse exploded here",
                                         write_wav_seconds=None))
    with pytest.raises(TTSError, match="argparse exploded here"):
        tts_bridge.synthesize("Xin chào.", Voice(label="x", preset="Ngọc Lan"))


def test_timeout_maps_to_friendly_error(bridged, monkeypatch):
    monkeypatch.setattr(tts_bridge.subprocess, "run",
                        fake_run_factory({}, raise_timeout=True))
    with pytest.raises(TTSError, match="quá thời gian"):
        tts_bridge.synthesize("Xin chào.", Voice(label="x", preset="Ngọc Lan"),
                              timeout_s=5)


def test_list_voices_presets_plus_user_wavs(monkeypatch, tmp_path):
    monkeypatch.setattr(tts_bridge, "VOICES_DIR", tmp_path / "voices")
    with_txt = tiny_wav(tmp_path / "voices" / "vi" / "mc-chinh.wav")
    with_txt.with_suffix(".txt").write_text("xin chào quý vị", encoding="utf-8")
    tiny_wav(tmp_path / "voices" / "vi" / "khach-moi.wav")  # no sidecar

    voices = tts_bridge.list_voices("vi")
    presets = [v for v in voices if v.preset]
    clones = [v for v in voices if v.wav]
    assert len(presets) == len(tts_bridge.PRESET_VOICES)
    assert {c.wav.name for c in clones} == {"mc-chinh.wav", "khach-moi.wav"}
    by_name = {c.wav.stem: c for c in clones}
    assert by_name["mc-chinh"].transcript == "xin chào quý vị"
    assert by_name["khach-moi"].transcript is None


def test_list_voices_no_user_dir_is_fine(monkeypatch, tmp_path):
    monkeypatch.setattr(tts_bridge, "VOICES_DIR", tmp_path / "missing")
    voices = tts_bridge.list_voices("vi")
    assert len(voices) == len(tts_bridge.PRESET_VOICES)


def test_estimate_seconds_sane():
    # phase-1 measurement: 76 chars ≈ 4.5s, 816 chars ≈ 41.7s
    assert 3.0 < tts_bridge.estimate_seconds("x" * 76) < 6.0
    assert 35.0 < tts_bridge.estimate_seconds("x" * 816) < 55.0
    assert tts_bridge.estimate_seconds("  ") == 0.0


def test_tts_available_reflects_python_presence(monkeypatch, tmp_path):
    monkeypatch.setattr(tts_bridge, "TTS_PYTHON", tmp_path / "no" / "python.exe")
    assert tts_bridge.tts_available() is False


def test_list_voices_survives_broken_sidecar(monkeypatch, tmp_path):
    """A mis-encoded transcript in the user drop-dir must not crash app startup
    (list_voices runs at build_ui time) — voice stays listed, transcript dropped."""
    monkeypatch.setattr(tts_bridge, "VOICES_DIR", tmp_path / "voices")
    wav = tiny_wav(tmp_path / "voices" / "vi" / "mc.wav")
    wav.with_suffix(".txt").write_bytes("xin chào".encode("utf-16"))  # not UTF-8
    voices = tts_bridge.list_voices("vi")
    clone = next(v for v in voices if v.wav)
    assert clone.wav.name == "mc.wav" and clone.transcript is None


def test_json_parse_skips_junk_brace_lines(bridged, monkeypatch):
    """SDK stdout noise after the result JSON must not turn success into failure."""
    rec: dict = {}

    def stdout_with_trailing_junk(out_wav: str) -> str:
        good = json.dumps({"ok": True, "out": out_wav, "duration_s": 0.5,
                           "engine": "vieneu", "model": "v3turbo",
                           "voice": "Ngọc Lan", "synth_s": 0.1, "load_s": 0.1,
                           "chunks": 1})
        return good + "\n{partial interleaved log line"

    def fake_run(cmd, **kwargs):
        rec["cmd"] = [str(c) for c in cmd]
        out_wav = Path(rec["cmd"][rec["cmd"].index("--out") + 1])
        tiny_wav(out_wav)
        return types.SimpleNamespace(returncode=0,
                                     stdout=stdout_with_trailing_junk(str(out_wav)),
                                     stderr="")

    monkeypatch.setattr(tts_bridge.subprocess, "run", fake_run)
    out = tts_bridge.synthesize("Xin chào.", Voice(label="x", preset="Ngọc Lan"))
    assert out.exists()
