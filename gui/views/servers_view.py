"""Servidores dos clientes: o app acessa os equipamentos pela rede deles (SSH + proxy)."""
import os
import threading
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from core import vault
from core.remote import DEFAULT_KEY, POOL
from gui import theme


class ServersView(ctk.CTkFrame):
    COLS = ("nome", "endereco", "usuario", "login")
    HEAD = ("Nome", "Endereço", "Usuário", "Login")
    WIDTH = (200, 220, 110, 160)

    def __init__(self, master, db):
        super().__init__(master, corner_radius=10, fg_color="transparent")
        self.db = db
        self.on_change = None
        self.editing_id = None
        self.grid_columnconfigure(0, weight=3)
        self.grid_columnconfigure(1, weight=2)
        self.grid_rowconfigure(1, weight=1)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, columnspan=2, sticky="ew", padx=4, pady=(4, 10))
        ctk.CTkLabel(head, text="Servidores", font=theme.font(22, "bold")).pack(side="left")
        ctk.CTkLabel(head, text="  acesse os equipamentos pela rede do cliente (scanner, navegador, gravação e lote)",
                     text_color=theme.MUTED).pack(side="left", pady=(6, 0))
        theme.button(head, "+ Novo servidor", "primary", width=150, command=self._new).pack(side="right")

        # ---- lista
        card = theme.card(self)
        card.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        card.grid_rowconfigure(0, weight=1)
        card.grid_columnconfigure(0, weight=1)
        theme.apply_tree_style()
        self.tree = ttk.Treeview(card, columns=self.COLS, show="headings", selectmode="browse")
        for c, h, w in zip(self.COLS, self.HEAD, self.WIDTH):
            self.tree.heading(c, text=h, anchor="w")
            self.tree.column(c, width=w, minwidth=60, anchor="w", stretch=(c == "endereco"))
        self.tree.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._load_selected())
        self.empty = ctk.CTkLabel(card, text="Nenhum servidor cadastrado.\nCadastre o servidor do cliente "
                                             "(o do ACS/TR-069 costuma alcançar as CPEs).", text_color=theme.MUTED)

        # ---- formulário
        form = theme.card(self)
        form.grid(row=1, column=1, sticky="nsew", padx=(6, 0))
        form.grid_columnconfigure(0, weight=1)
        self.title_lbl = ctk.CTkLabel(form, text="Novo servidor", font=theme.font(15, "bold"))
        self.title_lbl.grid(row=0, column=0, sticky="w", padx=14, pady=(12, 6))
        self.e = {}
        r = 1
        for key, label, ph in (("name", "Nome *", "ex.: Cliente X - TR-069"), ("host", "IP ou host *", ""),
                               ("port", "Porta SSH", "22"), ("user", "Usuário *", "support")):
            ctk.CTkLabel(form, text=label, text_color=theme.MUTED, font=theme.font(11)).grid(
                row=r, column=0, sticky="w", padx=14)
            ent = ctk.CTkEntry(form, placeholder_text=ph)
            ent.grid(row=r + 1, column=0, sticky="ew", padx=14, pady=(0, 6))
            self.e[key] = ent
            r += 2
        ctk.CTkLabel(form, text="Login", text_color=theme.MUTED, font=theme.font(11)).grid(
            row=r, column=0, sticky="w", padx=14)
        self.auth_var = ctk.StringVar(value="Chave SSH")
        ctk.CTkSegmentedButton(form, values=["Chave SSH", "Senha"], variable=self.auth_var,
                               command=lambda _=None: self._auth_mode()).grid(row=r + 1, column=0, sticky="w",
                                                                             padx=14, pady=(0, 6))
        r += 2
        self.key_row = ctk.CTkFrame(form, fg_color="transparent")
        self.key_row.grid(row=r, column=0, sticky="ew", padx=14, pady=(0, 6))
        self.e_key = ctk.CTkEntry(self.key_row)
        self.e_key.pack(side="left", fill="x", expand=True)
        theme.button(self.key_row, "...", width=36, command=self._pick_key).pack(side="left", padx=(6, 0))
        self.e_pass = ctk.CTkEntry(form, show="•", placeholder_text="senha (fica no cofre do sistema)")
        self.pass_row = r
        r += 1
        self.status = ctk.CTkLabel(form, text="", justify="left", anchor="w", wraplength=330)
        self.status.grid(row=r + 1, column=0, sticky="ew", padx=14, pady=6)
        bar = ctk.CTkFrame(form, fg_color="transparent")
        bar.grid(row=r + 2, column=0, sticky="ew", padx=14, pady=(4, 14))
        theme.button(bar, "Salvar", "primary", width=100, command=self._save).pack(side="right")
        self.btn_test = theme.button(bar, "Verificar servidor", width=140, command=self._test)
        self.btn_test.pack(side="right", padx=8)
        self.btn_del = theme.button(bar, "Excluir", "ghost", width=70, text_color=theme.ERROR, command=self._delete)
        self.btn_del.pack(side="left")
        self._new()
        self.refresh()

    def on_theme_change(self):
        theme.apply_tree_style()

    # ------------------------------------------------------------------ lista
    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        rows = self.db.get_servers()
        for sid, name, host, port, user, auth, key in rows:
            login = "senha (cofre)" if auth == "password" else f"chave {os.path.basename(key or DEFAULT_KEY)}"
            self.tree.insert("", "end", iid=str(sid), values=(name, f"{host}:{port}", user, login))
        if rows:
            self.empty.place_forget()
        else:
            self.empty.place(relx=0.5, rely=0.5, anchor="center")

    def _load_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        row = self.db.get_server(int(sel[0]))
        if not row:
            return
        sid, name, host, port, user, auth, key = row
        self.editing_id = sid
        self.title_lbl.configure(text=f"Editar: {name}")
        for k, v in (("name", name), ("host", host), ("port", str(port)), ("user", user)):
            self.e[k].delete(0, "end")
            self.e[k].insert(0, v)
        self.auth_var.set("Senha" if auth == "password" else "Chave SSH")
        self.e_key.delete(0, "end")
        self.e_key.insert(0, key or DEFAULT_KEY)
        self.e_pass.delete(0, "end")
        self._auth_mode()
        self.status.configure(text="")
        self.btn_del.configure(state="normal")

    def _new(self):
        self.editing_id = None
        self.tree.selection_remove(self.tree.selection())
        self.title_lbl.configure(text="Novo servidor")
        for k, ent in self.e.items():
            ent.delete(0, "end")
        self.e["port"].insert(0, "22")
        self.e["user"].insert(0, "support")
        self.auth_var.set("Chave SSH")
        self.e_key.delete(0, "end")
        self.e_key.insert(0, DEFAULT_KEY)
        self.e_pass.delete(0, "end")
        self._auth_mode()
        self.status.configure(text="")
        self.btn_del.configure(state="disabled")

    def _auth_mode(self):
        if self.auth_var.get() == "Senha":
            self.key_row.grid_remove()
            self.e_pass.grid(row=self.pass_row, column=0, sticky="ew", padx=14, pady=(0, 6))
        else:
            self.e_pass.grid_remove()
            self.key_row.grid()

    def _pick_key(self):
        path = filedialog.askopenfilename(title="Chave SSH privada",
                                          initialdir=os.path.join(os.path.expanduser("~"), ".ssh"))
        if path:
            self.e_key.delete(0, "end")
            self.e_key.insert(0, path)

    # ------------------------------------------------------------------ ações
    def _values(self):
        name, host = self.e["name"].get().strip(), self.e["host"].get().strip()
        user = self.e["user"].get().strip()
        try:
            port = int(self.e["port"].get().strip() or 22)
        except ValueError:
            raise ValueError("Porta inválida.") from None
        if not (name and host and user):
            raise ValueError("Nome, IP/host e usuário são obrigatórios.")
        auth = "password" if self.auth_var.get() == "Senha" else "key"
        return name, host, port, user, auth, self.e_key.get().strip()

    def _save(self):
        try:
            name, host, port, user, auth, key = self._values()
        except ValueError as e:
            self.status.configure(text=str(e), text_color=theme.ERROR)
            return
        sid = self.db.save_server(name, host, port, user, auth, key, self.editing_id)
        note = ""
        if auth == "password" and self.e_pass.get():
            if not vault.set_password(sid, self.e_pass.get()):
                note = " (cofre do sistema indisponível: a senha vale só até fechar o app)"
        POOL.close(sid)            # dados mudaram: próxima conexão usa os novos
        self.refresh()
        self.tree.selection_set(str(sid))
        self.status.configure(text="Salvo." + note, text_color=theme.SUCCESS)
        if self.on_change:
            self.on_change()
        self.editing_id = sid
        self.after(300, self._test)          # checkup automático ao cadastrar/alterar

    def _delete(self):
        if not self.editing_id:
            return
        row = self.db.get_server(self.editing_id)
        if not messagebox.askyesno("Excluir servidor", f"Excluir o servidor {row[1]} ({row[2]})?"):
            return
        POOL.close(self.editing_id)
        vault.delete_password(self.editing_id)
        self.db.delete_server(self.editing_id)
        self._new()
        self.refresh()
        if self.on_change:
            self.on_change()

    def _test(self):
        try:
            name, host, port, user, auth, key = self._values()
        except ValueError as e:
            self.status.configure(text=str(e), text_color=theme.ERROR)
            return
        pwd = self.e_pass.get() or (vault.get_password(self.editing_id) if self.editing_id else "")
        self.btn_test.configure(state="disabled", text="Verificando...")
        self.status.configure(text=f"Conectando em {host}:{port} e verificando o servidor...",
                              text_color=theme.INFO)

        def run():
            from core.remote import Server, SshGateway, checkup_summary
            try:
                gw = SshGateway(Server(host, port, user, auth, key or DEFAULT_KEY, name, pwd or None)).connect()
                try:
                    items = gw.checkup()
                    has_ip = any(i["item"] == "ip" and i["ok"] for i in items)
                    nets = gw.networks() if has_ip else []
                finally:
                    gw.close()
                text, level = checkup_summary(items)
                good = [n for n in nets if n["suggested"]]
                head = {"ok": "Servidor pronto para uso.",
                        "aviso": "Servidor funciona, mas falta algo recomendado:",
                        "erro": "Falta algo obrigatório no servidor:"}[level]
                msg = (f"{head}\n{text}\n\nRedes privadas alcançáveis: {len(nets)}"
                       + (f" (ex.: {', '.join(n['cidr'] for n in good[:3])})" if good else ""))
                color = {"ok": theme.SUCCESS, "aviso": theme.WARNING, "erro": theme.ERROR}[level]
            except Exception as e:  # noqa: BLE001
                msg, color = f"✖ {e}", theme.ERROR
            self.after(0, lambda: (self.status.configure(text=msg, text_color=color),
                                   self.btn_test.configure(state="normal", text="Verificar servidor")))
        threading.Thread(target=run, daemon=True).start()
