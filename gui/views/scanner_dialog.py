"""Janela do Scanner de Rede: acha IPs com interface web e identifica fabricante/modelo/firmware."""
import threading
from tkinter import messagebox, ttk

import customtkinter as ctk

import webbrowser

from core import fingerprint as fpm
from core.browser import open_url
from core.remote import POOL, local_networks
from gui import theme

DIRECT_LABEL = "Direto (rede deste PC)"


def _guess_range():
    ip = fpm.local_ip()
    return f"{ip.rsplit('.', 1)[0]}.0/24" if ip else "192.168.1.0/24"


class ScannerDialog(ctk.CTkToplevel):
    """on_add(list[Fingerprint], template_row|None, via_label) é chamado ao adicionar à fila."""

    COLS = ("ip", "porta", "fabricante", "modelo", "firmware", "mac", "fonte", "template")
    HEAD = ("IP", "Porta", "Fabricante", "Modelo", "Firmware", "MAC", "Identificado por", "Template sugerido")
    WIDTH = (115, 55, 100, 130, 120, 135, 310, 190)

    def __init__(self, master, templates, on_add, servers=None, via_label=DIRECT_LABEL):
        super().__init__(master)
        self.servers = servers or {}          # {"Servidor: nome": linha do banco}
        self.via_label = via_label if via_label in self.servers else DIRECT_LABEL
        self.title("Scanner de Rede")
        self.geometry("1250x660")
        self.minsize(900, 500)
        self.transient(master.winfo_toplevel())
        self.templates = templates
        self.on_add = on_add
        self.results: list[fpm.Fingerprint] = []
        self.suggestions: dict[str, tuple] = {}
        self.stop = threading.Event()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._build()
        self.after(100, self.grab_set)

    # ------------------------------------------------------------------ UI
    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 4))
        ctk.CTkLabel(head, text="Scanner de Rede", font=theme.font(20, "bold")).pack(anchor="w")
        ctk.CTkLabel(head, text="Encontra equipamentos com interface web e tenta identificar fabricante, modelo e "
                                "firmware sem fazer login. Pelo servidor do cliente, a varredura roda lá.",
                     text_color=theme.MUTED).pack(anchor="w")

        form = theme.card(self)
        form.grid(row=1, column=0, sticky="ew", padx=20, pady=10)
        ctk.CTkLabel(form, text="Acessar via", text_color=theme.MUTED, font=theme.font(11)).grid(
            row=0, column=0, padx=12, pady=(10, 0), sticky="w")
        self.via_var = ctk.StringVar(value=self.via_label)
        ctk.CTkOptionMenu(form, variable=self.via_var, values=[DIRECT_LABEL, *self.servers], width=210,
                          dynamic_resizing=False).grid(row=1, column=0, padx=12, pady=(0, 12), sticky="w")
        ctk.CTkLabel(form, text="Faixas (ex.: 192.168.1.0/24, 10.0.0.1-50)", text_color=theme.MUTED,
                     font=theme.font(11)).grid(row=0, column=1, padx=12, pady=(10, 0), sticky="w")
        tf = ctk.CTkFrame(form, fg_color="transparent")
        tf.grid(row=1, column=1, padx=12, pady=(0, 12), sticky="ew")
        self.e_targets = ctk.CTkEntry(tf, width=330)
        self.e_targets.insert(0, _guess_range() if self.via_label == DIRECT_LABEL else "")
        self.e_targets.pack(side="left", fill="x", expand=True)
        self.btn_discover = theme.button(tf, "Descobrir redes", width=130, command=self._discover)
        self.btn_discover.pack(side="left", padx=(8, 0))
        self.e_ports = self._field(form, "Portas (em ordem)", "80, 443, 8080", 2, width=130)
        self.e_snmp = self._field(form, "Community SNMP", "public", 3, width=100)
        self.var_ident = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(form, text="Identificar modelo", variable=self.var_ident).grid(
            row=1, column=4, padx=12, pady=(0, 12), sticky="w")
        self.btn_scan = theme.button(form, "Iniciar varredura", "primary", command=self._start, width=160)
        self.btn_scan.grid(row=0, column=5, rowspan=2, padx=16, pady=12, sticky="e")
        form.grid_columnconfigure(1, weight=1)

        table = theme.card(self)
        table.grid(row=2, column=0, sticky="nsew", padx=20, pady=(0, 6))
        table.grid_rowconfigure(0, weight=1)
        table.grid_columnconfigure(0, weight=1)
        theme.apply_tree_style()
        self.tree = ttk.Treeview(table, columns=self.COLS, show="headings", selectmode="extended")
        for c, h, w in zip(self.COLS, self.HEAD, self.WIDTH):
            self.tree.heading(c, text=h, anchor="w")
            self.tree.column(c, width=w, minwidth=50, anchor="w", stretch=(c == "template"))
        self.tree.tag_configure("unknown", foreground=theme.pick(theme.MUTED))
        self.tree.tag_configure("match", foreground=theme.pick(theme.SUCCESS))
        self.tree.tag_configure("vendor", foreground=theme.pick(theme.WARNING))
        self.tree.tag_configure("silent", foreground=theme.pick(theme.BORDER_STRONG))
        sb = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        sb.grid(row=0, column=1, sticky="ns", pady=8, padx=(0, 4))
        self.tree.bind("<Double-1>", self._open_selected)
        self.empty = ctk.CTkLabel(table, text="Informe as faixas (ou clique em Descobrir redes) e depois em "
                                              "Iniciar varredura.\nDois cliques num equipamento abrem a tela dele "
                                              "no navegador.", text_color=theme.MUTED)
        self.empty.place(relx=0.5, rely=0.5, anchor="center")

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=3, column=0, sticky="ew", padx=20, pady=(4, 6))
        self.progress = ctk.CTkProgressBar(foot, width=260)
        self.progress.set(0)
        self.progress.pack(side="left")
        self.lbl = ctk.CTkLabel(foot, text="", text_color=theme.MUTED)
        self.lbl.pack(side="left", padx=12)
        legend = ctk.CTkFrame(self, fg_color="transparent")
        legend.grid(row=4, column=0, sticky="w", padx=20, pady=(0, 12))
        ctk.CTkLabel(legend, text="● modelo bate com um template", text_color=theme.SUCCESS,
                     font=theme.font(11)).pack(side="left")
        ctk.CTkLabel(legend, text="● só o fabricante bate (conferir)", text_color=theme.WARNING,
                     font=theme.font(11)).pack(side="left", padx=14)
        ctk.CTkLabel(legend, text="● não identificado", text_color=theme.MUTED,
                     font=theme.font(11)).pack(side="left")
        self.btn_add_all = theme.button(foot, "Adicionar todos à fila", "primary", command=lambda: self._add(True),
                                        state="disabled")
        self.btn_add_all.pack(side="right")
        self.btn_add_sel = theme.button(foot, "Adicionar selecionados", command=lambda: self._add(False),
                                        state="disabled")
        self.btn_add_sel.pack(side="right", padx=8)

    def _field(self, master, label, value, col, width=140):
        ctk.CTkLabel(master, text=label, text_color=theme.MUTED, font=theme.font(11)).grid(
            row=0, column=col, padx=12, pady=(10, 0), sticky="w")
        e = ctk.CTkEntry(master, width=width)
        e.insert(0, value)
        e.grid(row=1, column=col, padx=12, pady=(0, 12), sticky="w")
        return e

    # ------------------------------------------------------------------ scan
    def _start(self):
        if self.btn_scan.cget("text") == "Parar":
            self.stop.set()
            self.btn_scan.configure(state="disabled", text="Parando...")
            return
        try:
            ips = fpm.parse_targets(self.e_targets.get())
            if not ips:
                raise ValueError("Informe ao menos uma faixa de IPs (ou use Descobrir redes).")
            ports = [int(p) for p in self.e_ports.get().replace(";", ",").split(",") if p.strip()]
            if not ports:
                raise ValueError("Informe ao menos uma porta.")
        except ValueError as e:
            messagebox.showerror("Scanner", str(e), parent=self)
            return
        self.stop.clear()
        self.results.clear()
        self.suggestions.clear()
        self.tree.delete(*self.tree.get_children())
        self.empty.place_forget()
        self.btn_scan.configure(text="Parar", fg_color=theme.DANGER_FILL)
        self.btn_add_all.configure(state="disabled")
        self.btn_add_sel.configure(state="disabled")
        ident = self.var_ident.get()
        community = self.e_snmp.get().strip() or None
        total = len(ips)
        self.lbl.configure(text=f"Procurando portas abertas em {total} IPs...")

        def progress(done, tot, fp):
            if fp is None:
                self.after(0, lambda: (self.progress.set(done / max(tot, 1) * (0.5 if ident else 1)),
                                       self.lbl.configure(text=f"Verificando portas: {done}/{tot}")))
            else:
                self.after(0, lambda f=fp: self._add_row(f))

        row = self.servers.get(self.via_var.get())
        if row:
            self.lbl.configure(text=f"Conectando no servidor {row[1]}...")

        def run():
            try:
                gw = POOL.get(row) if row else None
                if gw is not None:
                    self.after(0, lambda: self.lbl.configure(
                        text=f"Varredura rodando no servidor {row[1]} ({total} IPs)..."))
                res = fpm.scan(ips, ports, identify_models=ident, snmp_community=community,
                               progress=progress, stop=self.stop, gateway=gw)
            except Exception as e:  # noqa: BLE001
                self.after(0, lambda e=e: messagebox.showerror("Scanner", f"Falha na varredura:\n{e}", parent=self))
                res = []
            self.after(0, lambda: self._done(res, ident))
        threading.Thread(target=run, daemon=True).start()

    def _add_row(self, fp: fpm.Fingerprint):
        if not self.winfo_exists():
            return
        if not fp.open:   # está na rede (ARP), mas sem web nas portas informadas
            vals = (fp.ip, "—", "—", "sem interface web", "—", fp.mac or "—",
                    "na rede, nenhuma das portas respondeu", "—")
            if self.tree.exists(fp.ip):
                self.tree.item(fp.ip, values=vals, tags=("silent",))
            else:
                self.tree.insert("", "end", iid=fp.ip, values=vals, tags=("silent",))
            return
        tpl, conf = fpm.match_template(fp, self.templates)
        if tpl:
            self.suggestions[fp.ip] = tpl
        tag = "match" if conf == "modelo" else ("vendor" if conf == "fabricante" else
                                                ("unknown" if not fp.vendor else ""))
        tpl_txt = f"[{tpl[0]}] {tpl[1]} {tpl[2]}" + ("" if conf == "modelo" else " (conferir)") if tpl else "—"
        source = ", ".join(fp.sources) or ("—" if not fp.vendor else "")
        note = fp.raw.get("note", "")
        vals = (fp.ip, fp.port, fp.vendor or "?", fp.model or (fp.title[:30] if fp.title else "—"),
                fp.firmware or "—", fp.mac or "—", source + (f" · {note}" if note else ""), tpl_txt)
        if self.tree.exists(fp.ip):
            self.tree.item(fp.ip, values=vals, tags=(tag,))
        else:
            self.tree.insert("", "end", iid=fp.ip, values=vals, tags=(tag,))
        n = len(self.tree.get_children())
        self.progress.set(0.5 + 0.5 * min(1.0, n / max(n, 1)))
        self.lbl.configure(text=f"Identificando... {n} equipamento(s)")

    def _done(self, results, ident):
        if not self.winfo_exists():
            return
        self.results = results
        if not ident:
            for fp in results:
                self._add_row(fp)
        else:
            # reordena por IP
            for i, fp in enumerate(results):
                if self.tree.exists(fp.ip):
                    self.tree.move(fp.ip, "", i)
        self.progress.set(1)
        web = [f for f in results if f.open]
        silent = len(results) - len(web)
        known = sum(1 for f in web if f.vendor)
        msg = f"{len(web)} com interface web"
        if ident:
            msg += f" · {known} identificado(s)"
        if silent:
            msg += f" · {silent} na rede sem web (em cinza)"
        if self.stop.is_set():
            msg += " · varredura interrompida"
        self.lbl.configure(text=msg)
        self.btn_scan.configure(state="normal", text="Iniciar varredura", fg_color=theme.ACCENT)
        state = "normal" if web else "disabled"
        self.btn_add_all.configure(state=state)
        self.btn_add_sel.configure(state=state)
        if not results:
            me = fpm.local_ip() or "desconhecido"
            self.empty.configure(text=(
                "Nenhum equipamento respondeu nas portas informadas.\n\n"
                f"IP deste computador: {me}\n"
                "Confira se a faixa de IPs é a mesma rede deste computador (ou se a VPN está ligada),\n"
                "se as portas estão certas (ex.: 80, 443, 8080) e se o firewall/antivírus não está bloqueando.\n"
                "Dica: rode de novo; equipamento ocupado às vezes demora a responder."))
            self.empty.place(relx=0.5, rely=0.5, anchor="center")

    def _add(self, all_rows):
        ips = list(self.tree.get_children()) if all_rows else list(self.tree.selection())
        if not ips:
            messagebox.showinfo("Scanner", "Selecione um ou mais equipamentos na lista.", parent=self)
            return
        chosen = [f for f in self.results if f.ip in ips and f.open]
        if not chosen:
            messagebox.showinfo("Scanner", "Os itens selecionados não têm interface web nas portas informadas.",
                                parent=self)
            return
        tpls = {self.suggestions.get(f.ip) for f in chosen}
        template = next(iter(tpls)) if len(tpls) == 1 else None
        if len({t for t in tpls if t}) > 1:
            messagebox.showinfo("Scanner", "Os equipamentos selecionados sugerem templates diferentes.\n"
                                "Dica: adicione um modelo por vez para rodar cada um com o template certo.",
                                parent=self)
        self.on_add(chosen, template, self.via_var.get())
        self._close()

    def _close(self):
        self.stop.set()
        self.grab_release()
        self.destroy()

    # ------------------------------------------------------------------ descoberta automática de redes
    def _discover(self):
        row = self.servers.get(self.via_var.get())
        self.btn_discover.configure(state="disabled", text="Procurando...")
        self.lbl.configure(text=f"Descobrindo redes {'no servidor ' + row[1] if row else 'deste PC'}...")

        def run():
            try:
                nets = POOL.get(row).networks() if row else local_networks()
                err = None
            except Exception as e:  # noqa: BLE001
                nets, err = [], e
            self.after(0, lambda: self._show_networks(nets, err, row))
        threading.Thread(target=run, daemon=True).start()

    def _show_networks(self, nets, err, row):
        if not self.winfo_exists():
            return
        self.btn_discover.configure(state="normal", text="Descobrir redes")
        self.lbl.configure(text="")
        if err:
            messagebox.showerror("Descobrir redes", str(err), parent=self)
            return
        if not nets:
            messagebox.showinfo("Descobrir redes", "Nenhuma rede privada encontrada. Informe as faixas à mão.",
                                parent=self)
            return
        win = ctk.CTkToplevel(self)
        win.title("Redes encontradas")
        win.geometry("760x480")
        win.transient(self)
        win.after(100, win.grab_set)
        where = f"no servidor {row[1]}" if row else "neste PC"
        ctk.CTkLabel(win, text=f"Redes encontradas {where}", font=theme.font(17, "bold")).pack(
            anchor="w", padx=18, pady=(16, 2))
        ctk.CTkLabel(win, text="Marque as que quer varrer. Redes grandes vêm desmarcadas para não varrer "
                               "milhares de IPs sem querer.", text_color=theme.MUTED).pack(anchor="w", padx=18)
        box = ctk.CTkScrollableFrame(win, fg_color=theme.CARD)
        box.pack(fill="both", expand=True, padx=18, pady=10)
        checks = []
        for n in nets:
            var = ctk.BooleanVar(value=n["suggested"])
            alive = f" · {n['alive']} vistos" if n.get("alive") else ""
            ctk.CTkCheckBox(box, variable=var, text=f"{n['cidr']:<18}  {n['hosts']} IPs{alive}  —  {n['source']}",
                            font=("Consolas", 12)).pack(anchor="w", pady=3)
            checks.append((var, n))
        total_lbl = ctk.CTkLabel(win, text="", text_color=theme.MUTED)
        total_lbl.pack(anchor="w", padx=18)

        def update_total(*_):
            total = sum(n["hosts"] for v, n in checks if v.get())
            total_lbl.configure(text=f"{total} IPs selecionados" + (" — acima de 4096 a varredura fica lenta"
                                                                     if total > 4096 else ""))
        for v, _ in checks:
            v.trace_add("write", update_total)
        update_total()

        def use():
            sel = [n["cidr"] for v, n in checks if v.get()]
            self.e_targets.delete(0, "end")
            self.e_targets.insert(0, ", ".join(sel))
            win.destroy()
        bar = ctk.CTkFrame(win, fg_color="transparent")
        bar.pack(fill="x", padx=18, pady=(4, 16))
        theme.button(bar, "Usar estas redes", "primary", width=160, command=use).pack(side="right")
        theme.button(bar, "Cancelar", width=100, command=win.destroy).pack(side="right", padx=8)

    # ------------------------------------------------------------------ abrir no navegador
    def _open_selected(self, event=None):
        iid = self.tree.identify_row(event.y) if event is not None else (self.tree.selection() or [None])[0]
        fp = next((f for f in self.results if f.ip == iid and f.open), None)
        if fp is None:
            return
        url = f"{fp.scheme}://{fp.ip}:{fp.port}/"
        row = self.servers.get(self.via_var.get())
        if not row:
            webbrowser.open(url)
            return

        def run():
            try:
                open_url(url, POOL.proxy(row))
            except Exception as e:  # noqa: BLE001
                self.after(0, lambda e=e: messagebox.showerror("Abrir", str(e), parent=self))
        threading.Thread(target=run, daemon=True).start()
