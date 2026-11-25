"""Diferenças entre Windows, Linux e macOS concentradas num lugar só."""
import os
import subprocess
import sys

IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"


def no_window_flags() -> int:
    """creationflags para subprocess não abrir janela de console no Windows (0 nos outros sistemas)."""
    return 0x08000000 if IS_WINDOWS else 0


def open_path(path: str) -> None:
    """Abre arquivo/pasta com o programa padrão do sistema."""
    if IS_WINDOWS:
        os.startfile(path)  # noqa: S606 - só existe no Windows
    elif IS_MAC:
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def app_base_dir() -> str:
    """Raiz do projeto (onde ficam bot_templates.db e logs/)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
