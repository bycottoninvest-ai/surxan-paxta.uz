#!/bin/bash
# SURXON PAXTA — test mode on Mac / Linux (double-click on Mac, or: bash start_test.command)
cd "$(dirname "$0")"
command -v python3 >/dev/null || { echo "Python 3 o'rnatilmagan: https://www.python.org/downloads/"; exit 1; }
if [ ! -d .venv ]; then
  echo "Birinchi ishga tushirish: kutubxonalar o'rnatilmoqda..."
  python3 -m venv .venv && .venv/bin/pip install --quiet --upgrade pip && .venv/bin/pip install --quiet -r requirements.txt || exit 1
fi
exec .venv/bin/python tools/run_local.py "$@"
