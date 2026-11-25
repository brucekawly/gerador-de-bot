"""Linha de comando (sem interface gráfica) — roda em Windows, Linux e servidor/contêiner.

Exemplos:
  python -m core run --template-id 3 --devices equipamentos.xlsx --json resultado.json
  python -m core scan --discover --via 203.0.113.10:2222          # redes que o servidor alcança
  python -m core scan 10.20.30.0/24 --via 203.0.113.10:2222      # varredura rodando no servidor
  python -m core run --template-id 3 --devices lista.csv --via 203.0.113.10:2222
  python -m core run --template-file meu_template.py --devices lista.csv --credentials creds.txt --workers 10
  python -m core scan 192.168.1.1 192.168.1.254 --ports 80,443,8080 --json scan.json
  python -m core templates

Código de saída: 0 = tudo OK, 1 = alguma falha, 2 = erro de uso/arquivo.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import sys


def _load_devices(path: str):
    import pandas as pd

    from core.devices import parse_dataframe
    if path.lower().endswith(".csv"):
        df = pd.read_csv(path, sep=None, engine="python", dtype=str)
    elif path.lower().endswith(".json"):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        df = pd.DataFrame(data)
    else:
        df = pd.read_excel(path, dtype=str)
    return parse_dataframe(df)


def _write_json(path: str | None, payload) -> None:
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if path in (None, "-"):
        print(text)
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)


def _gateway(args):
    """--via HOST[:PORTA] -> SshGateway conectado (ou None)."""
    if not getattr(args, "via", None):
        return None
    from core.remote import DEFAULT_KEY, Server, SshGateway
    host, _, port = args.via.partition(":")
    pwd = os.environ.get(args.ssh_password_env) if args.ssh_password_env else None
    server = Server(host, int(port or 22), args.ssh_user, auth="password" if pwd else "key",
                    key_path=args.ssh_key or DEFAULT_KEY, password=pwd)
    return SshGateway(server).connect()


def _add_via_args(parser):
    g = parser.add_argument_group("acesso pelo servidor do cliente (SSH)")
    g.add_argument("--via", metavar="HOST[:PORTA]", help="servidor do cliente que alcança os equipamentos")
    g.add_argument("--ssh-user", default="support")
    g.add_argument("--ssh-key", help="chave privada (padrão ~/.ssh/oltsoft_acs)")
    g.add_argument("--ssh-password-env", metavar="VAR",
                   help="nome da variável de ambiente com a senha do SSH (em vez de chave)")


def cmd_run(args) -> int:
    from core.automation_engine import AutomationEngine, RetryPolicy, validate_template
    from core.devices import parse_credentials, parse_defaults

    creds_text, defaults_text = "", ""
    if args.template_id is not None:
        from database.db_handler import DatabaseHandler
        row = DatabaseHandler(args.db).get_template(args.template_id) if args.db else \
            DatabaseHandler().get_template(args.template_id)
        if not row:
            print(f"Template {args.template_id} não encontrado.", file=sys.stderr)
            return 2
        script, creds_text, defaults_text = row[5], row[6], row[7]
    else:
        with open(args.template_file, encoding="utf-8") as f:
            script = f.read()
    if args.credentials:
        with open(args.credentials, encoding="utf-8") as f:
            creds_text = f.read()
    err = validate_template(script)
    if err:
        print(f"Template com erro de sintaxe: {err}", file=sys.stderr)
        return 2
    try:
        devices, warns = _load_devices(args.devices)
    except (OSError, ValueError) as e:
        print(f"Erro ao ler equipamentos: {e}", file=sys.stderr)
        return 2
    for w in warns:
        print("aviso:", w, file=sys.stderr)

    engine = AutomationEngine(max_concurrent=args.workers, retry=RetryPolicy(max_attempts=args.attempts),
                              logs_dir=args.logs, screenshots=not args.no_screenshots)

    def progress(i, status, detail):
        if not args.quiet:
            d = devices[i]
            print(f"[{d.ip}:{d.port}] {status} {detail}".rstrip(), file=sys.stderr, flush=True)

    try:
        gw = _gateway(args)
    except Exception as e:  # noqa: BLE001
        print(f"Servidor: {e}", file=sys.stderr)
        return 2
    try:
        results = asyncio.run(engine.run_batch(devices, script, parse_credentials(creds_text),
                                           browser_type=args.browser, timeout_ms=args.timeout * 1000,
                                           progress_callback=progress, visible=args.show,
                                           proxy=gw.start_socks() if gw else None,
                                           defaults={**parse_defaults(defaults_text),
                                                     **parse_defaults("\n".join(args.set or []))}))
    finally:
        if gw:
            gw.close()
    ok = sum(1 for r in results if r.status == "success")
    _write_json(args.json, {"total": len(results), "success": ok, "failed": len(results) - ok,
                            "logs_dir": engine.run_dir, "results": [r.as_dict() for r in results]})
    print(f"Concluído: {ok}/{len(results)} com sucesso.", file=sys.stderr)
    return 0 if ok == len(results) else 1


def cmd_scan(args) -> int:
    from core import fingerprint as fpm
    try:
        gw = _gateway(args)
    except Exception as e:  # noqa: BLE001
        print(f"Servidor: {e}", file=sys.stderr)
        return 2
    try:
        if args.discover:
            from core.remote import local_networks
            _write_json(args.json, gw.networks() if gw else local_networks())
            return 0
        if not args.targets:
            print("Informe as faixas (ex.: 192.168.1.0/24) ou use --discover.", file=sys.stderr)
            return 2
        try:
            ips = fpm.parse_targets(" ".join(args.targets))
            ports = [int(p) for p in args.ports.split(",") if p.strip()]
        except ValueError as e:
            print(e, file=sys.stderr)
            return 2
        return _do_scan(args, fpm, ips, ports, gw)
    finally:
        if gw:
            gw.close()


def _do_scan(args, fpm, ips, ports, gw) -> int:

    def progress(done, total, fp):
        if fp is not None and not args.quiet:
            print(f"{fp.ip}:{fp.port}  {fp.label}  {fp.firmware}", file=sys.stderr, flush=True)

    res = fpm.scan(ips, ports, identify_models=not args.no_identify,
                   snmp_community=None if args.no_snmp else args.community, progress=progress, gateway=gw)
    out = []
    for f in res:
        d = dataclasses.asdict(f)
        d.pop("raw", None)
        d["note"] = f.raw.get("note", "")
        out.append(d)
    _write_json(args.json, out)
    return 0


def cmd_templates(args) -> int:
    from database.db_handler import DatabaseHandler
    from core.devices import BUILTIN_VARS, parse_credentials, template_variables
    db = DatabaseHandler(args.db) if args.db else DatabaseHandler()
    rows = [{"id": r[0], "vendor": r[1], "model": r[2], "firmware": r[3], "hardware": r[4],
             "variables": sorted(template_variables(r[5]) - BUILTIN_VARS),
             "credentials": len(parse_credentials(r[6]))} for r in db.get_all_templates()]
    _write_json(args.json, rows)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m core", description="Gerador de BOT — modo linha de comando")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="executa um template num lote de equipamentos")
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--template-id", type=int, help="ID do template no banco (bot_templates.db)")
    g.add_argument("--template-file", help="arquivo .py com o corpo do template")
    r.add_argument("--devices", required=True, help="planilha .xlsx/.csv/.json (coluna IP obrigatória)")
    r.add_argument("--credentials", help="arquivo com usuario:senha por linha (substitui as do template)")
    r.add_argument("--set", action="append", metavar="NOME=valor",
                   help="valor padrão de variável (ex.: --set PORT=8080 --set SCHEME=https); repetível")
    r.add_argument("--workers", type=int, default=5)
    r.add_argument("--attempts", type=int, default=3)
    r.add_argument("--timeout", type=int, default=30, help="segundos por ação/navegação")
    r.add_argument("--browser", default="chromium", choices=["chromium", "firefox", "webkit"])
    r.add_argument("--show", action="store_true", help="mostra o navegador (precisa de interface gráfica)")
    r.add_argument("--logs", help="pasta dos logs/screenshots (padrão ./logs ou $GERADOR_BOT_LOGS)")
    r.add_argument("--no-screenshots", action="store_true")
    r.add_argument("--json", help="salva o resultado em JSON ('-' = stdout)")
    r.add_argument("--db", help="caminho do banco de templates")
    r.add_argument("-q", "--quiet", action="store_true")
    _add_via_args(r)
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("scan", help="varre a rede e identifica fabricante/modelo/firmware")
    s.add_argument("targets", nargs="*", help="faixas: 192.168.1.0/24, 10.0.0.1-50, IPs soltos")
    s.add_argument("--discover", action="store_true", help="só lista as redes que dá para varrer")
    s.add_argument("--ports", default="80,443,8080")
    s.add_argument("--community", default="public")
    s.add_argument("--no-snmp", action="store_true")
    s.add_argument("--no-identify", action="store_true")
    s.add_argument("--json", default="-")
    s.add_argument("-q", "--quiet", action="store_true")
    _add_via_args(s)
    s.set_defaults(func=cmd_scan)

    t = sub.add_parser("templates", help="lista os templates cadastrados")
    t.add_argument("--db")
    t.add_argument("--json", default="-")
    t.set_defaults(func=cmd_templates)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
