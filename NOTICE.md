# Third-Party Notices & Licenses

This build targets **personal (non-commercial) use**: the default matting
engine (RobustVideoMatting) is GPL-3.0. Setting `commercial_safe=True` in
`RenderConfig` forces the BiRefNet (MIT) path and never loads RVM — with that
flag, every component below permits commercial use.

## Models

| Component | Repo | License | Commercial use |
|---|---|---|---|
| SadTalker (animation) | OpenTalker/SadTalker | Apache-2.0 (code) + MIT (weights) | Yes |
| RobustVideoMatting (default matting) | PeterL1n/RobustVideoMatting @ `53d74c68` | GPL-3.0 | **No** (personal use; disabled by `commercial_safe=True`) |
| BiRefNet-matting (commercial-safe matting) | ZhengPeng7/BiRefNet | MIT | Yes |
| GFPGAN (optional enhancer) | TencentARC/GFPGAN | Apache-2.0 | Yes |
| facexlib (face utils/weights) | xinntao/facexlib | Apache-2.0 | Yes |

## Key libraries

PyTorch / torchvision / torchaudio (BSD-3), Gradio (Apache-2.0),
transformers / timm / huggingface_hub (Apache-2.0/MIT), librosa (ISC),
numpy / scipy / scikit-image (BSD), opencv-python (Apache-2.0),
imageio / imageio-ffmpeg (BSD), ffmpeg (LGPL/GPL build — invoked as an external
executable, not linked).

## Deliberately excluded (license-incompatible with free commercial use)

- Wav2Lip (LRS2/BBC non-commercial), LivePortrait (InsightFace weights non-commercial),
  Hallo2/Hallo3 (CogVideo-5B non-commercial), Sonic / FLOAT / V-Express / DreamTalk
  (research-only), RMBG-2.0 (CC BY-NC 4.0).
- RVM (GPL-3.0) is NOT excluded anymore: it is the default matting engine for
  personal use, contained in `lipsync/matting_rvm.py` behind the
  `commercial_safe` flag (see the Models table above).

## Obligations

- Retain the Apache-2.0 / MIT notices above when redistributing.
- The optional GFPGAN enhancer downloads its weights from the official release on
  first enable.
