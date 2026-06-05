# Third-Party Notices & Licenses

This application is assembled from open-source components that all permit
commercial use. Bundled model weights are commercial-safe; no non-commercial
weights are included or downloaded.

## Models

| Component | Repo | License | Commercial use |
|---|---|---|---|
| SadTalker (animation) | OpenTalker/SadTalker | Apache-2.0 (code) + MIT (weights) | Yes |
| BiRefNet-matting | ZhengPeng7/BiRefNet | MIT | Yes |
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
  (research-only), RVM (GPL-3.0 copyleft), RMBG-2.0 (CC BY-NC 4.0).

## Obligations

- Retain the Apache-2.0 / MIT notices above when redistributing.
- The optional GFPGAN enhancer downloads its weights from the official release on
  first enable.
