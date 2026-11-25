"""Leitura da lista de equipamentos e das variáveis de cada um.

Cada linha da planilha vira um Device. As colunas reconhecidas são:
  - IP (obrigatória) e Porta (vazia = porta padrão do template)
  - Protocolo/SCHEME (http ou https, opcional)
  - Usuário / Senha (opcionais): credencial só daquele equipamento
  - Qualquer outra coluna vira variável {{NOME_DA_COLUNA}} no template
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")

# Variáveis que o motor sempre fornece
BUILTIN_VARS = {"IP", "PORT", "SCHEME", "BASE_URL", "USERNAME", "PASSWORD"}
HTTPS_PORTS = {"443", "8443"}

_IP_COLS = {"IP", "HOST", "ENDERECO", "ENDERECO_IP", "IP_HOST"}
_PORT_COLS = {"PORTA", "PORT", "PORTA_WEB", "PORTA_HTTP"}
_SCHEME_COLS = {"PROTOCOLO", "SCHEME"}
_USER_COLS = {"USUARIO", "USER", "USERNAME", "LOGIN"}
_PASS_COLS = {"SENHA", "PASSWORD", "PASS", "SENHA_WEB"}


@dataclass(frozen=True)
class Credential:
    username: str
    password: str

    def __str__(self) -> str:  # nunca mostra a senha em log
        return self.username


@dataclass
class Device:
    ip: str
    port: str = ""          # vazio = usa a porta padrão do template
    credential: Credential | None = None
    variables: dict[str, str] = field(default_factory=dict)


def normalize_var_name(name: str) -> str:
    """'Senha Wi-Fi 5G' -> 'SENHA_WI_FI_5G'"""
    text = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").upper()
    if text and text[0].isdigit():
        text = "_" + text
    return text


def _clean(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in ("nan", "none", "nat"):
        return ""
    # Excel lê "80" como 80.0
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    return text


def parse_rows(columns: list[str], rows: list[list]) -> tuple[list[Device], list[str]]:
    """Converte linhas de planilha em Devices.

    Retorna (devices, avisos). Linhas sem IP são ignoradas e contadas no aviso.
    """
    names = [normalize_var_name(c) for c in columns]
    warnings: list[str] = []

    def find(candidates: set[str]) -> int | None:
        for i, n in enumerate(names):
            if n in candidates:
                return i
        return None

    ip_i = find(_IP_COLS)
    if ip_i is None:
        raise ValueError("A planilha precisa de uma coluna 'IP'.")
    port_i, user_i, pass_i = find(_PORT_COLS), find(_USER_COLS), find(_PASS_COLS)
    scheme_i = find(_SCHEME_COLS)
    special = {i for i in (ip_i, port_i, user_i, pass_i, scheme_i) if i is not None}

    devices: list[Device] = []
    skipped = 0
    for row in rows:
        values = [_clean(v) for v in row] + [""] * (len(names) - len(row))
        ip = values[ip_i]
        if not ip:
            skipped += 1
            continue
        port = values[port_i] if port_i is not None else ""
        cred = None
        if user_i is not None and values[user_i]:
            cred = Credential(values[user_i], values[pass_i] if pass_i is not None else "")
        variables = {names[i]: values[i] for i in range(len(names)) if i not in special and names[i]}
        if scheme_i is not None and values[scheme_i]:
            variables["SCHEME"] = values[scheme_i].lower().rstrip(":/")
        devices.append(Device(ip=ip, port=port, credential=cred, variables=variables))

    if skipped:
        warnings.append(f"{skipped} linha(s) sem IP foram ignoradas.")
    return devices, warnings


def parse_dataframe(df) -> tuple[list[Device], list[str]]:
    df = df.astype(object).where(df.notna(), None)
    return parse_rows([str(c) for c in df.columns], df.values.tolist())


def parse_credentials(text: str) -> list[Credential]:
    """Uma credencial por linha: 'usuario:senha' (ou ';' / tab). Linhas com # são ignoradas."""
    creds: list[Credential] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([^:;\t]*)[:;\t](.*)$", line)
        user, pwd = (m.group(1), m.group(2)) if m else (line, "")
        creds.append(Credential(user.strip(), pwd.strip()))
    return creds


def format_credentials(creds: list[Credential]) -> str:
    return "\n".join(f"{c.username}:{c.password}" for c in creds)


def _strip_comments(script: str) -> str:
    """Remove comentários Python (# ...) sem mexer em '#' dentro de strings."""
    import io
    import tokenize
    try:
        out_lines = (script or "").splitlines()
        for tok in tokenize.generate_tokens(io.StringIO(script or "").readline):
            if tok.type == tokenize.COMMENT:
                row, col = tok.start
                out_lines[row - 1] = out_lines[row - 1][:col]
        return "\n".join(out_lines)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # template incompleto (ainda sendo digitado): ignora linhas que começam com #
        return "\n".join(ln for ln in (script or "").splitlines() if not ln.lstrip().startswith("#"))


def template_variables(script: str) -> set[str]:
    """Variáveis {{X}} usadas no template (comentários não contam)."""
    return {normalize_var_name(m) for m in PLACEHOLDER_RE.findall(_strip_comments(script))}


def parse_defaults(text: str) -> dict[str, str]:
    """Valores padrão do template: um 'NOME=valor' por linha (ex.: PORT=8080, SCHEME=https)."""
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if normalize_var_name(k):
            out[normalize_var_name(k)] = v.strip()
    return out


def resolve_variables(device: Device, credential: Credential | None = None,
                      defaults: dict[str, str] | None = None) -> dict[str, str]:
    """Junta padrões do template + dados do equipamento + variáveis do motor.

    Prioridade: planilha/scanner > padrão do template > automático.
    PORT vazio -> PORT do template -> 443 se SCHEME=https, senão 80.
    SCHEME vazio -> SCHEME do template -> https se a porta for 443/8443, senão http.
    """
    defaults = defaults or {}
    v = dict(defaults)
    v.update({k: x for k, x in device.variables.items() if x != ""})
    scheme = (device.variables.get("SCHEME") or "").lower()
    port = str(device.port or "").strip() or defaults.get("PORT", "")
    if not scheme:
        scheme = defaults.get("SCHEME", "").lower() or ("https" if port in HTTPS_PORTS else "http")
    if not port:
        port = "443" if scheme == "https" else "80"
    v.update(IP=device.ip, PORT=port, SCHEME=scheme, BASE_URL=f"{scheme}://{device.ip}:{port}")
    if credential is not None:
        v.update(USERNAME=credential.username, PASSWORD=credential.password)
    return v


def missing_variables(script: str, devices: list[Device],
                      defaults: dict[str, str] | None = None) -> dict[str, int]:
    """Variáveis usadas no template que faltam (ou estão vazias) em algum equipamento.

    Valores padrão do template contam como preenchidos.
    Retorna {variavel: quantidade_de_equipamentos_sem_ela}.
    """
    defaults = defaults or {}
    needed = template_variables(script) - BUILTIN_VARS - {k for k, v in defaults.items() if v}
    missing: dict[str, int] = {}
    for var in sorted(needed):
        count = sum(1 for d in devices if not d.variables.get(var))
        if count:
            missing[var] = count
    return missing
