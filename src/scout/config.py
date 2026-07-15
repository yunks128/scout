from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"


def load_env(path: Path | None = None) -> None:
    """Load KEY=value pairs from a local .env into os.environ.

    Existing environment variables always win, so CI-provided secrets and
    explicit shell exports are never clobbered. A missing file is a no-op.
    """
    env_path = path or REPO_ROOT / ".env"
    try:
        text = env_path.read_text()
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.removeprefix("export ").strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


@lru_cache
def _load(name: str) -> dict:
    with open(CONFIG_DIR / name) as f:
        return yaml.safe_load(f)


def keywords() -> dict:
    return _load("keywords.yaml")


def naics_psc() -> dict:
    return _load("naics_psc.yaml")


def portfolio() -> dict:
    return _load("portfolio.yaml")


def llm_model() -> str:
    return os.environ.get("SCOUT_LLM_MODEL", "gemini-3.1-flash-lite-preview")
