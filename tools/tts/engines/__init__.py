"""Engine registry for the TTS CLI.

Round-2 slot: when multi-language lands (en/fr/ko/ja), a chatterbox_engine
registers here — Chatterbox was dropped from the Vietnamese-first round because
its official weights have no Vietnamese (D2 amendment, 2026-07-05).
"""
from .vieneu_engine import VieNeuEngine

ENGINES = {
    VieNeuEngine.name: VieNeuEngine,
}
