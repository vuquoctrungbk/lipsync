"""VieNeu-TTS engine (Apache-2.0) — Vietnamese specialist, zero-shot cloning.

Verified against vieneu 3.0.11:
  Vieneu(mode="v3turbo") -> V3TurboVieNeuTTS(backbone_repo='pnnbao-ump/VieNeu-TTS-v3-Turbo',
                                             device='auto', backend='auto', ...)
  .infer(text, ref_audio=..., ref_text=None, voice=..., max_chars=384,
         apply_watermark=True, ...) -> numpy.ndarray  (Perth watermark on by default)
  .list_preset_voices() / .get_preset_voice(name)  -> SDK-bundled preset voices

D6 (user decision): v3 Turbo (48 kHz) is THE model this round. The v2-family
lives behind other factory modes ('standard'/'turbo') which REQUIRE the
`vieneu[gpu]` extras — per D6 it is installed only on documented v3-failure
evidence, so this engine deliberately exposes just "v3turbo".

Device (2026-07-08): CPU = torch-free ONNX (default, zero VRAM contention).
GPU = the SDK's PyTorch backend, which needs torch+torchaudio in this venv
(opt-in: scripts/setup_tts_env.ps1 -Gpu). device="auto" lets the SDK pick.
"""
from __future__ import annotations

from .base_engine import (
    KIND_ENGINE_LOAD, BaseEngine, TTSEngineError, coerce_audio,
)

_V3_SR = 48000  # documented v3 Turbo output rate (fallback if SDK lacks the attr)


class VieNeuEngine(BaseEngine):
    name = "vieneu"
    models = ("v3turbo",)

    def __init__(self, model: str, device: str = "cpu"):
        if model in ("v2", "standard", "turbo"):
            raise TTSEngineError(
                KIND_ENGINE_LOAD,
                "VieNeu v2-family is the gated fallback (D6): it needs the "
                "vieneu[gpu] extras and is installed only on documented v3 "
                "failure evidence — see the phase-1 benchmark report.",
            )
        super().__init__(model, device)

    def load(self) -> None:
        try:
            from vieneu import Vieneu
        except ImportError as exc:
            raise TTSEngineError(
                KIND_ENGINE_LOAD,
                f"vieneu SDK not importable in tools/tts/.venv: {exc}",
            ) from exc
        # GPU needs torch (the SDK's PyTorch backend). Fail early with an
        # actionable message instead of a deep SDK ImportError/CUDA crash.
        if self.device == "cuda":
            self._require_cuda()
        # device -> SDK: 'cpu' forces torch-free ONNX; 'cuda' forces PyTorch;
        # 'auto' lets the SDK choose (torch+cuda -> GPU, else CPU ONNX).
        # First load downloads the backbone + MOSS audio tokenizer from HF
        # (HF_HOME is repo-local, set by tts_cli.py before this import).
        self._tts = Vieneu(mode="v3turbo", device=self.device)
        self._sr = int(getattr(self._tts, "sample_rate", 0) or _V3_SR)
        # Report the RESOLVED device (SDK backend 'pytorch' == GPU, 'onnx' == CPU)
        # so JSON is truthful even when the caller passed device='auto'.
        self.backend_device = ("cuda" if getattr(self._tts, "backend", "") == "pytorch"
                               else "cpu")

    @staticmethod
    def _require_cuda() -> None:
        try:
            import torch
        except ImportError as exc:
            raise TTSEngineError(
                KIND_ENGINE_LOAD,
                "GPU cần torch trong venv TTS. Cài: powershell -ExecutionPolicy "
                "Bypass -File scripts\\setup_tts_env.ps1 -Gpu",
            ) from exc
        if not torch.cuda.is_available():
            raise TTSEngineError(
                KIND_ENGINE_LOAD,
                "torch đã cài nhưng CUDA không khả dụng (driver/GPU?) — "
                "dùng CPU hoặc kiểm tra lại GPU.",
            )

    def list_presets(self) -> list[str]:
        """Named preset voices bundled with the SDK (Apache-2.0)."""
        try:
            return list(self._tts.list_preset_voices())
        except Exception:
            return []

    def synthesize_chunk(self, text: str, voice_ref: str | None,
                         voice_ref_text: str | None,
                         voice_preset: str | None):
        # Either a named SDK preset voice or a reference wav (zero-shot clone).
        kwargs = {}
        if voice_preset:
            kwargs["voice"] = voice_preset
        else:
            kwargs["ref_audio"] = voice_ref
            if voice_ref_text:
                kwargs["ref_text"] = voice_ref_text
        # Chunks arrive <= 380 chars (CLI), under the SDK's max_chars=384, so
        # the SDK never re-splits and our silence joins stay deterministic.
        result = self._tts.infer(text, **kwargs)
        return coerce_audio(result, fallback_sr=self._sr)
