# Remote Style-Bert-VITS2 Training Status

Status time: 2026-09-01 02:36 +08:00
Remote root: `/root/autodl-tmp/bilibili-voice`
State: training and export completed; subjective voice selection remains with the user.

## Outcome

- Trained the normal multilingual Style-Bert-VITS2 architecture, not JP-Extra.
- Completed 100 epochs / 11,500 steps with `batch_size=1` on an RTX 5090.
- Kept all 12 inference checkpoints and the six newest resume checkpoint groups.
- Exported the final checkpoint to a single-file ONNX model and AIVMX container.
- Verified PyTorch CUDA synthesis and ONNX Runtime CPU synthesis in Chinese and Japanese.
- Generated fixed-seed Chinese/Japanese audition pairs for steps 8,000, 10,000, and 11,500.

No subjective claim is made about voice similarity or quality. Select the preferred checkpoint by listening to the audition files.

## Reproducibility

- Style-Bert-VITS2 source: `/root/autodl-tmp/bilibili-voice/src/Style-Bert-VITS2`
- Upstream commit: `66de777e06392c0f313600be03c43ef96658b244`
- Environment: `/root/autodl-tmp/bilibili-voice/envs/style-bert-vits2`
- Environment variables: `/root/autodl-tmp/bilibili-voice/style-bert-vits2.env`
- Private source dataset: `/root/autodl-tmp/bilibili-voice/datasets/haibara_jp`
- Prepared dataset: `/root/autodl-tmp/bilibili-voice/data/style-bert-vits2/haibara_jp`
- Model assets: `/root/autodl-tmp/bilibili-voice/model_assets/haibara_jp`
- Logs and auditions: `/root/autodl-tmp/bilibili-voice/outputs/style-bert-vits2`

Key versions:

- Python 3.10.21
- Style-Bert-VITS2 2.7.0
- PyTorch 2.13.0+cu130
- NumPy 1.26.4
- ONNX 1.17.0
- ONNX Runtime 1.23.2
- onnxsim-prebuilt 0.4.39.post2
- aivmlib 1.2.2

The source tree contains three intentional compatibility changes:

- `style_gen.py`: imports the local torchaudio compatibility shim before pyannote.
- `sbv2_torchaudio_compat.py`: supplies removed torchaudio metadata/load APIs required by pyannote 3.x.
- `convert_onnx.py`: sets `dynamo=False` on both ONNX export calls because PyTorch 2.13's new Dynamo exporter cannot capture the model's data-dependent spline branch.

The reusable patches and regression checks are stored in this project's `scripts/remote/` directory.

## Dataset verification

- WAV files: 120
- Non-empty label rows: 120
- Speaker: `haibara_jp`
- Source language tag: `JP`
- Training/validation rows: 115 / 5
- `sliced.list` SHA-256: `a7ac7b6c61503f41a0c5766dd7c1e87b568922191a0b1893b391a72d20d78b05`
- WAV checksum manifest: `/root/autodl-tmp/bilibili-voice/datasets/haibara_jp/wavs.sha256`
- WAV checksum manifest SHA-256: `5c3268c7b0a088c97e43c718defc28749140c9bc3b6ddb5555713979287ead24`

## Training verification

- Start: 2026-09-01 01:22:59 +08:00
- Final save: 2026-09-01 02:11:36 +08:00
- Final epoch / step: 100 / 11,500
- Final TensorBoard scalar step: 11,450
- All 27 latest scalar values finite: yes
- Final logged generator loss: 28.965412
- Final logged discriminator loss: 2.362924
- CUDA OOM, traceback, NaN/Inf loss: none
- Training process: stopped normally after final save
- Training log: `/root/autodl-tmp/bilibili-voice/outputs/style-bert-vits2/train.log`
- Preprocess log: `/root/autodl-tmp/bilibili-voice/outputs/style-bert-vits2/preprocess.log`

The final PyTorch warning about `destroy_process_group()` is an exit-time warning from a one-process NCCL group. It occurred after all final files were saved and is not a checkpoint failure.

## Final artifacts

| Artifact | Remote path | SHA-256 |
|---|---|---|
| Inference weights | `/root/autodl-tmp/bilibili-voice/model_assets/haibara_jp/haibara_jp_e100_s11500.safetensors` | `c4baadb7b0e6458de7775d620b132667a436ea81e716e79f9d6ceacdcd86608d` |
| CPU ONNX model | `/root/autodl-tmp/bilibili-voice/model_assets/haibara_jp/haibara_jp_e100_s11500.onnx` | `2378acf6d79c8f6a4c5f6a5f422b86f7192e1b425387903275843be05a93e7d7` |
| Importable AIVMX | `/root/autodl-tmp/bilibili-voice/model_assets/haibara_jp/haibara_jp_e100_s11500.aivmx` | `43f3de83e48ee27c967956f7c07ffa90a58ab47e8db502bd9a5a899beeadd965` |
| Hyperparameters | `/root/autodl-tmp/bilibili-voice/model_assets/haibara_jp/config.json` | `ae3ec12a44c219d6bbd3d9e518ebd66cf9959164e192c40eef650ce8184f6cda` |
| Style vectors | `/root/autodl-tmp/bilibili-voice/model_assets/haibara_jp/style_vectors.npy` | `e38b53f381456ad1240fbc4a8f454f565f6f7f58a1301ca2210326637ed578a0` |

