"""Shared helpers for Rust CLI black-box tests.

Tests drive the real CLI via subprocess so they exercise exactly what a user runs.
Resolution is environment/cwd sensitive, so each test controls cwd and a cleaned env.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUST_BINARY = REPO_ROOT / "target" / "debug" / ("relay.exe" if os.name == "nt" else "relay")
_RUST_TOOLCHAIN_ENV = {
    "RUSTUP_HOME": os.environ.get("RUSTUP_HOME", str(Path.home() / ".rustup")),
    "CARGO_HOME": os.environ.get("CARGO_HOME", str(Path.home() / ".cargo")),
}

# Env vars that used to influence root resolution; stripped so a "clean" run only
# resolves through the default Plugin installation root or an explicit flag.
_RESOLUTION_ENV = (
    "RELAY_ROOT",
    "CONVERSATE_ROOT",
    "BRAIN_CONV",
    "RELAY_USE_UVX_SEMBLE",
    "CONV_USE_UVX_SEMBLE",
)


def clean_env(*, home: Path | None = None, **overrides: object) -> dict[str, str]:
    env = dict(os.environ)
    for key in _RESOLUTION_ENV:
        env.pop(key, None)
    if home is not None:
        env["HOME"] = str(home)
        env["USERPROFILE"] = str(home)
        env.update(_RUST_TOOLCHAIN_ENV)
    for key, value in overrides.items():
        env[key] = str(value)
    return env

def run_cli(args, cwd, env=None, input=None) -> subprocess.CompletedProcess:
    if env is None:
        env = clean_env()
    if not RUST_BINARY.is_file():
        subprocess.run(["cargo", "build"], cwd=str(REPO_ROOT), check=True)
    return subprocess.run(
        [str(RUST_BINARY), *map(str, args)],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        input=input,
    )


def load_json(proc: subprocess.CompletedProcess) -> dict:
    return json.loads(proc.stdout)
