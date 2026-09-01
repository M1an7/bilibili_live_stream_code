# Remote Style-Bert-VITS2 Training Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Train a first multilingual Style-Bert-VITS2 `haibara_jp` checkpoint on the remote RTX 5090 while keeping every large artifact under `/root/autodl-tmp/bilibili-voice/`.

**Architecture:** Keep the official Style-Bert-VITS2 source, Python environment, Hugging Face/Torch/pip caches, dataset, logs, and outputs on the remote data disk. Reuse the already verified 120 Japanese WAV/label pairs, train the normal multilingual model with batch size 1, and retain reproducible environment metadata and checkpoints for later ONNX/AIVMX export.

**Tech Stack:** Style-Bert-VITS2, Python 3.10, PyTorch CUDA, Hugging Face models, SSH/rsync, NVIDIA RTX 5090

**Spec:** `docs/superpowers/specs/2026-08-31-multilingual-aivmx-cpu-voice-design.md`

## Global Constraints

- Use normal multilingual Style-Bert-VITS2; do not enable `JP-Extra`.
- Keep all large files under `/root/autodl-tmp/bilibili-voice/`; keep system disk free space above 3 GB.
- Use `batch_size=1` for the first run.
- Do not delete or replace the existing remote GPT-SoVITS environment.
- Source audio and labels remain private and are not pushed to Git or a public release.
- Preserve checkpoints and logs so training can be stopped and resumed.

---

### Task 1: Bootstrap and verify remote storage

**Files:**
- Create: `/root/autodl-tmp/bilibili-voice/style-bert-vits2.env`
- Create: `/root/autodl-tmp/bilibili-voice/tmp/style-bert-vits2/`

**Interfaces:**
- Consumes: SSH access to the remote instance.
- Produces: Stable cache and temporary-directory environment variables on the data disk.

- [x] Check `df -h / /root/autodl-tmp`, `nvidia-smi`, and existing target contents.
- [x] Create dedicated source, environment, cache, data, output, and temporary directories without modifying GPT-SoVITS.
- [x] Write and source `style-bert-vits2.env` with `HF_HOME`, `TORCH_HOME`, `PIP_CACHE_DIR`, `XDG_CACHE_HOME`, `TMPDIR`, and training paths rooted under `/root/autodl-tmp/bilibili-voice/`.
- [x] Verify all resolved paths are on `/root/autodl-tmp` and system-disk free space remains above 3 GB.

### Task 2: Synchronize and validate the training dataset

**Files:**
- Copy: `voice/haibara_jp/sliced/*.wav` -> `/root/autodl-tmp/bilibili-voice/datasets/haibara_jp/raw/`
- Copy: `voice/haibara_jp/sliced/sliced.list` -> `/root/autodl-tmp/bilibili-voice/datasets/haibara_jp/sliced.list`

**Interfaces:**
- Consumes: 120 locally validated WAV/label pairs.
- Produces: A private remote source dataset with checksum manifest.

- [x] Synchronize only the 120 WAV files and active `sliced.list`; exclude backups and GPT-SoVITS weights.
- [x] Verify exactly 120 WAV files and 120 non-empty label rows exist remotely.
- [x] Check that every label path resolves to one WAV and every language tag is `JP`.
- [x] Record SHA-256 checksums for the list and WAV set.

### Task 3: Install and initialize Style-Bert-VITS2

**Files:**
- Create: `/root/autodl-tmp/bilibili-voice/src/Style-Bert-VITS2/`
- Create: `/root/autodl-tmp/bilibili-voice/envs/style-bert-vits2/`
- Create: `/root/autodl-tmp/bilibili-voice/outputs/style-bert-vits2/environment.txt`

**Interfaces:**
- Consumes: Official repository and model downloads.
- Produces: A pinned, CUDA-capable multilingual training installation.

- [x] Clone the official repository and record the exact commit.
- [x] Create the Python environment on the data disk and install a PyTorch build that recognizes the RTX 5090.
- [x] Install project dependencies with caches redirected to the data disk.
- [x] Run initialization while skipping unnecessary default voice assets when supported.
- [x] Verify Python can import Torch and Style-Bert-VITS2, `torch.cuda.is_available()` is true, and CUDA reports the RTX 5090.

### Task 4: Prepare training inputs

**Files:**
- Create: `/root/autodl-tmp/bilibili-voice/data/style-bert-vits2/haibara_jp/`
- Create: `/root/autodl-tmp/bilibili-voice/outputs/style-bert-vits2/preprocess.log`

**Interfaces:**
- Consumes: Remote source dataset and initialized official repository.
- Produces: `config.json`, train/validation lists, normalized audio/features, and style vectors required by training.

- [x] Place or link the private dataset into the official `Data/haibara_jp` layout without duplicating large files unnecessarily.
- [x] Convert label paths only as required by the pinned repository while preserving speaker `haibara_jp` and language `JP`.
- [x] Run normal multilingual preprocessing with batch size 1, a small validation split, and no `--use_jp_extra`.
- [x] Verify preprocessing exits successfully, lists contain valid rows, and every required feature exists.
- [x] Recheck both disks before training.

### Task 5: Train and monitor the v0 model

**Files:**
- Create: `/root/autodl-tmp/bilibili-voice/outputs/style-bert-vits2/train.log`
- Create: `/root/autodl-tmp/bilibili-voice/model_assets/haibara_jp/`

**Interfaces:**
- Consumes: Preprocessed multilingual dataset and batch-size-1 configuration.
- Produces: Resume-capable v0 checkpoints and training logs.

- [x] Confirm the GPU is idle enough to start and configuration has `batch_size=1`.
- [x] Start `train_ms.py` in a persistent remote process with stdout/stderr logged on the data disk.
- [x] Monitor initial iterations for CUDA OOM, NaN/Inf loss, missing features, and unexpected system-disk growth.
- [x] Verify at least one checkpoint is written and can be discovered by the official inference/export tooling.
- [x] Record the process ID, current step, GPU memory, disk usage, repository commit, and environment package versions.

### Task 6: Handoff for audition and export

**Files:**
- Create: `/root/autodl-tmp/bilibili-voice/outputs/style-bert-vits2/STATUS.md`

**Interfaces:**
- Consumes: Verified checkpoint and logs.
- Produces: Exact remote paths and commands for resume, stop, audition, ONNX export, and later download.

- [x] Write a status record containing paths, commit, configuration, and safe stop/resume commands.
- [x] Verify the status commands resolve the active process and newest checkpoint.
- [x] Report any remaining quality-selection step without claiming subjective voice quality.
