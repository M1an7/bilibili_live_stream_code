#!/usr/bin/env python3
"""Verify Style-Bert-VITS2 uses the legacy ONNX exporter on new PyTorch."""

from __future__ import annotations

import ast
import sys
from pathlib import Path


def is_torch_onnx_export(node: ast.Call) -> bool:
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "export"
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == "onnx"
        and isinstance(func.value.value, ast.Name)
        and func.value.value.id == "torch"
    )


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} CONVERT_ONNX_PY", file=sys.stderr)
        return 2

    source_path = Path(sys.argv[1])
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and is_torch_onnx_export(node)]
    if not calls:
        print("no torch.onnx.export calls found", file=sys.stderr)
        return 1

    incompatible_lines: list[int] = []
    for call in calls:
        dynamo = next((keyword.value for keyword in call.keywords if keyword.arg == "dynamo"), None)
        if not isinstance(dynamo, ast.Constant) or dynamo.value is not False:
            incompatible_lines.append(call.lineno)

    if incompatible_lines:
        joined = ", ".join(map(str, incompatible_lines))
        print(f"torch.onnx.export must set dynamo=False at lines: {joined}", file=sys.stderr)
        return 1

    print(f"onnx_export_compat=ok calls={len(calls)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
