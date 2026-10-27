"""Small, scriptable CLI; stdout is JSON with --json."""
from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import sys
from pathlib import Path

from .pipeline import Config, run, verify


def doctor() -> dict:
    result = {"platform": platform.platform(), "machine": platform.machine(),
              "mlx_installed": importlib.util.find_spec("mlx_embedding_models") is not None,
              "torch_installed": importlib.util.find_spec("torch") is not None}
    if result["torch_installed"]:
        import torch
        result["cuda_available"] = bool(torch.cuda.is_available())
        result["mps_available"] = bool(torch.backends.mps.is_available())
    return result


def parser() -> argparse.ArgumentParser:
    main = argparse.ArgumentParser(prog="local-vector-batch",
                                   description="Generate verifiable local embeddings from JSONL")
    sub = main.add_subparsers(dest="command", required=True)
    health = sub.add_parser("doctor", help="show installed backends and accelerator availability")
    health.add_argument("--json", action="store_true")
    embed = sub.add_parser("embed", help="embed documents from JSONL")
    embed.add_argument("--input", required=True, type=Path)
    embed.add_argument("--output", required=True, type=Path)
    embed.add_argument("--model", default="BAAI/bge-base-en-v1.5")
    embed.add_argument("--backend", choices=["auto", "mlx", "torch"], default="auto")
    embed.add_argument("--device", choices=["auto", "gpu", "mps", "cuda", "cpu"], default="auto")
    embed.add_argument("--pooling", choices=["cls", "mean"], default="cls")
    embed.add_argument("--format", choices=["vectorize", "generic"], default="vectorize")
    embed.add_argument("--batch-size", type=int, default=32)
    embed.add_argument("--chunk-chars", type=int, default=1500)
    embed.add_argument("--overlap", type=int, default=150)
    embed.add_argument("--min-chars", type=int, default=80)
    embed.add_argument("--workers", type=int, default=1)
    embed.add_argument("--max-tokens", type=int, default=512)
    embed.add_argument("--normalize", action=argparse.BooleanOptionalAction, default=True)
    embed.add_argument("--revision", help="Hugging Face revision, Torch backend only")
    embed.add_argument("--threads-per-worker", type=int, default=1)
    embed.add_argument("--offline", action="store_true", help="use cached model files only")
    embed.add_argument("--resume", action="store_true")
    embed.add_argument("--json", action="store_true")
    check = sub.add_parser("verify", help="check hashes, dimensions, and unique vector IDs")
    check.add_argument("--output", required=True, type=Path)
    check.add_argument("--json", action="store_true")
    return main


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "doctor":
            result = doctor()
        elif args.command == "verify":
            result = verify(args.output)
        else:
            keys = Config.__dataclass_fields__.keys()
            values = vars(args)
            result = run(Config(input_path=values["input"], output_dir=values["output"],
                                **{key: values[key] for key in keys if key not in {"input_path", "output_dir"}}))
            result = {"complete": result["complete"], "batches": len(result["batches"]),
                      "vectors": sum(item["count"] for item in result["batches"]),
                      "dimensions": result["dimensions"], "model": result["model"],
                      "device": result["device"], "output": str(args.output)}
        print(json.dumps(result, ensure_ascii=False) if args.json else json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (ValueError, RuntimeError, OSError, KeyError) as error:
        print(json.dumps({"error": str(error)}) if getattr(args, "json", False) else f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
