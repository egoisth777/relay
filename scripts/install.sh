#!/usr/bin/env bash
#
# Platform installer shim for the Relay plugin (POSIX: Linux / macOS / WSL).
#
# install.py is the Python bootstrap/configuration layer. This wrapper locates
# a usable Python 3.10+ interpreter and forwards every argument verbatim.

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
install_py="$script_dir/install.py"

if [[ ! -f "$install_py" ]]; then
    echo "install.sh: cannot find sibling install.py at '$install_py'." >&2
    exit 2
fi

verify='import sys; print("%d.%d.%d" % sys.version_info[:3]); sys.exit(0 if sys.version_info[:2]>=(3,10) else 1)'

probe_py() {
    local cmd="$1"
    command -v "$cmd" >/dev/null 2>&1 || return 1
    "$cmd" -c "$verify" 2>/dev/null
}

candidates=()
if [[ -n "${RELAY_PYTHON:-}" ]]; then
    candidates+=("$RELAY_PYTHON")
elif [[ -n "${CONVERSATE_PYTHON:-}" ]]; then
    # Legacy compatibility only; new configuration should use RELAY_PYTHON.
    candidates+=("$CONVERSATE_PYTHON")
fi
candidates+=(python3.10 python3.11 python3.12 python3.13 python3 python)

resolved=""
pyver=""
for cand in "${candidates[@]}"; do
    if pyver="$(probe_py "$cand" 2>/dev/null)" && [[ -n "$pyver" ]]; then
        resolved="$cand"
        break
    fi
done

if [[ -z "$resolved" ]]; then
    echo "install.sh: no usable Python 3.10+ found." >&2
    echo "  - Set RELAY_PYTHON to a Python path, or install Python 3.10+." >&2
    exit 127
fi

echo "install.sh: using Python ${pyver} (${resolved})" >&2
exec "$resolved" "$install_py" "$@"
