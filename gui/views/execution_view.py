"""Tela de Execução em Lote: 1) template  2) equipamentos  3) executar e acompanhar."""
import asyncio
import ipaddress
import json
import os
import sys
import threading
import time
import webbrowser
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk
import pandas as pd

from core.automation_engine import ERROR_LABELS, AutomationEngine, RetryPolicy, default_logs_dir
from core.devices import (Device, missing_variables, parse_credentials, parse_dataframe, parse_defaults,
                          resolve_variables, template_variables)
from core.devices import BUILTIN_VARS
from core.platform_utils import open_path
from core.browser import open_url
from core.remote import POOL
from gui import theme
from gui.views.credentials_dialog import CredentialsDialog
from gui.views.scanner_dialog import ScannerDialog

TIMEOUTS = {"15 s (rápido)": 15000, "30 s (padrão)": 30000, "60 s (lento)": 60000, "90 s (muito lento)": 90000}
ATTEMPTS = {"1 (sem repetir)": 1, "2": 2, "3": 3, "5": 5}
FILTERS = ["Todos", "Falhas", "Sucesso", "Pendentes"]
DIRECT_LABEL = "Direto (rede deste PC)"


class ExecutionView(ctk.CTkFrame):
    COLS = ("n", "ip", "porta", "modelo", "status", "tent", "detalhe")
    HEAD = ("#", "IP / Host", "Porta", "Modelo", "Status", "Tent.", "Detalhe")
    WIDTH = (44, 130, 110, 150, 120, 50, 420)

    def __init__(self, master, db):
        super().__init__(master, corner_radius=10, fg_color="transparent")
        self.db = db
        self.devices: list[Device] = []
        self.rows: list[dict] = []          # estado de cada linha (mesmo índice de self.devices)
        self.active_engine: AutomationEngine | None = None
        self.running = False
        self.run_started = 0.0
        self.template_rows = []
        self._build()
        self.refresh_templates()

    # ================================================================== layout
    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

        # ---- cabeçalho com a ação principal
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 10))
        ctk.CTkLabel(head, text="Execução em lote", font=theme.font(22, "bold")).pack(side="left")
        self.btn_play = theme.button(head, "Iniciar automação", "primary", width=190,
                                     command=self.start_execution)
        self.btn_play.pack(side="right")
        self.btn_stop = theme.button(head, "Parar", "danger", width=120, command=self.stop_execution)
        self.btn_test = theme.button(head, "Testar em 1 IP", width=130, command=self.open_test_modal)
        self.btn_test.pack(side="right", padx=10)

        # ---- passo 1 e 2 lado a lado
        steps = ctk.CTkFrame(self, fg_color="transparent")
        steps.grid(row=1, column=0, sticky="ew")
        steps.grid_columnconfigure((0, 1), weight=1, uniform="s")

        c1 = theme.card(steps)
        c1.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        theme.section_title(c1, "1", "Template").pack(anchor="w", padx=14, pady=(12, 6))
        row = ctk.CTkFrame(c1, fg_color="transparent")
        row.pack(fill="x", padx=14)
        self.template_var = ctk.StringVar(value="")
        self.cb_templates = ctk.CTkOptionMenu(row, variable=self.template_var, values=[""], width=300,
                                              command=lambda _=None: self._update_template_info(),
                                              dynamic_resizing=False)
        self.cb_templates.pack(side="left")
        self.btn_creds = theme.button(row, "Credenciais", width=130, command=self.edit_credentials)
        self.btn_creds.pack(side="left", padx=8)
        self.lbl_template = ctk.CTkLabel(c1, text="", text_color=theme.MUTED, justify="left", anchor="w")
        self.lbl_template.pack(fill="x", padx=14, pady=(6, 12))

        c2 = theme.card(steps)
        c2.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        head2 = ctk.CTkFrame(c2, fg_color="transparent")
        head2.pack(fill="x", padx=14, pady=(12, 6))
        theme.section_title(head2, "2", "Equipamentos").pack(side="left")
        self.via_var = ctk.StringVar(value=DIRECT_LABEL)
        self.cb_via = ctk.CTkOptionMenu(head2, variable=self.via_var, values=[DIRECT_LABEL], width=220,
                                        dynamic_resizing=False, command=lambda _=None: self._update_via_info())
        self.cb_via.pack(side="right")
        ctk.CTkLabel(head2, text="Acessar via", text_color=theme.MUTED).pack(side="right", padx=8)
        row2 = ctk.CTkFrame(c2, fg_color="transparent")
        row2.pack(fill="x", padx=14)
        self.btn_import = theme.button(row2, "Planilha", width=110, command=self.import_list)
        self.btn_import.pack(side="left")
        self.btn_range = theme.button(row2, "Faixa de IP", width=120, command=self.open_ip_range_modal)
        self.btn_range.pack(side="left", padx=6)
        self.btn_scan = theme.button(row2, "Scanner", width=110, command=self.open_ip_scanner_modal)
        self.btn_scan.pack(side="left")
        self.btn_clear = theme.button(row2, "Limpar", "ghost", width=70, command=self.clear_devices)
        self.btn_clear.pack(side="left", padx=6)
        self.lbl_devices = ctk.CTkLabel(c2, text="", text_color=theme.MUTED, justify="left", anchor="w")
        self.lbl_devices.pack(fill="x", padx=14, pady=(6, 12))

        # ---- avançado
        adv = ctk.CTkFrame(self, fg_color="transparent")
        adv.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self.browser_var = ctk.StringVar(value="Firefox")
        self.timeout_var = ctk.StringVar(value="15 s (rápido)")
        self.attempts_var = ctk.StringVar(value="3")
        self._opt(adv, "Navegador", self.browser_var, ["Firefox", "Chromium", "WebKit"], 110)
        self._opt(adv, "Timeout", self.timeout_var, list(TIMEOUTS), 140)
        self._opt(adv, "Tentativas", self.attempts_var, list(ATTEMPTS), 130)
        ctk.CTkLabel(adv, text="Simultâneos", text_color=theme.MUTED).pack(side="left", padx=(14, 6))
        self.workers_var = ctk.IntVar(value=5)
        self.slider_workers = ctk.CTkSlider(adv, from_=1, to=50, number_of_steps=49, variable=self.workers_var,
                                            width=160, command=self._update_slider_label)
        self.slider_workers.pack(side="left")
        self.lbl_workers = ctk.CTkLabel(adv, text="", width=150, anchor="w")
        self.lbl_workers.pack(side="left", padx=6)
        self.var_visible = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(adv, text="Mostrar navegador", variable=self.var_visible,
                        checkbox_width=18, checkbox_height=18).pack(side="left", padx=10)
        self._update_slider_label(5)

        # ---- progresso (aparece ao executar)
        self.prog = ctk.CTkFrame(self, fg_color="transparent")
        self.prog.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        self.progress = ctk.CTkProgressBar(self.prog, height=10)
        self.progress.set(0)
        self.progress.pack(fill="x")
        self.lbl_progress = ctk.CTkLabel(self.prog, text="", anchor="w")
        self.lbl_progress.pack(fill="x")
        self.prog.grid_remove()

        # ---- tabela
        tcard = theme.card(self)
        tcard.grid(row=4, column=0, sticky="nsew", pady=(10, 0))
        tcard.grid_columnconfigure(0, weight=1)
        tcard.grid_rowconfigure(1, weight=1)
        bar = ctk.CTkFrame(tcard, fg_color="transparent")
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(8, 0))
        self.filter_var = ctk.StringVar(value="Todos")
        self.seg_filter = ctk.CTkSegmentedButton(bar, values=FILTERS, variable=self.filter_var,
                                                 command=lambda _=None: self._render_table())
        self.seg_filter.pack(side="left")
        self.lbl_counts = ctk.CTkLabel(bar, text="", text_color=theme.MUTED)
        self.lbl_counts.pack(side="left", padx=12)
        self.btn_export_py = theme.button(bar, "Exportar Python", "ghost", width=120, command=self.export_python)
        self.btn_export_py.pack(side="right")
        self.btn_logs = theme.button(bar, "Abrir pasta de logs", "ghost", width=140, command=self.open_logs)
        self.btn_logs.pack(side="right")
        self.btn_export = theme.button(bar, "Exportar relatório", "ghost", width=130, command=self.export_log)
        self.btn_export.pack(side="right")
        self.btn_retry = theme.button(bar, "Reexecutar falhas", width=150, command=self.retry_failures)

        theme.apply_tree_style()
        self.tree = ttk.Treeview(tcard, columns=self.COLS, show="headings", selectmode="extended")
        for c, h, w in zip(self.COLS, self.HEAD, self.WIDTH):
            self.tree.heading(c, text=h, anchor="w")
            self.tree.column(c, width=w, minwidth=40, anchor="w", stretch=(c == "detalhe"))
        theme.tag_tree(self.tree)
        sb = ttk.Scrollbar(tcard, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=1, column=0, sticky="nsew", padx=(10, 0), pady=10)
        sb.grid(row=1, column=1, sticky="ns", pady=10, padx=(0, 6))
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<Delete>", lambda e: self._remove_selected())

        self.empty = ctk.CTkLabel(
            tcard, justify="center", text_color=theme.MUTED, font=theme.font(13),
            text="Nenhum equipamento na fila.\n\nAdicione por Planilha (coluna IP + uma coluna para cada dado que muda\n"
                 "por equipamento, ex.: SSID, SENHA_WIFI, VLAN), Faixa de IP ou Scanner\n"
                 "(que também identifica o modelo).")
        self._render_table()

    def _opt(self, master, label, var, values, width):
        ctk.CTkLabel(master, text=label, text_color=theme.MUTED).pack(side="left", padx=(0, 6))
        ctk.CTkOptionMenu(master, variable=var, values=values, width=width).pack(side="left", padx=(0, 14))

    def on_theme_change(self):
        theme.apply_tree_style()
        theme.tag_tree(self.tree)

    def _update_slider_label(self, val):
        n = int(float(val))
        mb = 250 + n * 60      # 1 navegador + 1 contexto por equipamento
        txt = f"{n}  (~{mb / 1024:.1f} GB RAM)" if mb >= 1024 else f"{n}  (~{mb} MB RAM)"
        self.lbl_workers.configure(text=txt, text_color=theme.ERROR if n > 30 else theme.TEXT)

    # ================================================================== template
    # ================================================================== acesso pelo servidor
    def refresh_servers(self):
        rows = self.db.get_servers()
        self._servers = {f"Servidor: {r[1]}": r for r in rows}
        values = [DIRECT_LABEL] + list(self._servers)
        self.cb_via.configure(values=values)
        if self.via_var.get() not in values:
            self.via_var.set(DIRECT_LABEL)
        self._update_via_info()

    def _via_row(self):
        return getattr(self, "_servers", {}).get(self.via_var.get())

    def _update_via_info(self):
        self._update_devices_info()

    def _proxy_or_raise(self):
        """Chamar FORA da thread da tela: conecta no servidor (se escolhido) e devolve o proxy."""
        row = self._via_row()
        return POOL.proxy(row) if row else None

    def open_device(self, d):
        """Abre o equipamento no navegador: direto no navegador padrão, ou pelo servidor no navegador do app."""
        url = self._effective(d)["BASE_URL"] + "/"
        row = self._via_row()
        if not row:
            webbrowser.open(url)
            return

        def worker():
            try:
                proxy = POOL.proxy(row)
                open_url(url, proxy)
            except Exception as e:  # noqa: BLE001
                self.after(0, lambda e=e: messagebox.showerror("Abrir no navegador",
                                                           f"Não conectou no servidor {row[1]}:\n{e}"))
        threading.Thread(target=worker, daemon=True).start()

    def _get_template_names(self):
        self.template_rows = self.db.get_all_templates()
        return [f"[{t[0]}] {t[1]} {t[2]}" + (f" ({t[3]})" if t[3] else "") for t in self.template_rows]

    def refresh_templates(self):
        if hasattr(self, "cb_via"):
            self.refresh_servers()
        names = self._get_template_names()
        current = self.template_var.get()
        if not names:
            self.cb_templates.configure(values=["Nenhum template cadastrado"])
            self.template_var.set("Nenhum template cadastrado")
        else:
            self.cb_templates.configure(values=names)
            if current not in names:
                self.template_var.set(names[-1] if current == "" or "Nenhum" in current else names[0])
        self._update_template_info()

    def _selected_template(self):
        txt = self.template_var.get()
        if not txt.startswith("["):
            return None
        tid = int(txt.split("]")[0][1:])
        return self.db.get_template(tid)

    def _select_template_id(self, tid):
        for name in self.cb_templates.cget("values"):
            if name.startswith(f"[{tid}]"):
                self.template_var.set(name)
                self._update_template_info()
                return

    def _update_template_info(self):
        tpl = self._selected_template()
        if not tpl:
            self.lbl_template.configure(text="Cadastre um template em Gerenciador de Templates.")
            return
        creds = parse_credentials(tpl[6])
        custom = sorted(template_variables(tpl[5]) - BUILTIN_VARS)
        cred_txt = (f"Login: tenta {len(creds)} usuário(s) em ordem: " + ", ".join(c.username for c in creds[:4]) +
                    ("…" if len(creds) > 4 else "")) if creds else "Login: admin/admin (nenhuma credencial cadastrada)"
        var_txt = ("Precisa na planilha das colunas: " + ", ".join(custom)) if custom else \
            "Não precisa de colunas extras na planilha"
        d = parse_defaults(tpl[7])
        demo = resolve_variables(Device("IP"), None, d)
        acc_txt = f"Acesso padrão: {demo['SCHEME']}://IP:{demo['PORT']}" + (
            "" if ("PORT" in d or "SCHEME" in d) else " (automático; ajuste em Valores padrão do template)")
        self.lbl_template.configure(text=f"{acc_txt}\n{cred_txt}\n{var_txt}")
        self._update_devices_info()

    def edit_credentials(self):
        tpl = self._selected_template()
        if not tpl:
            messagebox.showwarning("Credenciais", "Selecione um template primeiro.")
            return

        def save(text):
            self.db.update_credentials(tpl[0], text)
            self._update_template_info()
        CredentialsDialog(self, f"{tpl[1]} {tpl[2]}", tpl[6], save)

    # ================================================================== equipamentos
    def _set_devices(self, devices, models=None, append=False):
        if self.running:
            return
        if not append:
            self.devices, self.rows = [], []
        for i, d in enumerate(devices):
            self.devices.append(d)
            self.rows.append({"status": "pending", "label": "Pendente", "attempts": 0, "detail": "",
                              "model": (models[i] if models else ""), "screenshot": None, "kind": None})
        self._render_table()
        self._update_devices_info()

    def _update_devices_info(self):
        n = len(self.devices)
        if not n:
            self.lbl_devices.configure(text="Nenhum equipamento na fila.")
            return
        cols = sorted({k for d in self.devices for k in d.variables})
        own = sum(1 for d in self.devices if d.credential)
        parts = [f"{n} equipamento(s) na fila"]
        if own:
            parts.append(f"{own} com usuário/senha próprio na planilha")
        row = self._via_row()
        if row:
            parts.append(f"acesso pelo servidor {row[1]}")
        txt = " · ".join(parts)
        txt += "\n" + ("Dados por equipamento (colunas da planilha): " + ", ".join(cols) if cols
                       else "Sem dados por equipamento (só IP e porta)")
        tpl = self._selected_template()
        if tpl:
            miss = missing_variables(tpl[5], self.devices, parse_defaults(tpl[7]))
            if miss:
                txt += "\n⚠ " + " · ".join(f"{v} equipamento(s) sem valor em {k}" for k, v in miss.items())
        self.lbl_devices.configure(text=txt)

    def clear_devices(self):
        if self.devices and messagebox.askyesno("Limpar fila", f"Remover os {len(self.devices)} equipamentos da fila?"):
            self._set_devices([])

    def import_list(self):
        path = filedialog.askopenfilename(title="Selecione a planilha",
                                          filetypes=(("Planilhas", "*.xlsx *.xls *.csv"), ("Todos", "*.*")))
        if not path:
            return
        try:
            if path.lower().endswith(".csv"):
                df = pd.read_csv(path, sep=None, engine="python", dtype=str)
            else:
                df = pd.read_excel(path, dtype=str)
            devices, warns = parse_dataframe(df)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Importar planilha", f"Não foi possível ler o arquivo:\n{e}\n\n"
                                 "Formato: coluna IP (obrigatória), Porta, Usuário, Senha e qualquer outra "
                                 "coluna vira variável {{NOME_DA_COLUNA}}.")
            return
        self._set_devices(devices)
        if warns:
            messagebox.showinfo("Importar planilha", "\n".join(warns))

    def open_ip_range_modal(self):
        modal = ctk.CTkToplevel(self)
        modal.title("Adicionar faixa de IPs")
        modal.geometry("380x300")
        modal.transient(self.winfo_toplevel())
        modal.after(100, modal.grab_set)
        ctk.CTkLabel(modal, text="Faixa de IPs", font=theme.font(18, "bold")).pack(pady=(18, 10))
        form = ctk.CTkFrame(modal, fg_color="transparent")
        form.pack(padx=20)
        entries = {}
        for i, (lbl, val) in enumerate((("IP inicial", "192.168.1.100"), ("IP final", "192.168.1.200"),
                                        ("Porta", ""))):
            ctk.CTkLabel(form, text=lbl).grid(row=i, column=0, sticky="w", pady=6, padx=(0, 10))
            e = ctk.CTkEntry(form, width=180, placeholder_text=val or "vazio = padrão do template")
            e.grid(row=i, column=1, pady=6)
            entries[lbl] = e

        def gen():
            from core.fingerprint import expand_range
            try:
                ips = expand_range(entries["IP inicial"].get().strip(), entries["IP final"].get().strip())
            except (ValueError, ipaddress.AddressValueError) as e:
                messagebox.showerror("Faixa de IPs", f"Faixa inválida: {e}", parent=modal)
                return
            port = entries["Porta"].get().strip()
            self._set_devices([Device(ip, port) for ip in ips], append=bool(self.devices) and messagebox.askyesno(
                "Faixa de IPs", "Adicionar ao final da fila atual?\n(Não = substituir a fila)", parent=modal))
            modal.destroy()
        theme.button(modal, "Adicionar à fila", "primary", command=gen).pack(pady=18)

    def open_ip_scanner_modal(self):
        def on_add(fps, template, via_label=None):
            if via_label and via_label in [DIRECT_LABEL, *getattr(self, "_servers", {})]:
                self.via_var.set(via_label)
            models = [" ".join(x for x in (f.vendor, f.model) if x) or "?" for f in fps]
            devs = []
            for f in fps:
                v = {"FABRICANTE": f.vendor, "MODELO": f.model, "FIRMWARE": f.firmware, "MAC": f.mac,
                     "SCHEME": f.scheme}
                devs.append(Device(f.ip, str(f.port), variables={k: x for k, x in v.items() if x}))
            self._set_devices(devs, models)
            if template:
                self._select_template_id(template[0])
        ScannerDialog(self, self.db.get_all_templates(), on_add,
                      servers=getattr(self, "_servers", {}), via_label=self.via_var.get())

    # ================================================================== tabela
    def _row_values(self, i):
        d, r = self.devices[i], self.rows[i]
        port = d.port or f"{self._effective(d)['PORT']} (padrão)"
        return (i + 1, d.ip, port, r["model"], r["label"], r["attempts"] or "", r["detail"])

    def _effective(self, d):
        tpl = self._selected_template()
        return resolve_variables(d, None, parse_defaults(tpl[7]) if tpl else {})

    def _visible(self, i):
        f, s = self.filter_var.get(), self.rows[i]["status"]
        return (f == "Todos" or (f == "Falhas" and s == "error") or (f == "Sucesso" and s == "success")
                or (f == "Pendentes" and s in ("pending", "running", "waiting")))

    def _render_table(self):
        self.tree.delete(*self.tree.get_children())
        for i in range(len(self.devices)):
            if self._visible(i):
                self.tree.insert("", "end", iid=str(i), values=self._row_values(i), tags=(self.rows[i]["status"],))
        if self.devices:
            self.empty.place_forget()
        else:
            self.empty.place(relx=0.5, rely=0.55, anchor="center")
        self._update_counts()

    def _update_row(self, i):
        iid = str(i)
        if self._visible(i):
            if self.tree.exists(iid):
                self.tree.item(iid, values=self._row_values(i), tags=(self.rows[i]["status"],))
            else:
                self._render_table()
                return
        elif self.tree.exists(iid):
            self.tree.delete(iid)
        self._update_counts()

    def _counts(self):
        c = {"success": 0, "error": 0, "cancelled": 0, "pending": 0, "running": 0, "waiting": 0}
        for r in self.rows:
            c[r["status"]] = c.get(r["status"], 0) + 1
        return c

    def _update_counts(self):
        c = self._counts()
        n = len(self.rows)
        self.lbl_counts.configure(text=f"{n} na fila · {c['success']} sucesso · {c['error']} falha" if n else "")
        if not self.running and c["error"]:
            self.btn_retry.pack(side="left", padx=8)
        else:
            self.btn_retry.pack_forget()
        if self.running:
            done = c["success"] + c["error"] + c["cancelled"]
            self.progress.set(done / max(n, 1))
            el = time.monotonic() - self.run_started
            eta = ""
            if done:
                rest = el / done * (n - done)
                eta = f" · faltam ~{int(rest // 60)}m{int(rest % 60):02d}s"
            self.lbl_progress.configure(
                text=f"{done}/{n} concluídos · ✔ {c['success']} sucesso · ✖ {c['error']} falha · "
                     f"⏳ {c['running'] + c['waiting']} em andamento · {int(el // 60)}m{int(el % 60):02d}s{eta}")

    def _on_double_click(self, event):
        iid = self.tree.identify_row(event.y)
        if iid:
            shot = self.rows[int(iid)].get("screenshot")
            if shot and os.path.exists(shot):
                open_path(shot)
            else:
                self.open_device(self.devices[int(iid)])

    def _on_right_click(self, event):
        import tkinter as tk
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        if iid not in self.tree.selection():
            self.tree.selection_set(iid)
        i = int(iid)
        d, r = self.devices[i], self.rows[i]
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Abrir no navegador" + (" (pelo servidor)" if self._via_row() else ""),
                         command=lambda: self.open_device(d))
        if r.get("screenshot") and os.path.exists(r["screenshot"]):
            menu.add_command(label="Ver screenshot da falha", command=lambda: open_path(r["screenshot"]))
        menu.add_command(label="Copiar IP", command=lambda: (self.clipboard_clear(), self.clipboard_append(d.ip)))
        if d.variables:
            menu.add_command(label="Ver variáveis", command=lambda: messagebox.showinfo(
                d.ip, "\n".join(f"{{{{{k}}}}} = {v}" for k, v in d.variables.items())))
        menu.add_separator()
        menu.add_command(label="Remover da fila", command=self._remove_selected,
                         state="disabled" if self.running else "normal")
        menu.tk_popup(event.x_root, event.y_root)

    def _remove_selected(self):
        if self.running:
            return
        drop = {int(i) for i in self.tree.selection()}
        if not drop:
            return
        self.devices = [d for i, d in enumerate(self.devices) if i not in drop]
        self.rows = [r for i, r in enumerate(self.rows) if i not in drop]
        self._render_table()
        self._update_devices_info()

    # ================================================================== execução
    def _settings(self):
        return dict(browser_type=self.browser_var.get().lower(), timeout_ms=TIMEOUTS[self.timeout_var.get()])

    def _progress_cb(self, i, status, detail):
        kind = {"Executando": "running", "Aguardando": "waiting", "Sucesso": "success",
                "Cancelado": "cancelled"}.get(status, "error")

        def upd():
            r = self.rows[i]
            r["status"], r["label"], r["detail"] = kind, ("✔ " + status if kind == "success" else status), detail
            if kind == "running":
                r["attempts"] = r.get("attempts", 0) + 1
            self._update_row(i)
        self.after(0, upd)

    def start_execution(self, only=None):
        tpl = self._selected_template()
        if not tpl:
            messagebox.showwarning("Iniciar", "Selecione um template.")
            return
        if not self.devices:
            messagebox.showwarning("Iniciar", "Adicione equipamentos à fila (Planilha, Faixa de IP ou Scanner).")
            return
        miss = missing_variables(tpl[5], self.devices if only is None else [self.devices[i] for i in only],
                                 parse_defaults(tpl[7]))
        if miss and not messagebox.askyesno(
                "Variáveis faltando",
                "O template usa variáveis que estão vazias em alguns equipamentos:\n\n" +
                "\n".join(f"• {{{{{k}}}}}: falta em {v} equipamento(s)" for k, v in miss.items()) +
                "\n\nElas serão preenchidas com texto vazio. Continuar mesmo assim?"):
            return
        app = self.winfo_toplevel()
        go = lambda: self._run(tpl, only)  # noqa: E731
        if hasattr(app, "download_browser_if_missing"):
            app.download_browser_if_missing(self.browser_var.get().lower(), callback=go)
        else:
            go()

    def retry_failures(self):
        failed = [i for i, r in enumerate(self.rows) if r["status"] == "error"]
        if failed:
            self.start_execution(only=failed)

    def _set_running(self, running):
        self.running = running
        state = "disabled" if running else "normal"
        for b in (self.btn_import, self.btn_range, self.btn_scan, self.btn_clear, self.btn_test, self.btn_creds,
                  self.cb_templates, self.slider_workers):
            b.configure(state=state)
        if running:
            self.btn_play.pack_forget()
            self.btn_stop.configure(state="normal", text="Parar")
            self.btn_stop.pack(side="right")
            self.prog.grid()
        else:
            self.btn_stop.pack_forget()
            self.btn_play.pack(side="right")
        self._update_counts()

    def _run(self, tpl, only):
        idx = list(range(len(self.devices))) if only is None else list(only)
        for i in idx:
            self.rows[i].update(status="pending", label="Na fila", detail="", attempts=0, screenshot=None)
        self.filter_var.set("Todos")
        self.run_started = time.monotonic()
        self._set_running(True)
        self._render_table()
        devices = [self.devices[i] for i in idx]
        creds = parse_credentials(tpl[6])
        engine = AutomationEngine(max_concurrent=int(self.workers_var.get()),
                                  retry=RetryPolicy(max_attempts=ATTEMPTS[self.attempts_var.get()]))
        self.active_engine = engine
        settings = self._settings()
        visible = self.var_visible.get()

        def cb(j, status, detail):
            self._progress_cb(idx[j], status, detail)

        def worker():
            results, error = [], None
            try:
                proxy = self._proxy_or_raise()
                results = asyncio.run(engine.run_batch(devices, tpl[5], creds, progress_callback=cb,
                                                       defaults=parse_defaults(tpl[7]),
                                                       visible=visible, proxy=proxy, **settings))
            except Exception as e:  # noqa: BLE001
                error = e
            self.after(0, lambda: self._finish(idx, results, error, engine))
        threading.Thread(target=worker, daemon=True).start()

    def _finish(self, idx, results, error, engine):
        for res in results:
            i = idx[res.index]
            r = self.rows[i]
            r["status"] = res.status
            r["label"] = "✔ Sucesso" if res.status == "success" else ERROR_LABELS.get(res.error_kind, "Erro")
            r["attempts"] = res.attempts
            r["detail"] = (f"OK em {res.duration_s:.0f}s" + (f" · usuário {res.credential}" if res.credential else "")
                           if res.status == "success" else res.message)
            r["screenshot"] = res.screenshot
            r["kind"] = res.error_kind
        self.last_run_dir = engine.run_dir
        self.active_engine = None
        self._set_running(False)
        self._render_table()
        if error:
            messagebox.showerror("Execução", f"A execução parou com erro:\n{error}")
            return
        ok = sum(1 for r in results if r.status == "success")
        fail = sum(1 for r in results if r.status == "error")
        el = time.monotonic() - self.run_started
        msg = f"{ok} sucesso · {fail} falha"
        self.lbl_progress.configure(text=f"Concluído em {int(el // 60)}m{int(el % 60):02d}s · {msg}")
        if fail:
            by_kind = {}
            for r in results:
                if r.status == "error":
                    by_kind[ERROR_LABELS.get(r.error_kind, "Erro")] = by_kind.get(ERROR_LABELS.get(r.error_kind, "Erro"), 0) + 1
            msg += "\n\nFalhas por tipo:\n" + "\n".join(f"• {k}: {v}" for k, v in by_kind.items())
            msg += "\n\nDê dois cliques numa falha para ver o screenshot, ou use ↻ Reexecutar falhas."
        messagebox.showinfo("Execução concluída", msg)

    def stop_execution(self):
        if self.active_engine:
            self.active_engine.cancel()
            self.btn_stop.configure(state="disabled", text="Parando...")

    # ================================================================== teste único
    def open_test_modal(self):
        tpl = self._selected_template()
        if not tpl:
            messagebox.showwarning("Testar", "Selecione um template.")
            return
        modal = ctk.CTkToplevel(self)
        modal.title("Testar em 1 IP")
        modal.geometry("420x420")
        modal.transient(self.winfo_toplevel())
        modal.after(100, modal.grab_set)
        ctk.CTkLabel(modal, text="Testar template em 1 equipamento", font=theme.font(17, "bold")).pack(pady=(18, 4))
        ctk.CTkLabel(modal, text="Abre o navegador visível para você acompanhar.", text_color=theme.MUTED).pack()
        form = ctk.CTkFrame(modal, fg_color="transparent")
        form.pack(pady=14)
        sel = self.tree.selection()
        first = self.devices[int(sel[0])] if sel else (self.devices[0] if self.devices else None)
        fields = {}
        for r, (lbl, val) in enumerate((("IP", first.ip if first else ""), ("Porta", first.port if first else ""),
                                        ("Usuário", ""), ("Senha", ""))):
            ctk.CTkLabel(form, text=lbl).grid(row=r, column=0, sticky="w", padx=(0, 10), pady=5)
            e = ctk.CTkEntry(form, width=200, show="•" if lbl == "Senha" else "",
                             placeholder_text={"Usuário": "(usa as credenciais do template)",
                                               "Porta": "vazio = padrão do template"}.get(lbl, ""))
            if val:
                e.insert(0, val)
            e.grid(row=r, column=1, pady=5)
            fields[lbl] = e
        status = ctk.CTkLabel(modal, text="", wraplength=380)
        status.pack(pady=6)

        def run():
            from core.devices import Credential
            ip = fields["IP"].get().strip()
            if not ip:
                status.configure(text="Informe o IP.", text_color=theme.ERROR)
                return
            user = fields["Usuário"].get().strip()
            cred = Credential(user, fields["Senha"].get()) if user else None
            variables = dict(first.variables) if first and first.ip == ip else {}
            dev = Device(ip, fields["Porta"].get().strip(), cred, variables)
            btn.configure(state="disabled", text="Executando...")
            status.configure(text="Abrindo navegador...", text_color=theme.INFO)

            def cb(_i, s, d):
                modal.after(0, lambda: status.configure(text=f"{s} · {d}" if d else s))

            def worker():
                engine = AutomationEngine(max_concurrent=1, retry=RetryPolicy(max_attempts=1))
                try:
                    res = asyncio.run(engine.test_single(dev, tpl[5], parse_credentials(tpl[6]),
                                                         proxy=self._proxy_or_raise(),
                                                         defaults=parse_defaults(tpl[7]),
                                                         progress_callback=cb, **self._settings()))
                except Exception as e:  # noqa: BLE001
                    res = e

                def done():
                    if not modal.winfo_exists():
                        return
                    btn.configure(state="normal", text="Executar teste")
                    if getattr(res, "status", "") == "success":
                        status.configure(text=f"✔ Funcionou! ({res.duration_s:.0f}s"
                                              + (f", usuário {res.credential})" if res.credential else ")"),
                                         text_color=theme.SUCCESS)
                    else:
                        kind = ERROR_LABELS.get(getattr(res, "error_kind", None), "Erro")
                        status.configure(text=f"✖ {kind}: {getattr(res, 'message', res)}", text_color=theme.ERROR)
                        shot = getattr(res, "screenshot", None)
                        if shot and messagebox.askyesno("Teste", "Abrir o screenshot da falha?", parent=modal):
                            open_path(shot)
                modal.after(0, done)
            threading.Thread(target=worker, daemon=True).start()

        btn = theme.button(modal, "Executar teste", "primary", command=lambda: self.winfo_toplevel()
                           .download_browser_if_missing(self.browser_var.get().lower(), callback=run)
                           if hasattr(self.winfo_toplevel(), "download_browser_if_missing") else run())
        btn.pack(pady=6)

    # ================================================================== exportações
    def export_log(self):
        if not self.devices:
            messagebox.showwarning("Exportar", "Nenhum dado para exportar.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".xlsx", title="Salvar relatório",
                                            filetypes=[("Excel", "*.xlsx"), ("CSV", "*.csv")])
        if not path:
            return
        data = []
        for d, r in zip(self.devices, self.rows):
            data.append({"IP": d.ip, "Porta": self._effective(d)["PORT"], "Modelo": r["model"], "Status": r["label"],
                         "Tentativas": r["attempts"], "Detalhe": r["detail"], "Screenshot": r["screenshot"] or "",
                         **d.variables})
        df = pd.DataFrame(data)
        try:
            if path.lower().endswith(".csv"):
                df.to_csv(path, index=False, sep=";", encoding="utf-8-sig")
            else:
                df.to_excel(path, index=False)
            messagebox.showinfo("Exportar", f"Relatório salvo em:\n{path}")
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Exportar", f"Falha ao salvar:\n{e}")

    def open_logs(self):
        path = getattr(self, "last_run_dir", None)
        if not path or not os.path.isdir(path):
            path = default_logs_dir()
            os.makedirs(path, exist_ok=True)
        open_path(path)

    def export_python(self):
        """Gera um .py autônomo com o mesmo motor do app (retry, credenciais, variáveis)."""
        tpl = self._selected_template()
        if not tpl or not self.devices:
            messagebox.showwarning("Exportar Python", "Selecione um template e adicione equipamentos.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".py", filetypes=[("Python", "*.py")],
                                            title="Salvar script de automação")
        if not path:
            return
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        try:
            with open(os.path.join(base, "core", "devices.py"), encoding="utf-8") as f:
                devices_src = f.read()
            with open(os.path.join(base, "core", "automation_engine.py"), encoding="utf-8") as f:
                engine_src = f.read().replace(
                    "from core.devices import PLACEHOLDER_RE, Credential, Device, normalize_var_name", "")
        except OSError as e:
            messagebox.showerror("Exportar Python", f"Código do motor não encontrado:\n{e}")
            return
        payload = {
            "devices": [{"ip": d.ip, "port": d.port, "variables": d.variables,
                         "credential": [d.credential.username, d.credential.password] if d.credential else None}
                        for d in self.devices],
            "credentials": tpl[6] or "",
            "defaults": parse_defaults(tpl[7]),
            "workers": int(self.workers_var.get()),
            "attempts": ATTEMPTS[self.attempts_var.get()],
            **self._settings(),
        }
        main = f'''

# ======================================================================== execução
CONFIG = json.loads({json.dumps(json.dumps(payload, ensure_ascii=False))})
SCRIPT_TEMPLATE = {tpl[5]!r}


def _main():
    devices = [Device(d["ip"], d["port"], Credential(*d["credential"]) if d["credential"] else None,
                      d["variables"]) for d in CONFIG["devices"]]
    engine = AutomationEngine(max_concurrent=CONFIG["workers"], retry=RetryPolicy(max_attempts=CONFIG["attempts"]))

    def progress(i, status, detail):
        print(f"[{{devices[i].ip}}:{{devices[i].port}}] {{status}} {{detail}}")

    results = asyncio.run(engine.run_batch(devices, SCRIPT_TEMPLATE, parse_credentials(CONFIG["credentials"]),
                                           browser_type=CONFIG["browser_type"], timeout_ms=CONFIG["timeout_ms"],
                                           progress_callback=progress, defaults=CONFIG["defaults"]))
    ok = sum(1 for r in results if r.status == "success")
    print(f"\\nConcluído: {{ok}}/{{len(results)}} com sucesso. Screenshots de falhas em: {{engine.run_dir}}")


if __name__ == "__main__":
    _main()
'''
        header = ('# Script gerado pelo Gerador de BOT\n'
                  f'# Template: {tpl[1]} {tpl[2]} {tpl[3] or ""}\n'
                  '# Requisitos: pip install playwright && playwright install\n'
                  'import json\n')
        devices_src = devices_src.replace("from __future__ import annotations", "")
        engine_src = engine_src.replace("from __future__ import annotations", "")
        code = "from __future__ import annotations\n" + header + devices_src + "\n" + engine_src + main
        code = code.replace("os.path.dirname(os.path.dirname(os.path.abspath(__file__)))",
                            "os.path.dirname(os.path.abspath(__file__))")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(code)
            messagebox.showinfo("Exportar Python", f"Script salvo em:\n{path}\n\nRodar com: python \"{os.path.basename(path)}\"\n"
                                "Atenção: o arquivo contém as senhas configuradas.")
        except OSError as e:
            messagebox.showerror("Exportar Python", f"Falha ao salvar:\n{e}")
