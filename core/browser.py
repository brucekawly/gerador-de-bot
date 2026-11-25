"""Abrir o navegador do Playwright (para ver a tela do equipamento) e montar o comando do gravador,
direto ou saindo pelo servidor do cliente (proxy SOCKS de core/remote.py)."""
from __future__ import annotations

import subprocess
import sys


def _base(cmd: str, browser: str = "chromium", proxy: str | None = None) -> list[str]:
    args = [sys.executable, "-m", "playwright", cmd, "-b", browser, "--ignore-https-errors"]
    if proxy:
        args += ["--proxy-server", proxy]
    return args


def open_url(url: str, proxy: str | None = None, browser: str = "chromium") -> subprocess.Popen:
    """Abre uma janela do navegador do app na URL (não bloqueia)."""
    return subprocess.Popen(_base("open", browser, proxy) + [url],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def codegen_cmd(output_file: str, browser: str = "chromium", proxy: str | None = None,
                start_url: str | None = None) -> list[str]:
    """Comando do gravador (Playwright codegen) gerando Python assíncrono em `output_file`."""
    cmd = _base("codegen", browser, proxy) + ["--target", "python-async", "-o", output_file]
    if start_url:
        cmd.append(start_url)
    return cmd
