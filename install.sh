#!/usr/bin/env bash
# decant installer (macOS / Linux).
# Creates .venv, installs Python deps, downloads the Chromium engine, makes ./decant runnable.
# Safe to re-run.
#
# Usage:  bash install.sh
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 1) locate python
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
[ -n "$PY" ] || { echo "Python 3.9+ not found. Install it and re-run."; exit 1; }
echo "[decant] python: $("$PY" --version)"

# 2) venv
if [ ! -x ".venv/bin/python" ]; then
  echo "[decant] creating .venv ..."
  "$PY" -m venv .venv
fi
VENVPY=".venv/bin/python"

# 3) dependencies
echo "[decant] installing dependencies ..."
"$VENVPY" -m pip install --disable-pip-version-check -q --upgrade pip
"$VENVPY" -m pip install --disable-pip-version-check -q -r requirements.txt

# 4) browser engine (default on macOS/Linux = bundled Chromium)
echo "[decant] installing Chromium engine (one-time ~150 MB download) ..."
"$VENVPY" -m playwright install chromium
# Linux: Chromium needs system libraries; best-effort (may prompt for sudo).
if [ "$(uname -s)" = "Linux" ]; then
  echo "[decant] installing Linux system libs for Chromium (may ask for sudo) ..."
  "$VENVPY" -m playwright install-deps chromium \
    || echo "[decant] WARN: could not auto-install system libs; see 'playwright install-deps'."
fi

# 5) make the wrapper executable
chmod +x ./decant 2>/dev/null || true

echo ""
echo "[decant] done. Next steps:"
echo "  ./decant login https://your-confluence.example.com   # one-time auth (2FA ok)"
echo "  ./decant get <URL> --out ./captures                  # rip one page"
echo "  ./decant serve                                       # daemon + bookmarklet"
