"""Motor de execução dos templates (Playwright).

- Um único navegador por lote; cada equipamento roda num contexto isolado
  (cookies/sessão separados), o que é bem mais leve que abrir um navegador por IP.
- Retry com espera crescente para falhas transitórias (sem resposta, timeout).
- Várias credenciais: tenta cada uma em ordem até o login funcionar.
- Erros classificados (sem resposta / timeout / login / script) e screenshot da
  falha final salvo em logs/<data_hora>/.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from core.devices import PLACEHOLDER_RE, Credential, Device, normalize_var_name, resolve_variables

DEFAULT_CREDENTIAL = Credential("admin", "admin")


class LoginError(Exception):
    """O template pode levantar isto quando detectar que o login falhou:
    `raise LoginError("senha incorreta")`. O motor tenta a próxima credencial."""


class ErrorKind:
    UNREACHABLE = "sem_resposta"
    TIMEOUT = "timeout"
    AUTH = "login"
    PAGE = "pagina"
    SCRIPT = "script"
    CANCELLED = "cancelado"


ERROR_LABELS = {
    ErrorKind.UNREACHABLE: "Sem resposta",
    ErrorKind.TIMEOUT: "Tempo esgotado",
    ErrorKind.AUTH: "Login recusado",
    ErrorKind.PAGE: "Erro na página",
    ErrorKind.SCRIPT: "Erro no template",
    ErrorKind.CANCELLED: "Cancelado",
}

_UNREACHABLE_MARKERS = (
    "ERR_CONNECTION_REFUSED", "ERR_CONNECTION_TIMED_OUT", "ERR_ADDRESS_UNREACHABLE",
    "ERR_NAME_NOT_RESOLVED", "ERR_CONNECTION_RESET", "ERR_CONNECTION_CLOSED",
    "ERR_INTERNET_DISCONNECTED", "ERR_NETWORK_CHANGED", "ERR_EMPTY_RESPONSE",
    "NS_ERROR_CONNECTION_REFUSED", "NS_ERROR_NET_TIMEOUT", "NS_ERROR_UNKNOWN_HOST",
    "NS_ERROR_NET_RESET", "NS_ERROR_NET_INTERRUPT", "NS_ERROR_NET_EMPTY_RESPONSE",
    "Could not connect", "Connection refused", "Network is unreachable",
    "ERR_UNSAFE_PORT", "NS_ERROR_PORT_ACCESS_NOT_ALLOWED",   # porta bloqueada pelo navegador
)


def classify_error(exc: BaseException, cancelled: bool = False) -> ErrorKind | str:
    if cancelled or isinstance(exc, asyncio.CancelledError):
        return ErrorKind.CANCELLED
    if isinstance(exc, LoginError):
        return ErrorKind.AUTH
    if isinstance(exc, PlaywrightTimeoutError):
        return ErrorKind.TIMEOUT
    if isinstance(exc, PlaywrightError):
        msg = str(exc)
        if any(m.lower() in msg.lower() for m in _UNREACHABLE_MARKERS):
            return ErrorKind.UNREACHABLE
        if "Timeout" in msg and "exceeded" in msg:
            return ErrorKind.TIMEOUT
        return ErrorKind.PAGE
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return ErrorKind.TIMEOUT
    if isinstance(exc, (ConnectionError, OSError)):
        return ErrorKind.UNREACHABLE
    # SyntaxError, NameError, TypeError... = problema no próprio template
    return ErrorKind.SCRIPT


_FRIENDLY = [  # (trecho da mensagem técnica, explicação para o técnico)
    (("ERR_CONNECTION_REFUSED", "NS_ERROR_CONNECTION_REFUSED", "Connection refused"),
     "Conexão recusada: a porta está fechada ou o acesso web está desativado"),
    (("ERR_CONNECTION_TIMED_OUT", "NS_ERROR_NET_TIMEOUT", "ERR_ADDRESS_UNREACHABLE", "Network is unreachable"),
     "Não respondeu: equipamento desligado, IP errado ou bloqueado por firewall"),
    (("ERR_NAME_NOT_RESOLVED", "NS_ERROR_UNKNOWN_HOST"), "Endereço não encontrado (verifique o IP/host)"),
    (("ERR_UNSAFE_PORT", "NS_ERROR_PORT_ACCESS_NOT_ALLOWED"),
     "Porta bloqueada pelo navegador por segurança: use outra porta"),
    (("ERR_CONNECTION_RESET", "NS_ERROR_NET_RESET", "ERR_EMPTY_RESPONSE", "NS_ERROR_NET_EMPTY_RESPONSE"),
     "O equipamento fechou a conexão (pode estar reiniciando ou sobrecarregado)"),
    (("ERR_SSL", "SSL_ERROR", "SEC_ERROR"), "Erro de HTTPS: confira se o protocolo (http/https) está certo"),
]


def short_error(exc: BaseException) -> str:
    """Mensagem curta e compreensível (o Playwright manda um 'call log' enorme)."""
    text = str(exc).strip() or exc.__class__.__name__
    first = text.splitlines()[0]
    url = re.search(r"\bat (https?://\S+)", first)
    for markers, msg in _FRIENDLY:
        if any(m.lower() in text.lower() for m in markers):
            return msg + (f" ({url.group(1)})" if url else "")
    m = re.search(r"Timeout \d+ms exceeded", first)
    if m:
        loc = re.search(r"waiting for (?:locator\()?['\"]?(.+?)['\"]?\)?(?:\s+to be|\n|$)", text)
        nav = "navigat" in text.lower() or "page.goto" in first.lower()
        if nav:
            return "A página não terminou de carregar no tempo limite" + (f" ({url.group(1)})" if url else "")
        if loc:
            return f"O elemento {loc.group(1)[:80]} não apareceu no tempo limite (senha errada ou tela diferente?)"
        return "Tempo esgotado esperando a página"
    return first[:300]


@dataclass
class RetryPolicy:
    max_attempts: int = 3          # tentativas por credencial em falha transitória
    backoff_s: float = 2.0         # espera antes da 2ª tentativa (dobra a cada nova)
    max_backoff_s: float = 20.0

    def delay(self, attempt: int) -> float:
        return min(self.backoff_s * (2 ** (attempt - 1)), self.max_backoff_s)


@dataclass
class DeviceResult:
    index: int
    ip: str
    port: str
    status: str                    # success | error | cancelled
    message: str = ""
    error_kind: str | None = None
    attempts: int = 0
    credential: str | None = None  # usuário que funcionou (sem senha)
    screenshot: str | None = None
    duration_s: float = 0.0

    def as_dict(self) -> dict:
        return dict(self.__dict__)


ProgressCallback = Callable[[int, str, str], None]  # (indice, status_curto, detalhe)


class TemplateVars:
    """Valores das variáveis lidos em TEMPO DE EXECUÇÃO pelo template (`_v.SSID`).

    Variável desconhecida vale "" (mesmo comportamento de antes).
    """
    def __init__(self, values: dict[str, str]):
        self.__dict__.update({k: str(v) for k, v in values.items()})

    def __getattr__(self, name):
        return ""


def _marker(name: str) -> str:
    return f"__GBV_{name}__"


_MARKER_RE = re.compile(r"__GBV_([A-Z0-9_]+?)__")


def prepare_template(template_script: str) -> str:
    """Converte os {{VAR}} do template em leitura em tempo de execução, sem colar valores no código.

    Segurança: o valor de uma variável (que pode vir da planilha de um cliente) NUNCA vira código
    Python. Dentro de uma string, ela vira uma f-string que lê `_v.NOME`; fora de string,
    vira a expressão `_v.NOME` (sempre texto).
    """
    import io
    import tokenize

    src = PLACEHOLDER_RE.sub(lambda m: _marker(normalize_var_name(m.group(1))), template_script or "")
    if "__GBV_" not in src:
        return src
    lines = src.splitlines(keepends=True)
    offsets = [0]
    for ln in lines:
        offsets.append(offsets[-1] + len(ln))

    def pos(rc):
        return offsets[rc[0] - 1] + rc[1]

    edits: list[tuple[int, int, str]] = []
    fstring_start = None
    tokens = list(tokenize.generate_tokens(io.StringIO(src).readline))
    for tok in tokens:
        tname = tokenize.tok_name[tok.type]
        if tname == "FSTRING_START":           # Python 3.12+: f-string vem em vários tokens
            fstring_start = pos(tok.start)
            continue
        if tname == "FSTRING_END" and fstring_start is not None:
            a, b = fstring_start, pos(tok.end)
            text = src[a:b]
            if "__GBV_" in text:
                edits.append((a, b, _MARKER_RE.sub(r"{_v.\1}", text)))
            fstring_start = None
            continue
        if fstring_start is not None:
            continue
        if tok.type == tokenize.STRING and "__GBV_" in tok.string:
            prefix = re.match(r"[A-Za-z]*", tok.string).group(0)
            body = tok.string[len(prefix):]
            low = prefix.lower()
            if "b" in low:                    # bytes: não suportado, deixa vazio
                new = _MARKER_RE.sub("", tok.string)
            elif "f" in low:                  # f-string (Python <= 3.11 = 1 token só)
                new = _MARKER_RE.sub(r"{_v.\1}", tok.string)
            else:                             # string normal -> f-string com chaves escapadas
                body = body.replace("{", "{{").replace("}", "}}")
                new = "f" + prefix + _MARKER_RE.sub(r"{_v.\1}", body)
            edits.append((pos(tok.start), pos(tok.end), new))
        elif tok.type == tokenize.NAME and _MARKER_RE.fullmatch(tok.string):
            edits.append((pos(tok.start), pos(tok.end), _MARKER_RE.sub(r"_v.\1", tok.string)))
    for a, b, new in sorted(edits, reverse=True):
        src = src[:a] + new + src[b:]
    return src


def render_script(template_script: str, variables: dict[str, str]) -> str:
    """Compatibilidade: só prepara o template (os valores agora entram em tempo de execução)."""
    return prepare_template(template_script)


def compile_template(prepared_script: str, variables: dict[str, str] | None = None):
    """Transforma o corpo do template na coroutine `run_automation(page, context, browser, variables)`."""
    body = "\n".join(f"    {line}" for line in prepared_script.splitlines()) or "    pass"
    source = f"async def run_automation(page, context, browser, variables):\n{body}\n"
    env = {"asyncio": asyncio, "LoginError": LoginError, "re": re, "_v": TemplateVars(variables or {})}
    code = compile(source, "<template>", "exec")
    exec(code, env)
    return env["run_automation"]


def validate_template(template_script: str) -> str | None:
    """Retorna a mensagem de erro de sintaxe, ou None se o template compila."""
    try:
        compile_template(prepare_template(template_script))
        return None
    except SyntaxError as e:
        return f"Linha {max((e.lineno or 1) - 1, 1)}: {e.msg}"
    except Exception as e:  # noqa: BLE001 - tokenize.TokenError (string/parêntese sem fechar)
        return f"Erro de sintaxe: {e}"


def default_logs_dir() -> str:
    """Pasta de logs: $GERADOR_BOT_LOGS ou <projeto>/logs."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.environ.get("GERADOR_BOT_LOGS") or os.path.join(base, "logs")


