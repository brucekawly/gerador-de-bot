#!/usr/bin/env bash
# Instalação no Linux/macOS: cria o venv, instala as dependências e os navegadores do Playwright.
# Uso:  ./install.sh            (interface gráfica + headless)
#       ./install.sh --headless (só o motor/CLI, sem tkinter — para servidor/contêiner)
set -euo pipefail
cd "$(dirname "$0")"

HEADLESS=0
[ "${1:-}" = "--headless" ] && HEADLESS=1

PY=${PYTHON:-python3}
command -v "$PY" >/dev/null || { echo "python3 não encontrado"; exit 1; }

if [ "$HEADLESS" = 0 ] && ! "$PY" -c "import tkinter" 2>/dev/null; then
  echo "Aviso: tkinter não está instalado (necessário para a interface gráfica)."
  echo "  Debian/Ubuntu: sudo apt install python3-tk"
  echo "  Fedora:        sudo dnf install python3-tkinter"
  echo "  Ou rode com --headless para instalar só o motor."
fi

echo "==> Criando ambiente virtual em .venv"
"$PY" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "==> Instalando navegadores do Playwright"
if [ "$HEADLESS" = 1 ]; then
  # Chromium já basta para o modo headless; --with-deps baixa as libs do sistema
  python -m playwright install --with-deps chromium
else
  python -m playwright install
  python -m playwright install-deps || echo "  (não foi possível instalar libs do sistema; rode com sudo se precisar)"
fi

echo
echo "Pronto. Para usar:"
echo "  source .venv/bin/activate"
[ "$HEADLESS" = 0 ] && echo "  python main.py                 # interface gráfica"
echo "  python -m core scan 192.168.1.1 192.168.1.254   # scanner (CLI)"
echo "  python -m core run --template-id 1 --devices lista.csv --json saida.json"
