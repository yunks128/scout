from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"


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
