#!/usr/bin/env bash
# Gerador de BOT - macOS / Linux: instala na primeira vez e abre o app.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
  echo "== Primeira execução: instalando =="
  ./install.sh
fi

exec .venv/bin/python main.py "$@"
