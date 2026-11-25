# Integração do motor no OLT Cloud

Guia para embutir o Gerador de BOT no OLT Cloud e oferecê-lo aos clientes.
Escrito para quem vai integrar (back-end); o uso no app desktop está no [README](../README.md).

> **Resumo:** reaproveite só a pasta `core/`. Ela não depende da interface gráfica e roda em
> Linux/macOS/Windows, como biblioteca Python ou pela linha de comando com saída JSON.
> Antes de abrir para clientes, decida o modelo de segurança dos templates (seção 4): hoje eles
> são **código Python executado no servidor**.

---

## 1. O que reaproveitar

| Arquivo | Papel | Depende de |
|---|---|---|
| `core/automation_engine.py` | Executa um template num lote: 1 navegador, contexto por equipamento, retry, credenciais, classificação de erro, screenshot | `playwright` |
| `core/devices.py` | Modelos (`Device`, `Credential`), leitura de planilha, variáveis `{{X}}`, valores padrão | `pandas` só para planilha |
| `core/fingerprint.py` | Scanner: identifica fabricante/modelo/firmware sem login (HTTP, UPnP, SNMP, sondas) | só stdlib |
| `core/cli.py` | `python -m core run/scan/templates` com saída JSON | os acima |
| `database/db_handler.py` | SQLite do app desktop. **No OLT Cloud, substitua pelos models do Django** | — |
| `gui/` | Interface desktop. **Não usar no servidor** | `customtkinter` |

Instalação no servidor (sem interface):

```bash
pip install "playwright>=1.44" "pandas>=2.2" openpyxl
python -m playwright install --with-deps chromium
```

---

## 2. Usando como biblioteca

```python
import asyncio
from core.automation_engine import AutomationEngine, RetryPolicy
from core.devices import Credential, Device, parse_credentials, parse_defaults

template = TemplateModel.objects.get(pk=3)          # seu model
devices = [
    Device(ip="10.20.0.15", variables={"SSID": "Loja Centro", "SENHA_WIFI": "12345678"}),
    Device(ip="10.20.0.16", port="8080", variables={"SSID": "Casa Silva", "SENHA_WIFI": "abcdefgh"}),
]

def on_progress(index, status, detail):
    # status: "Executando" | "Aguardando" | "Sucesso" | "Sem resposta" | "Tempo esgotado" |
    #         "Login recusado" | "Erro na página" | "Erro no template" | "Cancelado"
    job.update_device(index, status, detail)      # ex.: salvar e enviar por websocket

engine = AutomationEngine(max_concurrent=10, retry=RetryPolicy(max_attempts=3),
                          logs_dir=f"/var/lib/oltcloud/bot/{job.id}")
results = asyncio.run(engine.run_batch(
    devices,
    template.script,
    credentials=parse_credentials(template.credentials),   # "usuario:senha" por linha
    defaults=parse_defaults(template.defaults),            # "PORT=8080\nSCHEME=https"
    browser_type="chromium",
    timeout_ms=30_000,
    progress_callback=on_progress,
))
for r in results:            # DeviceResult (mesma ordem de `devices`)
    r.as_dict()              # {"index","ip","port","status","message","error_kind","attempts",
                             #  "credential","screenshot","duration_s"}
```

- `engine.cancel()` pode ser chamado **de outra thread** (botão "Parar" da tela): fecha os contextos ativos e marca o resto como cancelado.
- `progress_callback` é chamado **na thread do asyncio**. Não bloqueie dentro dele: enfileire.
- `error_kind`: `sem_resposta`, `timeout`, `login`, `pagina`, `script`, `cancelado`. `message` já vem em português para o técnico.
- `credential` informa **qual usuário** funcionou (nunca a senha).

### Scanner

```python
from core import fingerprint as fp
hosts = fp.scan(fp.expand_range("10.20.0.1", "10.20.0.254"), [80, 443, 8080], snmp_community="public")
for h in hosts:
    h.ip, h.port, h.scheme, h.vendor, h.model, h.firmware, h.mac, h.sources
template, confianca = fp.match_template(h, [(t.id, t.vendor, t.model, t.firmware, t.hardware) for t in ts])
```

Para ensinar um fabricante novo ao scanner, adicione uma regra em `VENDOR_RULES` ou uma sonda em `VENDOR_PROBES` (ver o exemplo da Fiberhome em `core/fingerprint.py`).

---

## 3. Usando pela linha de comando (worker isolado)

Útil para rodar cada job num processo ou contêiner separado:

```bash
python -m core run --template-file job/template.py --devices job/equipamentos.json \
  --credentials job/creds.txt --set PORT=8080 --workers 10 --attempts 3 \
  --logs job/logs --json job/resultado.json -q
```

`equipamentos.json` = lista de objetos com as mesmas colunas da planilha:
`[{"IP": "10.20.0.15", "Porta": "", "SSID": "Loja"}, ...]`

Saída (`resultado.json`):

```json
{"total": 2, "success": 1, "failed": 1, "logs_dir": "job/logs/2026-11-25_19-10-02",
 "results": [{"index": 0, "ip": "10.20.0.15", "port": "80", "status": "success", "error_kind": null,
              "attempts": 1, "credential": "admin", "screenshot": null, "duration_s": 2.1, "message": "..."}]}
```

