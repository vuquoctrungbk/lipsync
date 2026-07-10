"""Phase-1 benchmark: VieNeu v3turbo vs v2 on this machine (CPU-ONNX path).

Runs INSIDE tools/tts/.venv. Loads each model once, synthesizes 3 fixed
Vietnamese sentences (short/medium/long), reports load time + per-sentence
wall time + RTF (synth_seconds / audio_seconds; < 1.0 = faster than realtime).

  tools\\tts\\.venv\\Scripts\\python.exe tools\\tts\\benchmark_tts.py \
      [--voice "Ngọc Lan"] [--voice-ref <ref.wav>] [--models v3turbo]

Writes wavs + benchmark_results.json to outputs/tts-benchmark/.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("HF_HOME", str(REPO_ROOT / "models" / "tts" / "hf"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import numpy as np  # noqa: E402

from engines import ENGINES  # noqa: E402
from tts_cli import split_text, write_wav  # noqa: E402

OUT_DIR = REPO_ROOT / "outputs" / "tts-benchmark"

# Fixed MC-style test sentences (also phase-4 listening material).
SENTENCES = {
    "short": "Xin chào quý vị và các bạn, rất vui được gặp lại trong chương trình hôm nay.",
    "medium": ("Xin chào quý vị và các bạn. Trong bản tin ngày hôm nay, chúng ta sẽ cùng "
               "điểm qua những sự kiện nổi bật nhất về kinh tế, văn hóa và thể thao. "
               "Đầu tiên là câu chuyện về chuyển đổi số tại Thành phố Hồ Chí Minh, "
               "nơi hàng nghìn doanh nghiệp nhỏ đang từng bước đưa hoạt động kinh doanh "
               "lên môi trường trực tuyến."),
    "long": ("Kính thưa quý vị, chuyển đổi số không còn là một khái niệm xa lạ mà đã trở "
             "thành nhu cầu thiết yếu của mọi doanh nghiệp Việt Nam. Theo báo cáo mới nhất, "
             "hơn bảy mươi phần trăm doanh nghiệp vừa và nhỏ đã bắt đầu ứng dụng công nghệ "
             "vào quy trình vận hành hằng ngày. Từ việc quản lý kho hàng, chăm sóc khách hàng, "
             "cho đến các chiến dịch quảng bá sản phẩm, tất cả đều có thể thực hiện trên một "
             "nền tảng duy nhất. Điều này giúp tiết kiệm chi phí, nâng cao hiệu quả và mở ra "
             "cơ hội tiếp cận thị trường quốc tế. Tuy nhiên, thách thức lớn nhất vẫn nằm ở "
             "con người: đội ngũ nhân sự cần được đào tạo bài bản để làm chủ công cụ mới. "
             "Trong phần tiếp theo của chương trình, chúng tôi sẽ trò chuyện cùng chuyên gia "
             "để tìm hiểu lộ trình chuyển đổi phù hợp cho từng quy mô doanh nghiệp. "
             "Xin cảm ơn quý vị đã theo dõi và hẹn gặp lại."),
}


def bench_model(model: str, voice: str | None, voice_ref: str | None,
                voice_ref_text: str | None, device: str = "cpu") -> dict:
    engine = ENGINES["vieneu"](model=model, device=device)
    t0 = time.time()
    engine.load()
    load_s = time.time() - t0
    print(f"[bench] {model}: loaded in {load_s:.1f}s", file=sys.stderr)

    runs = []
    for key, sentence in SENTENCES.items():
        chunks = split_text(sentence)
        t1 = time.time()
        pieces = []
        sr = None
        for c in chunks:
            samples, sr = engine.synthesize_chunk(c, voice_ref, voice_ref_text, voice)
            pieces.append(samples)
        synth_s = time.time() - t1
        audio = np.concatenate(pieces)
        audio_s = len(audio) / sr
        resolved = getattr(engine, "backend_device", engine.device)
        wav = OUT_DIR / f"bench_{model}_{key}_{resolved}.wav"
        write_wav(wav, audio, sr)
        rtf = synth_s / audio_s if audio_s else float("inf")
        runs.append({"sentence": key, "chars": len(sentence), "chunks": len(chunks),
                     "audio_s": round(audio_s, 2), "synth_s": round(synth_s, 2),
                     "rtf": round(rtf, 3), "sr": sr, "wav": str(wav)})
        print(f"[bench] {model}/{key}: {audio_s:.1f}s audio in {synth_s:.1f}s "
              f"(RTF {rtf:.2f})", file=sys.stderr)
    return {"model": model, "load_s": round(load_s, 1), "runs": runs}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default="Ngọc Lan", help="SDK preset voice name")
    ap.add_argument("--voice-ref", default=None, help="clone from wav instead of preset")
    ap.add_argument("--voice-ref-text", default=None)
    ap.add_argument("--models", nargs="+", default=["v3turbo"])
    ap.add_argument("--device", default="cpu", choices=("cpu", "cuda", "auto"))
    args = ap.parse_args()
    voice = None if args.voice_ref else args.voice

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results = {"machine": f"RTX 3060 12GB / Xeon E5-2680 v4 (device={args.device})",
               "device": args.device,
               "voice": voice or f"clone:{args.voice_ref}", "models": []}
    for model in args.models:
        try:
            results["models"].append(
                bench_model(model, voice, args.voice_ref, args.voice_ref_text,
                            device=args.device))
        except Exception as exc:  # keep going: one model failing IS a result
            import traceback
            traceback.print_exc(file=sys.stderr)
            results["models"].append({"model": model, "error": f"{type(exc).__name__}: {exc}"})

    out_json = OUT_DIR / "benchmark_results.json"
    out_json.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False))
    print(f"[bench] wrote {out_json}", file=sys.stderr)


if __name__ == "__main__":
    main()
