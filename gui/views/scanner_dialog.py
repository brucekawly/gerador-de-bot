"""Janela do Scanner de Rede: acha IPs com interface web e identifica fabricante/modelo/firmware."""
import threading
from tkinter import messagebox, ttk

import customtkinter as ctk

import webbrowser

from core import fingerprint as fpm
from core.browser import open_url
from core.remote import POOL, auto_selection, local_networks
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
        ctk.CTkLabel(form, text="Faixas de IP — vazio = descobrir automaticamente", text_color=theme.MUTED,
                     font=theme.font(11)).grid(row=0, column=1, padx=12, pady=(10, 0), sticky="w")
        tf = ctk.CTkFrame(form, fg_color="transparent")
        tf.grid(row=1, column=1, padx=12, pady=(0, 12), sticky="ew")
        # botão primeiro, à direita: o campo ocupa só o espaço que sobrar (antes ele escondia o botão)
        self.btn_discover = theme.button(tf, "Descobrir redes", width=130, command=self._discover)
        self.btn_discover.pack(side="right", padx=(8, 0))
        self.e_targets = ctk.CTkEntry(tf, width=180, placeholder_text="ex.: 192.168.1.0/24, 10.0.0.1-50")
        self.e_targets.pack(side="left", fill="x", expand=True)
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
        self.tree.bind("<Button-3>", self._context_menu)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._update_device_buttons())
        self.empty = ctk.CTkLabel(table, text="Informe as faixas ou deixe vazio para descobrir automaticamente, e clique em "
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
        # ações no equipamento selecionado
        self.btn_template = theme.button(foot, "Criar template", width=130, state="disabled",
                                         command=lambda: self._create_template())
        self.btn_template.pack(side="right", padx=(0, 16))
        self.btn_open = theme.button(foot, "Abrir no navegador", width=150, state="disabled",
                                     command=lambda: self._open_selected())
        self.btn_open.pack(side="right", padx=(0, 8))

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
        if not self.e_targets.get().strip():      # sem faixas: varredura automática
            self._discover(auto=True)
            return
        try:
            ips = fpm.parse_targets(self.e_targets.get())
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
        if fpm.is_system(fp):    # servidor/sistema web (ex.: o próprio OLT Cloud): não é equipamento
            vals = (fp.ip, fp.port, fp.vendor, fp.model, "—", fp.mac or "—", fp.raw.get("note", ""), "—")
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
        web = [f for f in results if f.open and not fpm.is_system(f)]
        n_sys = sum(1 for f in results if f.open and fpm.is_system(f))
        silent = len(results) - len(web)
        known = sum(1 for f in web if f.vendor)
        msg = f"{len(web)} com interface web"
        if ident:
            msg += f" · {known} identificado(s)"
        silent -= n_sys
        if n_sys:
            msg += f" · {n_sys} servidor(es)/sistema(s), não são equipamentos (em cinza)"
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
        systems = [f for f in chosen if fpm.is_system(f)]
        if systems:
            if all_rows:
                chosen = [f for f in chosen if not fpm.is_system(f)]
            elif not messagebox.askyesno(
                    "Scanner", "Entre os selecionados há servidor/sistema (não é equipamento):\n"
                    + "\n".join(f"• {f.ip} — {f.raw.get('note', '')}" for f in systems)
                    + "\n\nAdicionar mesmo assim?", parent=self):
                chosen = [f for f in chosen if not fpm.is_system(f)]
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
    def _discover(self, auto=False):
        """Descobre as redes. auto=True: depois de confirmar a lista, já começa a varredura."""
        row = self.servers.get(self.via_var.get())
        self.btn_discover.configure(state="disabled", text="Procurando...")
        self.btn_scan.configure(state="disabled")
        self.empty.configure(text=f"Descobrindo as redes {'que o servidor ' + row[1] + ' alcança' if row else 'deste PC'}..."
                                  "\nIsso leva poucos segundos.")
        self.empty.place(relx=0.5, rely=0.5, anchor="center")
        self.lbl.configure(text=f"Descobrindo redes {'no servidor ' + row[1] if row else 'deste PC'}...")

        def run():
            try:
                nets = POOL.get(row).networks() if row else local_networks()
                err = None
            except Exception as e:  # noqa: BLE001
                nets, err = [], e
            self.after(0, lambda: self._show_networks(nets, err, row, auto))
        threading.Thread(target=run, daemon=True).start()

    def _show_networks(self, nets, err, row, auto=False):
        if not self.winfo_exists():
            return
        self.btn_discover.configure(state="normal", text="Descobrir redes")
        self.btn_scan.configure(state="normal")
        self.lbl.configure(text="")
        self.empty.configure(text="Informe as faixas (ou deixe vazio para descobrir automaticamente) e clique "
                                  "em Iniciar varredura.\nDois cliques num equipamento abrem a tela dele no navegador.")
        if err:
            messagebox.showerror("Descobrir redes", str(err), parent=self)
            return
        if not nets:
            messagebox.showinfo("Descobrir redes", "Nenhuma rede privada encontrada. Informe as faixas à mão.",
                                parent=self)
            return
        win = ctk.CTkToplevel(self)
        win.title("Redes encontradas")
        win.geometry("780x560")
        win.transient(self)
        win.after(100, win.grab_set)
        where = f"no servidor {row[1]}" if row else "neste PC"
        nets = auto_selection(nets)
        n_checked = sum(1 for n in nets if n["checked"])
        ctk.CTkLabel(win, text=f"{len(nets)} redes encontradas {where}", font=theme.font(17, "bold")).pack(
            anchor="w", padx=18, pady=(16, 2))
        hint = ("Já marquei primeiro as redes com aparelhos vistos e as das interfaces, até uns 4 mil IPs. "
                "Redes grandes ou além do limite ficam desmarcadas para a varredura não demorar demais.")
        if n_checked < len(nets):
            hint += f"\n{len(nets) - n_checked} rede(s) desmarcada(s): marque se quiser incluir."
        ctk.CTkLabel(win, text=hint, text_color=theme.MUTED, justify="left", wraplength=740).pack(anchor="w", padx=18)
        tools = ctk.CTkFrame(win, fg_color="transparent")
        tools.pack(fill="x", padx=18, pady=(6, 0))
        box = ctk.CTkScrollableFrame(win, fg_color=theme.CARD)
        box.pack(fill="both", expand=True, padx=18, pady=10)
        checks = []
        theme.button(tools, "Marcar todas", "ghost", width=100,
                     command=lambda: [v.set(True) for v, _ in checks]).pack(side="left")
        theme.button(tools, "Desmarcar todas", "ghost", width=120,
                     command=lambda: [v.set(False) for v, _ in checks]).pack(side="left", padx=6)
        for n in nets:
            var = ctk.BooleanVar(value=n["checked"])
            alive = f" · {n['alive']} vistos" if n.get("alive") else ""
            ctk.CTkCheckBox(box, variable=var, text=f"{n['cidr']:<18}  {n['hosts']} IPs{alive}  —  {n['source']}",
                            font=("Consolas", 12)).pack(anchor="w", pady=3)
            checks.append((var, n))
        total_lbl = ctk.CTkLabel(win, text="", text_color=theme.MUTED)
        total_lbl.pack(anchor="w", padx=18)

        def update_total(*_):
            total = sum(n["hosts"] for v, n in checks if v.get())
            minutes = total / 254 * (0.5 if self.servers.get(self.via_var.get()) else 0.35)
            est = f" · estimativa: ~{max(1, round(minutes))} min" if total else ""
            total_lbl.configure(text=f"{total} IPs selecionados{est}"
                                     + (" — muitas redes, a varredura vai demorar" if total > 8192 else ""),
                                text_color=theme.WARNING if total > 8192 else theme.MUTED)
        for v, _ in checks:
            v.trace_add("write", update_total)
        update_total()

        def use():
            sel = [n["cidr"] for v, n in checks if v.get()]
            if not sel:
                messagebox.showinfo("Redes", "Marque ao menos uma rede.", parent=win)
                return
            self.e_targets.delete(0, "end")
            self.e_targets.insert(0, ", ".join(sel))
            win.destroy()
            if auto:
                self.after(100, self._start)
        bar = ctk.CTkFrame(win, fg_color="transparent")
        bar.pack(fill="x", padx=18, pady=(4, 16))
        theme.button(bar, "Varrer estas redes" if auto else "Usar estas redes", "primary", width=170,
                     command=use).pack(side="right")
        theme.button(bar, "Cancelar", width=100, command=win.destroy).pack(side="right", padx=8)

    # ------------------------------------------------------------------ abrir no navegador
    def _selected_fp(self):
        sel = self.tree.selection()
        if len(sel) != 1:
            return None
        return next((f for f in self.results if f.ip == sel[0] and f.open), None)

    def _update_device_buttons(self):
        state = "normal" if self._selected_fp() else "disabled"
        self.btn_open.configure(state=state)
        self.btn_template.configure(state=state)

    def _context_menu(self, event):
        import tkinter as tk
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        self.tree.selection_set(iid)
        fp = self._selected_fp()
        if fp is None:
            return
        m = tk.Menu(self, tearoff=0)
        via = " (pelo servidor)" if self.servers.get(self.via_var.get()) else ""
        m.add_command(label="Abrir no navegador" + via, command=self._open_selected)
        m.add_command(label="Criar template para este equipamento", command=self._create_template)
        m.add_separator()
        m.add_command(label="Copiar IP", command=lambda: (self.clipboard_clear(), self.clipboard_append(fp.ip)))
        m.tk_popup(event.x_root, event.y_root)

    def _create_template(self):
        """Abre o editor de template já preenchido com o que o scanner detectou."""
        fp = self._selected_fp()
        if fp is None:
            return
        if fpm.is_system(fp) and not messagebox.askyesno(
                "Criar template", f"{fp.ip} não é um equipamento: {fp.raw.get('note', '')}.\n\n"
                "Escolha um roteador/ONT da lista. Criar o template mesmo assim?", parent=self, icon="warning"):
            return
        app = self.master.winfo_toplevel()
        tv = getattr(app, "views", {}).get("templates")
        if tv is None:
            return
        prefill = {"vendor": fp.vendor, "model": fp.model, "firmware": fp.firmware,
                   "url": f"{fp.scheme}://{fp.ip}:{fp.port}", "via_label": self.via_var.get(),
                   "defaults": f"PORT={fp.port}\nSCHEME={fp.scheme}\n"}
        self.grab_release()
        tv.open_new_template_modal(prefill=prefill)

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
