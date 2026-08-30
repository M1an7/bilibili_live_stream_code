# BiliLiveTool GPT-SoVITS GPU Runtime

This directory is the small, authenticated wrapper copied into the separate GPU runtime ZIP.

- Upstream: `RVC-Boss/GPT-SoVITS`
- Pinned commit: see `PINNED_GPT_SOVITS_COMMIT`
- Upstream license: MIT; the builder preserves the upstream `LICENSE` file.
- Supported voices: GPT-SoVITS `v2Pro` and `v2ProPlus`
- Output language: Japanese (`ja`)
- Device policy: NVIDIA CUDA, FP16, batch size 1

The wrapper binds only to `127.0.0.1`, requires a random bearer token, and accepts model/reference paths only under the application's authorized voice data directory. It does not expose the upstream API's arbitrary model-path or process-control endpoints.

The desktop application does not import PyTorch or GPT-SoVITS. It starts this sidecar only while a personalized voice is enabled and terminates the process to release VRAM when the voice is disabled or the app exits.
