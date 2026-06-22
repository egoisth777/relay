"""Shared helpers for conv_cli black-box tests.

Tests drive the real CLI via subprocess so they exercise exactly what a user runs.
Resolution is environment/cwd sensitive, so each test controls cwd and a cleaned env.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "conv_cli.py"

# Env vars that influence store-root resolution; stripped so a "clean" run only
# resolves via flag/marker, never an ambient store.
_RESOLUTION_ENV = ("BRAIN_CONV", "CONV_USE_UVX_SEMBLE")


def clean_env(**overrides: object) -> dict[str, str]:
    env = dict(os.environ)
    for key in _RESOLUTION_ENV:
        env.pop(key, None)
    for key, value in overrides.items():
        env[key] = str(value)
    return env


def run_cli(args, cwd, env=None) -> subprocess.CompletedProcess:
    if env is None:
        env = clean_env()
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
    )


def load_json(proc: subprocess.CompletedProcess) -> dict:
    return json.loads(proc.stdout)
