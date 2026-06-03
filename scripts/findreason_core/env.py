from __future__ import annotations

import os
import json
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[2]
_LOADED = False


def _split_paths(value: str) -> list[Path]:
    paths: list[Path] = []
    for item in value.split(os.pathsep):
        text = item.strip()
        if text:
            paths.append(Path(text).expanduser())
    return paths


def env_candidate_paths(
    *,
    skill_root: Path | None = None,
    cwd: Path | None = None,
    home: Path | None = None,
) -> list[Path]:
    root = (skill_root or SKILL_ROOT).resolve()
    working_dir = (cwd or Path.cwd()).resolve()
    user_home = (home or Path.home()).resolve()
    explicit = os.getenv("FINDREASON_ENV_FILE") or os.getenv("FINDREASON_ENV_PATH") or ""
    candidates: list[Path] = []
    candidates.extend(_split_paths(explicit))
    candidates.extend([root / ".env.local", root / ".env"])
    candidates.extend([working_dir / ".env.local", working_dir / ".env"])
    candidates.extend(
        [
            user_home / ".findreason" / ".env.local",
            user_home / ".codex" / "findreason" / ".env.local",
        ]
    )

    deduped: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path.expanduser().resolve() if path.exists() else path.expanduser())
        if key not in seen:
            seen.add(key)
            deduped.append(path.expanduser())
    return deduped


def runtime_config_candidate_paths(
    *,
    skill_root: Path | None = None,
) -> list[Path]:
    root = (skill_root or SKILL_ROOT).resolve()
    explicit = os.getenv("FINDREASON_CONFIG_FILE") or os.getenv("FINDREASON_CONFIG_PATH") or ""
    candidates: list[Path] = []
    candidates.extend(_split_paths(explicit))
    candidates.extend(
        [
            root / "config" / "runtime_defaults.json",
            root / "findreason.runtime.json",
        ]
    )

    deduped: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path.expanduser().resolve() if path.exists() else path.expanduser())
        if key not in seen:
            seen.add(key)
            deduped.append(path.expanduser())
    return deduped


def _load_runtime_config(path: Path) -> bool:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"runtime config must be a JSON object: {path}")
    loaded = False
    for key, value in data.items():
        if not isinstance(key, str) or not key:
            continue
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        os.environ.setdefault(key, text)
        loaded = True
    return loaded


def load_runtime_env(
    *,
    force: bool = False,
    skill_root: Path | None = None,
    cwd: Path | None = None,
    home: Path | None = None,
) -> list[Path]:
    global _LOADED
    if _LOADED and not force:
        return []
    if os.getenv("FINDREASON_ENV_DISABLE", "").lower() in {"1", "true", "yes", "on"}:
        _LOADED = True
        return []
    try:
        from dotenv import load_dotenv
    except Exception:
        _LOADED = True
        return []

    loaded: list[Path] = []
    env_paths = env_candidate_paths(skill_root=skill_root, cwd=cwd, home=home)
    explicit_paths = set(_split_paths(os.getenv("FINDREASON_ENV_FILE") or os.getenv("FINDREASON_ENV_PATH") or ""))
    for path in env_paths:
        if path in explicit_paths and path.exists():
            load_dotenv(path, override=False)
            loaded.append(path)
    for path in runtime_config_candidate_paths(skill_root=skill_root):
        if path.exists() and _load_runtime_config(path):
            loaded.append(path)
    for path in env_paths:
        if path in explicit_paths:
            continue
        if path.exists():
            load_dotenv(path, override=False)
            loaded.append(path)
    load_dotenv(override=False)
    _LOADED = True
    return loaded
