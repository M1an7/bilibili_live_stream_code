from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


FORBIDDEN_FILE_PARTS = (
    "providers_cuda",
    "providers_tensorrt",
    "cudnn",
    "cublas",
    "cufft",
    "directml",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify a BiliLiveTool CPU speech runtime")
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--allow-unsigned-development", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = args.project_root.resolve()
    runtime_root = args.runtime_root.resolve()
    sys.path.insert(0, str(project_root))

    from backend.cpu_runtime.registry import CpuRuntimeVerifier

    record = CpuRuntimeVerifier(
        expected_platform="windows-x86_64",
        allow_unsigned=args.allow_unsigned_development,
    ).verify_directory(runtime_root)

    runtime_python = runtime_root / "python" / "python.exe"
    if not runtime_python.is_file():
        raise FileNotFoundError(f"Runtime Python was not found: {runtime_python}")

    probe_code = """
import importlib.util
import json
import onnxruntime as ort
available = set(ort.get_available_providers())
forbidden = available.intersection({"CUDAExecutionProvider", "TensorrtExecutionProvider", "DmlExecutionProvider"})
assert not forbidden, forbidden
assert "CPUExecutionProvider" in available
assert importlib.util.find_spec("torch") is None
print(json.dumps({"providers": ["CPUExecutionProvider"], "vram_mb": 0, "torch": False}))
"""
    probe = subprocess.run(
        [str(runtime_python), "-I", "-c", probe_code],
        check=True,
        capture_output=True,
        text=True,
    )

    forbidden_files = [
        str(path.relative_to(runtime_root))
        for path in runtime_root.rglob("*")
        if path.is_file() and any(part in path.name.lower() for part in FORBIDDEN_FILE_PARTS)
    ]
    if forbidden_files:
        raise RuntimeError(f"CPU runtime contains forbidden GPU provider files: {forbidden_files[:10]}")

    print(json.dumps({
        "runtime": record.to_dict(),
        "probe": json.loads(probe.stdout),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