class AutomationEngine:
    def __init__(self, max_concurrent: int = 5, retry: RetryPolicy | None = None,
                 logs_dir: str | None = None, screenshots: bool = True):
        self.max_concurrent = max(1, int(max_concurrent))
        self.retry = retry or RetryPolicy()
        self.logs_dir = logs_dir or default_logs_dir()
        self.screenshots = screenshots
        self.is_cancelled = False
        self.browser_launches = 0        # usado nos testes / diagnóstico
        self.run_dir: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._active_contexts: set = set()
        self._cancel_event: asyncio.Event | None = None
        self.defaults: dict[str, str] = {}

    def _port(self, dev: Device) -> str:
        """Porta efetiva (planilha > padrão do template > 80/443)."""
        return resolve_variables(dev, None, self.defaults)["PORT"]

    # ---------- cancelamento (pode ser chamado de outra thread) ----------
    def cancel(self):
        self.is_cancelled = True
        loop = self._loop
        if loop and not loop.is_closed():
            loop.call_soon_threadsafe(self._abort_active)

    def _abort_active(self):
        if self._cancel_event:
            self._cancel_event.set()
        for ctx in list(self._active_contexts):
            asyncio.ensure_future(self._safe_close(ctx))

    @staticmethod
    async def _safe_close(obj):
        try:
            await obj.close()
        except Exception:
            pass

    async def _sleep_or_cancel(self, seconds: float):
        if seconds <= 0:
            return
        try:
            await asyncio.wait_for(self._cancel_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    # ---------- API principal ----------
    async def run_batch(self, devices: list[Device], script: str,
                        credentials: list[Credential] | None = None,
                        browser_type: str = "firefox", timeout_ms: int = 15000,
                        progress_callback: ProgressCallback | None = None,
                        visible: bool = False,
                        defaults: dict[str, str] | None = None,
                        proxy: str | None = None) -> list[DeviceResult]:
        """proxy: ex. 'socks5://127.0.0.1:1080' (acesso pelo servidor do cliente, ver core/remote.py)."""
        # (is_cancelled NÃO é zerado aqui: um "Parar" clicado antes do lote começar continua valendo.
        #  Use um AutomationEngine novo para cada lote.)
        self.defaults = dict(defaults or {})
        self._loop = asyncio.get_running_loop()
        self._cancel_event = asyncio.Event()
        self.run_dir = os.path.join(self.logs_dir, datetime.now().strftime("%Y-%m-%d_%H-%M-%S"))
        semaphore = asyncio.Semaphore(self.max_concurrent)
        creds = [c for c in (credentials or []) if c.username or c.password] or [DEFAULT_CREDENTIAL]
        notify = progress_callback or (lambda *_: None)

        # Valida sintaxe uma vez antes de abrir o navegador
        error = validate_template(script)
        if error:
            msg = f"Template com erro de sintaxe ({error})"
            results = []
            for i, d in enumerate(devices):
                notify(i, "Erro no template", msg)
                results.append(DeviceResult(i, d.ip, self._port(d), "error", msg, ErrorKind.SCRIPT))
            return results

        async with async_playwright() as p:
            launcher = getattr(p, (browser_type or "firefox").lower(), p.firefox)
            browser_lock = asyncio.Lock()
            state = {"browser": None}

            async def get_browser():
                async with browser_lock:
                    b = state["browser"]
                    if b is None or not b.is_connected():
                        opts = {"headless": not visible}
                        if proxy:
                            opts["proxy"] = {"server": proxy}
                        b = await launcher.launch(**opts)
                        self.browser_launches += 1
                        state["browser"] = b
                    return b

            async def worker(i: int, dev: Device):
                if self.is_cancelled:
                    notify(i, "Cancelado", "")
                    return DeviceResult(i, dev.ip, self._port(dev), "cancelled", "Cancelado", ErrorKind.CANCELLED)
                async with semaphore:
                    return await self._run_device(i, dev, script, creds, get_browser, timeout_ms, notify)

            try:
                results = await asyncio.gather(*(worker(i, d) for i, d in enumerate(devices)))
            finally:
                if state["browser"]:
                    await self._safe_close(state["browser"])
        return list(results)

    async def _run_device(self, index, dev: Device, script, batch_creds, get_browser,
                          timeout_ms, notify) -> DeviceResult:
        start = time.monotonic()
        creds = [dev.credential] if dev.credential else list(batch_creds)
        attempts = 0
        last_exc: BaseException | None = None
        last_kind = None
        last_shot = None

        for ci, cred in enumerate(creds):
            has_next_cred = ci < len(creds) - 1
            for attempt in range(1, self.retry.max_attempts + 1):
                if self.is_cancelled:
                    last_kind = ErrorKind.CANCELLED
                    break
                attempts += 1
                label = f"Tentativa {attempt}" + (f" · usuário {cred.username}" if len(creds) > 1 else "")
                notify(index, "Executando", label)
                exc, shot = await self._attempt(index, dev, script, cred, get_browser, timeout_ms,
                                                attempts, notify)
                if exc is None:
                    dur = time.monotonic() - start
                    notify(index, "Sucesso", f"OK em {dur:.0f}s" + (f" ({cred.username})" if len(creds) > 1 else ""))
                    return DeviceResult(index, dev.ip, self._port(dev), "success", "Configuração aplicada",
                                        None, attempts, cred.username, None, dur)
                last_exc, last_shot = exc, shot or last_shot
                last_kind = classify_error(exc, self.is_cancelled)

                if last_kind in (ErrorKind.SCRIPT, ErrorKind.CANCELLED):
                    break                       # não adianta repetir
                if last_kind in (ErrorKind.AUTH, ErrorKind.TIMEOUT) and has_next_cred:
                    break                       # senha errada costuma virar timeout: tenta a próxima
                if last_kind == ErrorKind.AUTH:
                    break
                if attempt < self.retry.max_attempts:
                    wait = self.retry.delay(attempt)
                    notify(index, "Aguardando", f"{ERROR_LABELS[last_kind]} · nova tentativa em {wait:.0f}s")
                    await self._sleep_or_cancel(wait)
            if last_kind in (ErrorKind.SCRIPT, ErrorKind.CANCELLED, ErrorKind.UNREACHABLE):
                break                           # trocar senha não resolve equipamento fora do ar

        dur = time.monotonic() - start
        if last_kind == ErrorKind.CANCELLED:
            notify(index, "Cancelado", "")
            return DeviceResult(index, dev.ip, self._port(dev), "cancelled", "Cancelado", last_kind,
                                attempts, None, last_shot, dur)
        detail = short_error(last_exc) if last_exc else ""
        if last_kind == ErrorKind.AUTH and len(creds) > 1:
            detail = f"Nenhuma das {len(creds)} credenciais funcionou. {detail}"
        notify(index, ERROR_LABELS.get(last_kind, "Erro"), detail)
        return DeviceResult(index, dev.ip, self._port(dev), "error", detail, last_kind, attempts,
                            None, last_shot, dur)

    async def _attempt(self, index, dev: Device, script, cred: Credential, get_browser,
                       timeout_ms, attempt_no, notify):
        """Uma execução do template. Retorna (exceção ou None, caminho do screenshot)."""
        context = None
        try:
            browser = await get_browser()
            context = await browser.new_context(
                ignore_https_errors=True,
                http_credentials={"username": cred.username, "password": cred.password},
            )
            self._active_contexts.add(context)
            if self.is_cancelled:   # cancelou enquanto o navegador abria
                raise asyncio.CancelledError()
            context.set_default_timeout(timeout_ms)
            context.set_default_navigation_timeout(timeout_ms)
            page = await context.new_page()

            # status da última navegação da página principal (401 = login HTTP recusado)
            last_nav = {"status": None}

            def on_response(resp):
                try:
                    if resp.request.is_navigation_request() and resp.frame == page.main_frame:
                        last_nav["status"] = resp.status
                except Exception:
                    pass
            page.on("response", on_response)

            variables = resolve_variables(dev, cred, self.defaults)
            run = compile_template(prepare_template(script), variables)
            try:
                await run(page, context, browser, dict(variables))
            except Exception as e:
                if last_nav["status"] == 401 and not isinstance(e, LoginError):
                    raise LoginError("Roteador respondeu 401 (usuário/senha)") from e
                raise
            if last_nav["status"] == 401:
                raise LoginError("Roteador respondeu 401 (usuário/senha)")
            return None, None
        except BaseException as e:  # noqa: BLE001 - classificado pelo chamador
            if isinstance(e, asyncio.CancelledError) and not self.is_cancelled:
                raise
            shot = None
            if (self.screenshots and context is not None and not self.is_cancelled
                    and attempt_no >= 1 and classify_error(e) != ErrorKind.UNREACHABLE):
                shot = await self._screenshot(context, dev, attempt_no)
            return e, shot
        finally:
            if context is not None:
                self._active_contexts.discard(context)
                await self._safe_close(context)

    async def _screenshot(self, context, dev: Device, attempt_no: int) -> str | None:
        try:
            pages = [p for p in context.pages if not p.is_closed()]
            if not pages:
                return None
            os.makedirs(self.run_dir, exist_ok=True)
            safe_ip = re.sub(r"[^0-9A-Za-z._-]", "_", dev.ip)
            path = os.path.join(self.run_dir, f"{safe_ip}_{self._port(dev)}_t{attempt_no}.png")
            await pages[-1].screenshot(path=path, timeout=5000)
            return path
        except Exception:
            return None

    # ---------- compatibilidade: teste de um IP com navegador visível ----------
    async def test_single(self, device: Device, script: str,
                          credentials: list[Credential] | None = None,
                          browser_type: str = "firefox", timeout_ms: int = 15000,
                          progress_callback: ProgressCallback | None = None,
                          defaults: dict[str, str] | None = None, proxy: str | None = None) -> DeviceResult:
        results = await self.run_batch([device], script, credentials, browser_type,
                                       timeout_ms, progress_callback, visible=True, defaults=defaults, proxy=proxy)
        return results[0]
