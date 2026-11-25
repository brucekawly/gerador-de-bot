"""Janela do Scanner de Rede: acha IPs com interface web e identifica fabricante/modelo/firmware."""
import socket
import threading
from tkinter import messagebox, ttk

import customtkinter as ctk

from core import fingerprint as fpm
from gui import theme


def _guess_range():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))     # não envia nada; só descobre o IP local
            ip = s.getsockname()[0]
        base = ip.rsplit(".", 1)[0]
        return f"{base}.1", f"{base}.254"
    except OSError:
        return "192.168.1.1", "192.168.1.254"


class ScannerDialog(ctk.CTkToplevel):
    """on_add(list[Fingerprint], template_row|None) é chamado ao adicionar à fila."""

    COLS = ("ip", "porta", "fabricante", "modelo", "firmware", "mac", "fonte", "template")
    HEAD = ("IP", "Porta", "Fabricante", "Modelo", "Firmware", "MAC", "Identificado por", "Template sugerido")
    WIDTH = (115, 55, 100, 130, 140, 135, 260, 220)

    def __init__(self, master, templates, on_add):
        super().__init__(master)
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
                                "firmware sem fazer login (página web, UPnP, SNMP).",
                     text_color=theme.MUTED).pack(anchor="w")

        form = theme.card(self)
        form.grid(row=1, column=0, sticky="ew", padx=20, pady=10)
        start, end = _guess_range()
        self.e_start = self._field(form, "IP inicial", start, 0)
        self.e_end = self._field(form, "IP final", end, 1)
        self.e_ports = self._field(form, "Portas (em ordem)", "80, 443, 8080", 2, width=150)
        self.e_snmp = self._field(form, "Community SNMP", "public", 3, width=110)
        self.var_ident = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(form, text="Identificar modelo", variable=self.var_ident).grid(
            row=1, column=4, padx=12, pady=(0, 12), sticky="w")
        self.btn_scan = theme.button(form, "Iniciar varredura", "primary", command=self._start, width=170)
        self.btn_scan.grid(row=0, column=5, rowspan=2, padx=16, pady=12, sticky="e")
        form.grid_columnconfigure(5, weight=1)

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
        sb = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        sb.grid(row=0, column=1, sticky="ns", pady=8, padx=(0, 4))
        self.empty = ctk.CTkLabel(table, text="Defina a faixa de IPs e clique em Iniciar varredura.",
                                  text_color=theme.MUTED)
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
            ips = fpm.expand_range(self.e_start.get().strip(), self.e_end.get().strip())
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

        def run():
            try:
                res = fpm.scan(ips, ports, identify_models=ident, snmp_community=community,
                               progress=progress, stop=self.stop)
            except Exception as e:  # noqa: BLE001
                self.after(0, lambda: messagebox.showerror("Scanner", f"Falha na varredura:\n{e}", parent=self))
                res = []
            self.after(0, lambda: self._done(res, ident))
        threading.Thread(target=run, daemon=True).start()

    def _add_row(self, fp: fpm.Fingerprint):
        if not self.winfo_exists():
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
        known = sum(1 for f in results if f.vendor)
        msg = f"{len(results)} equipamento(s) com interface web"
        if ident:
            msg += f" · {known} identificado(s)"
        if self.stop.is_set():
            msg += " · varredura interrompida"
        self.lbl.configure(text=msg)
        self.btn_scan.configure(state="normal", text="Iniciar varredura", fg_color=theme.ACCENT)
        state = "normal" if results else "disabled"
        self.btn_add_all.configure(state=state)
        self.btn_add_sel.configure(state=state)
        if not results:
            self.empty.configure(text="Nenhum equipamento respondeu nas portas informadas.")
            self.empty.place(relx=0.5, rely=0.5, anchor="center")

    def _add(self, all_rows):
        ips = list(self.tree.get_children()) if all_rows else list(self.tree.selection())
        if not ips:
            messagebox.showinfo("Scanner", "Selecione um ou mais equipamentos na lista.", parent=self)
            return
        chosen = [f for f in self.results if f.ip in ips]
        tpls = {self.suggestions.get(f.ip) for f in chosen}
        template = next(iter(tpls)) if len(tpls) == 1 else None
        if len({t for t in tpls if t}) > 1:
            messagebox.showinfo("Scanner", "Os equipamentos selecionados sugerem templates diferentes.\n"
                                "Dica: adicione um modelo por vez para rodar cada um com o template certo.",
                                parent=self)
        self.on_add(chosen, template)
        self._close()

    def _close(self):
        self.stop.set()
        self.grab_release()
        self.destroy()
