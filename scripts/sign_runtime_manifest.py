from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

COMMIT = re.compile(r"^[0-9a-f]{40}$")


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def canonical(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def private_key(path: str):
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as exc:
        raise SystemExit("cryptography is required only when signing a release runtime") from exc
    raw = Path(path).read_bytes()
    try:
        key = serialization.load_pem_private_key(raw, password=None)
    except ValueError:
        try:
            decoded = base64.b64decode(raw.strip(), validate=True)
            key = Ed25519PrivateKey.from_private_bytes(decoded)
        except ValueError as exc:
            raise SystemExit("private key must be Ed25519 PKCS8 PEM or base64 raw bytes") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise SystemExit("private key is not Ed25519")
    return key


def main() -> int:
    parser = argparse.ArgumentParser(description="Create and optionally sign a BiliLiveTool voice runtime manifest")
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument(
        "--engine",
        choices=("gpt-sovits-gpu", "style-bert-vits2-onnx-cpu"),
        default="gpt-sovits-gpu",
    )
    parser.add_argument("--runtime-id", default="gpt-sovits-cu126")
    parser.add_argument("--build-version", required=True)
    parser.add_argument("--gpt-sovits-commit", default="")
    parser.add_argument("--style-bert-vits2-commit", default="")
    parser.add_argument("--aivmlib-commit", default="")
    parser.add_argument("--private-key", default="")
    parser.add_argument("--public-key-output", default="")
    parser.add_argument("--allow-unsigned", action="store_true")
    parser.add_argument("--python-version", default="3.10")
    parser.add_argument("--torch-version", default="2.7.1+cu126")
    parser.add_argument("--cuda-version", default="12.6")
    parser.add_argument("--onnxruntime-version", default="1.22.1")
    parser.add_argument("--default-threads", type=int, default=4)
    args = parser.parse_args()

    root = Path(args.runtime_root).resolve()
    if not root.is_dir():
        raise SystemExit("runtime root does not exist")
    if args.engine == "gpt-sovits-gpu":
        if not COMMIT.fullmatch(args.gpt_sovits_commit):
            raise SystemExit("GPT-SoVITS commit must be a full lowercase SHA-1")
        required_files = (
            "engine/sidecar.py",
            "engine/protocol.py",
            "python/python.exe",
            "upstream/LICENSE",
        )
    else:
        if not COMMIT.fullmatch(args.style_bert_vits2_commit):
            raise SystemExit("Style-Bert-VITS2 commit must be a full lowercase SHA-1")
        if not COMMIT.fullmatch(args.aivmlib_commit):
            raise SystemExit("aivmlib commit must be a full lowercase SHA-1")
        if not 1 <= args.default_threads <= 4:
            raise SystemExit("CPU runtime default threads must be between 1 and 4")
        required_files = (
            "engine/sidecar.py",
            "engine/protocol.py",
            "python/python.exe",
            "upstream/style-bert-vits2/LICENSE",
            "upstream/aivmlib/LICENSE",
        )
    for required in required_files:
        if not (root / required).is_file():
            raise SystemExit(f"runtime is missing {required}")

    files: dict[str, str] = {}
    for member in sorted(root.rglob("*")):
        relative = member.relative_to(root).as_posix()
        info = member.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise SystemExit(f"runtime cannot contain links: {relative}")
        if member.is_file() and relative not in ("runtime-manifest.json", "runtime-manifest.sig"):
            files[relative] = hash_file(member)
    if args.engine == "gpt-sovits-gpu":
        payload = {
            "schema_version": 1,
            "runtime_id": args.runtime_id,
            "engine": "gpt-sovits-gpu",
            "engine_api_version": 1,
            "platform": "windows-x86_64",
            "build_version": args.build_version,
            "gpt_sovits_commit": args.gpt_sovits_commit,
            "python_version": args.python_version,
            "torch_version": args.torch_version,
            "cuda_version": args.cuda_version,
            "supported_model_versions": ["v2Pro", "v2ProPlus"],
            "supported_languages": ["ja"],
            "entrypoint": "engine/sidecar.py",
            "gpu": True,
            "precision": "fp16",
            "minimum_compute_capability": "6.1",
            "minimum_vram_mb": 4096,
            "files": files,
        }
    else:
        payload = {
            "schema_version": 1,
            "runtime_id": args.runtime_id,
            "engine": "style-bert-vits2-onnx-cpu",
            "engine_api_version": 1,
            "platform": "windows-x86_64",
            "build_version": args.build_version,
            "style_bert_vits2_commit": args.style_bert_vits2_commit,
            "aivmlib_commit": args.aivmlib_commit,
            "python_version": args.python_version,
            "onnxruntime_version": args.onnxruntime_version,
            "supported_architectures": ["Style-Bert-VITS2"],
            "supported_languages": ["zh-CN"],
            "entrypoint": "engine/sidecar.py",
            "gpu": False,
            "providers": ["CPUExecutionProvider"],
            "default_threads": args.default_threads,
            "files": files,
        }
    manifest_path = root / "runtime-manifest.json"
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8")
    signature_path = root / "runtime-manifest.sig"
    if args.private_key:
        from cryptography.hazmat.primitives import serialization

        key = private_key(args.private_key)
        signature_path.write_text(base64.b64encode(key.sign(canonical(payload))).decode("ascii") + "\n", "ascii")
        if args.public_key_output:
            public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            Path(args.public_key_output).write_text(base64.b64encode(public).decode("ascii") + "\n", "ascii")
    elif args.allow_unsigned:
        signature_path.unlink(missing_ok=True)
    else:
        raise SystemExit("a release private key is required; use --allow-unsigned only for local development")
    print(json.dumps({"runtime_root": str(root), "files": len(files), "signed": bool(args.private_key)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
