from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    scripts_dir = root / "scripts"
    sys.path.insert(0, str(scripts_dir))
    cli_path = scripts_dir / "findreason.py"
    spec = importlib.util.spec_from_file_location("_findreason_cli", cli_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load FindReason CLI from {cli_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return int(module.main())


if __name__ == "__main__":
    raise SystemExit(main())