The AIVMX manifest reports architecture `Style-Bert-VITS2`, supported languages `ja`, `en-US`, and `zh-CN`, one normal style, 100 epochs, and 11,500 steps. It has no ONNX external data. Its conservative authorization text records the user's confirmation for training, synthesis, and livestream use while granting no redistribution right.

Resume checkpoint groups remain for steps 7,000, 8,000, 9,000, 10,000, 11,000, and 11,500 under:

`/root/autodl-tmp/bilibili-voice/data/style-bert-vits2/haibara_jp/models`

## Audition files

Fixed-seed comparison pairs:

`/root/autodl-tmp/bilibili-voice/outputs/style-bert-vits2/audition/candidates`

- `haibara_e70_s8000_zh.wav`
- `haibara_e70_s8000_jp.wav`
- `haibara_e87_s10000_zh.wav`
- `haibara_e87_s10000_jp.wav`
- `haibara_e100_s11500_zh.wav`
- `haibara_e100_s11500_jp.wav`

All comparison files are 44.1 kHz non-silent PCM WAV files. Additional final PyTorch and ONNX validation WAV files are in sibling audition directories.

## Performance evidence

Server CPU: dual-socket Intel Xeon Gold 6459C, 128 logical CPUs.
GPU: NVIDIA GeForce RTX 5090 32 GB.

PyTorch CUDA functional checks were fast after model/BERT caching, but they are not a laptop deployment benchmark.

ONNX CPU, same Chinese sentence producing about 3.15 seconds of audio:

| Voice-network threads | Hot synthesis time |
|---:|---:|
| 1 | 3.201 s |
| 2 | 2.627 s |
| 4 | 2.177–2.273 s |
| 8 | 2.218 s |
| 16 | 2.769 s |
| 32 | 2.988 s |
| default (128 logical CPUs) | 7.078 s |

The four-thread test used no GPU memory beyond the driver's idle 2 MiB. Process RSS was about 1.97 GiB and peak RSS about 2.85 GiB because the multilingual BERT model remains large. The tuned latency is within the user's acceptable 2–3 second fallback, but the memory footprint is not comparable to Windows system TTS. Benchmark again on the target laptop before integrating this runtime into the desktop application.

## Operations

Connect:

```bash
ssh -p 33670 root@connect.weste.seetacloud.com
source /root/autodl-tmp/bilibili-voice/style-bert-vits2.env
```

Inspect training status:

```bash
PID=$(cat /root/autodl-tmp/bilibili-voice/outputs/style-bert-vits2/train.pid)
kill -0 "$PID" 2>/dev/null && echo running || echo stopped
tail -n 40 /root/autodl-tmp/bilibili-voice/outputs/style-bert-vits2/train.log
df -h / /root/autodl-tmp
```

Stop an active training process safely:

```bash
PID=$(cat /root/autodl-tmp/bilibili-voice/outputs/style-bert-vits2/train.pid)
kill -TERM "$PID"
```

Resume an interrupted run (or continue only after raising `train.epochs` above 100 in `config.json`):

```bash
source /root/autodl-tmp/bilibili-voice/style-bert-vits2.env
cd /root/autodl-tmp/bilibili-voice/src/Style-Bert-VITS2
nohup python train_ms.py \
  --config /root/autodl-tmp/bilibili-voice/data/style-bert-vits2/haibara_jp/config.json \
  --model /root/autodl-tmp/bilibili-voice/data/style-bert-vits2/haibara_jp \
  --assets_root /root/autodl-tmp/bilibili-voice/model_assets \
  --no_progress_bar \
  >> /root/autodl-tmp/bilibili-voice/outputs/style-bert-vits2/train-resume.log 2>&1 < /dev/null &
echo $! > /root/autodl-tmp/bilibili-voice/outputs/style-bert-vits2/train.pid
```

Re-export the final AIVMX after applying the recorded compatibility patch:

```bash
source /root/autodl-tmp/bilibili-voice/style-bert-vits2.env
cd /root/autodl-tmp/bilibili-voice/src/Style-Bert-VITS2
python convert_onnx.py \
  --model /root/autodl-tmp/bilibili-voice/model_assets/haibara_jp/haibara_jp_e100_s11500.safetensors \
  --aivmx --force-convert
```

The system disk retained 5.3 GB free. After purging only the reproducible pip download cache, the data disk retained 16 GB free. No training or model artifacts were deleted.