Códigos de saída: `0` tudo OK · `1` algum equipamento falhou · `2` erro de uso/arquivo/template.
O progresso sai em `stderr`, uma linha por evento (sem `-q`).

---

## 4. Segurança: decisão necessária antes de abrir para clientes

**Hoje um template é um trecho de Python que o motor executa com `exec()`.** No desktop isso é aceitável (o técnico roda o próprio código). Num serviço multi-cliente, um template malicioso ou errado pode ler arquivos, acessar a rede interna e pegar segredos do servidor.

Opções, da mais rápida para a mais robusta:

| Opção | Como | Prós | Contras |
|---|---|---|---|
| **A. Só a equipe OLT Cloud cria templates** | Cliente apenas escolhe um template homologado e envia a planilha | Rápido de lançar; mesmo modelo dos templates TR-069 homologados | Cliente não grava os próprios bots |
| **B. Sandbox por job** | Cada execução num contêiner descartável: sem segredos, usuário sem privilégio, rede liberada só para os IPs do job, limite de CPU/RAM/tempo | Permite templates de cliente | Infra mais complexa |
| **C. Formato declarativo** | Trocar o Python por passos JSON (`goto`, `fill`, `click`, `wait`, `assert`), como os passos do Config Flow | Seguro por construção; dá para ter editor visual | Precisa reescrever o gravador e o motor de execução |

**Recomendação:** lançar com **A**, rodando o worker isolado como em **B**, e planejar **C** se os clientes forem criar os próprios bots.

Outros pontos:
- **Credenciais:** no desktop ficam em texto puro no SQLite. No OLT Cloud, guarde-as criptografadas (como as outras senhas do sistema) e passe ao motor só na hora de executar.
- **Screenshots** podem mostrar dados do cliente (SSID, PPPoE). Guarde por cliente e com prazo de retenção.
- **Escopo de rede:** valide que os IPs do job pertencem ao cliente que pediu, para que um cliente não use o bot contra a rede de outro.

---

## 5. Onde o bot roda (alcance de rede)

O navegador precisa **alcançar a interface web do equipamento** (IP de gerência/LAN). Opções:

1. **Worker no próprio servidor da instância do cliente**, se ele já tiver rota para a rede de gerência das CPEs.
2. **Worker central saindo pelo servidor do cliente** (já implementado em `core/remote.py`): SSH no servidor + proxy SOCKS5. O lote, o scanner e o navegador passam pelo servidor sem instalar nada nele:

   ```python
   from core.remote import Server, SshGateway
   with SshGateway(Server("10.0.0.5", 22, "support", key_path="/caminho/chave")) as gw:
       redes = gw.networks()                         # descoberta automática (rotas, ARP, CPEs no ACS)
       hosts = fp.scan(ips, [80, 443], gateway=gw)   # varredura roda no servidor
       results = asyncio.run(engine.run_batch(..., proxy=gw.start_socks()))
   ```
   Pela linha de comando: `--via HOST:PORTA` em `run` e `scan`. No servidor só rodam comandos de leitura fixos (`ip`, `ss`, teste de porta com `bash`, `snmpget`).
3. **Agente na rede do cliente** (um mini-PC ou VM rodando `python -m core run`), que recebe jobs do OLT Cloud e devolve o JSON. Resolve redes sem rota a partir da nuvem.

Em testes num cliente real, o servidor do ACS alcançava as redes das CPEs (descobertas pelas conexões TR-069 abertas) e boa parte delas tinha a interface web acessível por ali. Já pelo IP público, nenhuma das CPEs amostradas respondia web. Ou seja, **sair pelo servidor do ACS costuma ser o caminho**.

---

## 6. Dimensionamento

- Cada worker abre **um** Chromium (~250 MB) e um contexto por equipamento simultâneo (~60 MB cada).
- Ex.: 10 simultâneos ≈ 850 MB. O tempo por equipamento depende do modelo (normalmente 3 a 30 s).
- Credencial errada geralmente vira timeout. Por isso deixe a **senha mais provável primeiro** na lista e use um `timeout_ms` adequado ao modelo.

### Exemplo de imagem (não testado neste projeto)

```dockerfile
FROM mcr.microsoft.com/playwright/python:v1.58.0-noble
WORKDIR /app
COPY core/ core/
RUN pip install --no-cache-dir "pandas>=2.2" openpyxl
USER pwuser
ENTRYPOINT ["python", "-m", "core"]
```

Escolha uma tag da imagem que bata com a versão do `playwright` instalada.

---

## 7. Modelo de dados sugerido

```
BotTemplate   id, cliente(nullable = global), vendor, model, firmware, hardware,
              script, credentials(criptografado), defaults, homologado(bool)
BotJob        id, cliente, template, criado_por, status, workers, attempts, timeout,
              iniciado_em, terminado_em
BotJobDevice  job, index, ip, port, variables(json), status, error_kind, message,
              attempts, credential_user, screenshot(arquivo), duration_s
```

`BotJobDevice` corresponde 1:1 a `DeviceResult.as_dict()`.

---

## 8. Testes

```bash
python -m unittest discover -s tests -t . -v
```

Eles sobem um roteador falso local (login por formulário, HTTP Basic, página lenta) e cobrem: navegador único por lote, limite de concorrência, retry, lista de credenciais, HTTP 401, erro de template, cancelamento, valores padrão/porta e o scanner (HTTP, UPnP, SNMP, sonda Fiberhome).
