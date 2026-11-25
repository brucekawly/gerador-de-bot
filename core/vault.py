"""Senhas de servidores no cofre do sistema (Windows: Gerenciador de Credenciais; macOS: Keychain;
Linux: Secret Service). Nunca vão para o banco. Sem cofre disponível, a senha só vale na sessão."""
from __future__ import annotations

SERVICE = "Gerador de BOT"
_session: dict[str, str] = {}


def _key(server_id) -> str:
    return f"server:{server_id}"


def available() -> bool:
    try:
        import keyring
        from keyring.backends import fail
        return not isinstance(keyring.get_keyring(), fail.Keyring)
    except Exception:  # noqa: BLE001
        return False


def set_password(server_id, password: str) -> bool:
    """Grava no cofre. Devolve False se só ficou na memória (sem cofre)."""
    _session[_key(server_id)] = password or ""
    if not available():
        return False
    try:
        import keyring
        keyring.set_password(SERVICE, _key(server_id), password or "")
        return True
    except Exception:  # noqa: BLE001
        return False


def get_password(server_id) -> str:
    if _key(server_id) in _session:
        return _session[_key(server_id)]
    if not available():
        return ""
    try:
        import keyring
        return keyring.get_password(SERVICE, _key(server_id)) or ""
    except Exception:  # noqa: BLE001
        return ""


def delete_password(server_id) -> None:
    _session.pop(_key(server_id), None)
    if available():
        try:
            import keyring
            keyring.delete_password(SERVICE, _key(server_id))
        except Exception:  # noqa: BLE001
            pass
