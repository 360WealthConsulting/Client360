# shellcheck shell=bash
#
# pyenv.sh — resolve a Python 3 interpreter for the repo's shell scripts.
#
# Source this (do not execute it). On success it exports $PYTHON with a usable
# Python 3 command; on failure it prints a clear diagnostic and returns non-zero
# so the sourcing script aborts under `set -e`.
#
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/pyenv.sh"
#   "$PYTHON" -m pytest ...
#
# Resolution order (first Python 3 wins):
#   1. Active virtualenv        — $VIRTUAL_ENV/bin/python
#   2. Repo-local virtualenv    — <repo>/.venv/bin/python  (path derived from
#                                 this file's location, never hardcoded)
#   3. python3 on PATH
#   4. python  on PATH          — only if it is actually Python 3
#
# Portable across macOS and Linux; contains no user-specific filesystem paths.

# Idempotent: safe to source more than once in a single shell.
if [ -n "${_CLIENT360_PYENV_SOURCED:-}" ]; then
  return 0 2>/dev/null || true
fi
_CLIENT360_PYENV_SOURCED=1

# Absolute repo root, derived from this file: <repo>/scripts/lib/pyenv.sh
_client360_pyenv_repo_root() {
  ( cd "$(dirname "${BASH_SOURCE[0]}")/../.." >/dev/null 2>&1 && pwd )
}

# True only when $1 runs and reports itself as Python 3.x.
_client360_is_py3() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info[0] == 3 else 1)' \
    >/dev/null 2>&1
}

_client360_resolve_python() {
  local repo_root candidate
  repo_root="$(_client360_pyenv_repo_root)"

  # 1. Active virtualenv.
  if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "${VIRTUAL_ENV}/bin/python" ] \
     && _client360_is_py3 "${VIRTUAL_ENV}/bin/python"; then
    PYTHON="${VIRTUAL_ENV}/bin/python"
    return 0
  fi

  # 2. Repo-local virtualenv (relative to repo root — portable, not a user path).
  candidate="${repo_root}/.venv/bin/python"
  if [ -x "$candidate" ] && _client360_is_py3 "$candidate"; then
    PYTHON="$candidate"
    return 0
  fi

  # 3. python3 on PATH.
  if command -v python3 >/dev/null 2>&1 && _client360_is_py3 python3; then
    PYTHON="$(command -v python3)"
    return 0
  fi

  # 4. python on PATH, only if it is Python 3 (never a stray Python 2).
  if command -v python >/dev/null 2>&1 && _client360_is_py3 python; then
    PYTHON="$(command -v python)"
    return 0
  fi

  {
    echo "ERROR: no Python 3 interpreter found."
    echo "  Looked for, in order:"
    echo "    1. \$VIRTUAL_ENV/bin/python   (active virtualenv)"
    echo "    2. ${repo_root}/.venv/bin/python   (repo-local virtualenv)"
    echo "    3. python3 on PATH"
    echo "    4. python on PATH (Python 3 only)"
    echo "  Fix: create the project virtualenv"
    echo "    python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt"
    echo "  or install Python 3 and ensure python3 is on PATH."
  } >&2
  return 1
}

_client360_resolve_python || return 1 2>/dev/null || exit 1
export PYTHON
