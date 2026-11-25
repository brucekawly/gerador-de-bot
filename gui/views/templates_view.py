"""Gerenciador de Templates (receitas de automação)."""
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from core.automation_engine import validate_template
from core.devices import (BUILTIN_VARS, Device, parse_credentials, parse_defaults, resolve_variables,
                          template_variables)
from gui import theme

NEW_TEMPLATE_TIP = """# Variáveis: {{BASE_URL}} (= {{SCHEME}}://{{IP}}:{{PORT}}) {{USERNAME}} {{PASSWORD}}
# Porta/protocolo padrão deste modelo: em "Valores padrão" ao lado (ex.: PORT=8080, SCHEME=https)
# + qualquer coluna da planilha, ex.: {{SSID}} {{SENHA_WIFI}} {{VLAN}}
# Login falhou de um jeito que o bot não percebe? use:  raise LoginError("motivo")
await page.goto("{{BASE_URL}}/")
"""

RECORD_HELP = """Como gravar:
1. Preencha abaixo o usuário/senha que você VAI digitar no roteador (o app troca por {{USERNAME}}/{{PASSWORD}}).
2. Clique em Gravar: abre um navegador + o Playwright Inspector.
3. Digite o IP do roteador, faça login e aplique as configurações (sem cliques desnecessários).
4. Feche o navegador: o código aparece no editor. O IP gravado vira {{IP}} automaticamente."""


def clean_recording(code: str, user: str = "", password: str = "") -> str:
    """Extrai só as ações do código do codegen e troca IP/usuário/senha por variáveis."""
    lines, out, inside = code.splitlines(), [], False
    for line in lines:
        if re.search(r"(async\s+)?def run\(playwright", line):
            inside = True
            continue
        if inside:
            if line.strip() and not line.startswith("    "):
                break
            cleaned = line[4:] if line.startswith("    ") else line
            if any(s in cleaned for s in ("browser = await", "context = await", "page = await",
                                          "await context.close()", "await browser.close()",
                                          "browser = playwright", "context = browser")):
                continue
            out.append(cleaned)
    script = "\n".join(out).strip("\n")
    # http(s)://IP[:porta] -> {{BASE_URL}} (porta/protocolo vêm dos valores padrão do template)
    script = re.sub(r"https?://(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?::\d+)?", "{{BASE_URL}}", script)
    script = re.sub(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?::(\d+))?\b",
                    lambda m: "{{IP}}:{{PORT}}" if m.group(1) else "{{IP}}", script)
    for value, var in ((password, "{{PASSWORD}}"), (user, "{{USERNAME}}")):
        if value:
            script = re.sub(r'(["\'])' + re.escape(value) + r'\1', lambda m: f"{m.group(1)}{var}{m.group(1)}", script)
    return script


class TemplatesView(ctk.CTkFrame):
    COLS = ("ID", "Vendor", "Model", "Firmware", "Hardware", "Acesso", "Vars", "Creds")
    HEAD = ("ID", "Fabricante", "Modelo", "Firmware", "Hardware", "Acesso", "Colunas da planilha", "Credenciais")
    WIDTH = (50, 140, 160, 140, 90, 110, 240, 90)

    def __init__(self, master, db):
        super().__init__(master, corner_radius=10, fg_color="transparent")
        self.db = db
        self.on_change = None   # callback para a tela de execução recarregar
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 10))
        ctk.CTkLabel(head, text="Templates", font=theme.font(22, "bold")).pack(side="left")
        ctk.CTkLabel(head, text="  receitas de automação por modelo de equipamento", text_color=theme.MUTED).pack(
            side="left", pady=(6, 0))
        theme.button(head, "+ Novo template", "primary", width=150,
                     command=self.open_new_template_modal).pack(side="right")
        theme.button(head, "Importar", width=90, command=self.import_templates).pack(side="right", padx=8)
        theme.button(head, "Exportar", width=90, command=self.export_templates).pack(side="right")

        card = theme.card(self)
        card.grid(row=1, column=0, sticky="nsew")
        card.grid_rowconfigure(0, weight=1)
        card.grid_columnconfigure(0, weight=1)
        theme.apply_tree_style()
        self.tree = ttk.Treeview(card, columns=self.COLS, show="headings", selectmode="browse")
        for c, h, w in zip(self.COLS, self.HEAD, self.WIDTH):
            self.tree.heading(c, text=h, anchor="w")
            self.tree.column(c, width=w, minwidth=40, anchor="w", stretch=(c == "Vars"))
        sb = ttk.Scrollbar(card, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(10, 0), pady=10)
        sb.grid(row=0, column=1, sticky="ns", pady=10, padx=(0, 6))
        self.tree.bind("<Double-1>", lambda e: self.edit_template())
        self.tree.bind("<Delete>", lambda e: self.delete_template())
        self.tree.bind("<Button-3>", self._menu)

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        ctk.CTkLabel(foot, text="Dois cliques para editar · botão direito para mais opções",
                     text_color=theme.MUTED, font=theme.font(11)).pack(side="left")
        theme.button(foot, "Excluir", "ghost", width=80, text_color=theme.ERROR,
                     command=self.delete_template).pack(side="right")
        theme.button(foot, "Duplicar", "ghost", width=80, command=self.duplicate_template).pack(side="right")
        theme.button(foot, "Editar", "ghost", width=80, command=self.edit_template).pack(side="right")

        self.empty = ctk.CTkLabel(card, text="Nenhum template ainda.\nClique em + Novo template e grave as ações "
                                             "num roteador de referência.", text_color=theme.MUTED)
        self.refresh_table()

    def on_theme_change(self):
        theme.apply_tree_style()

    def _changed(self):
        self.refresh_table()
        if self.on_change:
            self.on_change()

    def refresh_table(self):
        self.tree.delete(*self.tree.get_children())
        rows = self.db.get_all_templates()
        for r in rows:
            custom = sorted(template_variables(r[5]) - BUILTIN_VARS)
            n_creds = len(parse_credentials(r[6]))
            d = resolve_variables(Device("x"), None, parse_defaults(r[7]))
            custom = sorted(set(custom) - set(parse_defaults(r[7])))
            self.tree.insert("", "end", iid=str(r[0]), values=(
                r[0], r[1], r[2], r[3] or "", r[4] or "", f"{d['SCHEME']} :{d['PORT']}", ", ".join(custom) or "—",
                n_creds or "admin/admin"))
        if rows:
            self.empty.place_forget()
        else:
            self.empty.place(relx=0.5, rely=0.5, anchor="center")

    def _selected_id(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Templates", "Selecione um template na lista.")
            return None
        return int(sel[0])

    def _menu(self, event):
        import tkinter as tk
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        self.tree.selection_set(iid)
        m = tk.Menu(self, tearoff=0)
        m.add_command(label="Editar", command=self.edit_template)
        m.add_command(label="Duplicar (nova versão de firmware)", command=self.duplicate_template)
        m.add_separator()
        m.add_command(label="Excluir", command=self.delete_template)
        m.tk_popup(event.x_root, event.y_root)

    def delete_template(self):
        tid = self._selected_id()
        if tid is None:
            return
        r = self.db.get_template(tid)
        if messagebox.askyesno("Excluir template", f"Excluir o template [{tid}] {r[1]} {r[2]}?\n"
                                                   "Essa ação não pode ser desfeita."):
            self.db.delete_template(tid)
            self._changed()

    def edit_template(self):
        tid = self._selected_id()
        if tid is not None:
            self.open_new_template_modal(template_id=tid)

    def duplicate_template(self):
        tid = self._selected_id()
        if tid is None:
            return
        r = self.db.get_template(tid)
        new_id = self.db.save_template(r[1], r[2] + " (cópia)", r[3], r[4], r[5], r[6], r[7])
        self._changed()
        self.tree.selection_set(str(new_id))
        self.open_new_template_modal(template_id=new_id)

    # ------------------------------------------------------------------ importar / exportar
    def export_templates(self):
        rows = self.db.get_all_templates()
        if not rows:
            messagebox.showwarning("Exportar", "Não há templates para exportar.")
            return
        path = filedialog.asksaveasfilename(title="Exportar templates", defaultextension=".json",
                                            filetypes=[("JSON", "*.json")])
        if not path:
            return
        with_creds = any(r[6] for r in rows) and messagebox.askyesno(
            "Exportar", "Incluir as credenciais (usuário/senha) no arquivo?\n\n"
                        "Escolha Não se for compartilhar o arquivo com outras pessoas.")
        data = [{"vendor": r[1], "model": r[2], "firmware": r[3], "hardware": r[4], "script": r[5],
                 **({"defaults": r[7]} if r[7] else {}),
                 **({"credentials": r[6]} if with_creds and r[6] else {})} for r in rows]
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            messagebox.showinfo("Exportar", f"{len(data)} template(s) exportado(s).")
        except OSError as e:
            messagebox.showerror("Exportar", f"Erro ao exportar: {e}")

    def import_templates(self):
        path = filedialog.askopenfilename(title="Importar templates", filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            items = [i for i in data if isinstance(i, dict) and i.get("vendor") and i.get("model") and i.get("script")]
        except (OSError, ValueError) as e:
            messagebox.showerror("Importar", f"Arquivo inválido: {e}")
            return
        if not items:
            messagebox.showwarning("Importar", "Nenhum template válido no arquivo.")
            return
        names = "\n".join(f"• {i['vendor']} {i['model']} {i.get('firmware') or ''}" for i in items[:15])
        broken = [i for i in items if validate_template(i["script"])]
        if not messagebox.askyesno(
                "Importar templates",
                f"{len(items)} template(s):\n{names}{'…' if len(items) > 15 else ''}\n\n"
                "⚠ Templates são código Python e rodam no seu computador com as suas permissões.\n"
                "Só importe arquivos de fontes em que você confia." +
                (f"\n\n{len(broken)} deles têm erro de sintaxe (serão importados para você corrigir)." if broken else "") +
                "\n\nImportar?", icon="warning"):
            return
        for i in items:
            self.db.save_template(i["vendor"], i["model"], i.get("firmware", ""), i.get("hardware", ""),
                                  i["script"], i.get("credentials", ""), i.get("defaults", ""))
        self._changed()
        messagebox.showinfo("Importar", f"{len(items)} template(s) importado(s).")

    # ------------------------------------------------------------------ editor
    def open_new_template_modal(self, template_id=None):
        row = self.db.get_template(template_id) if template_id else None
        modal = ctk.CTkToplevel(self)
        modal.title("Editar template" if row else "Novo template")
        modal.geometry("1040x780")
        modal.minsize(820, 640)
        modal.transient(self.winfo_toplevel())
        modal.after(100, modal.grab_set)
        modal.grid_columnconfigure(0, weight=3)
        modal.grid_columnconfigure(1, weight=2)
        modal.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(modal, text=(f"Editar template [{row[0]}]" if row else "Novo template"),
                     font=theme.font(20, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", padx=20, pady=(16, 8))

        # ---- coluna esquerda: código
        left = ctk.CTkFrame(modal, fg_color="transparent")
        left.grid(row=1, column=0, sticky="nsew", padx=(20, 8))
        left.grid_rowconfigure(2, weight=1)
        left.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(left, text="Script de automação (Python + Playwright)", font=theme.font(13, "bold")).grid(
            row=0, column=0, sticky="w")
        chips = ctk.CTkFrame(left, fg_color="transparent")
        chips.grid(row=1, column=0, sticky="w", pady=4)
        text = ctk.CTkTextbox(left, font=("Consolas", 13), wrap="none", undo=True)
        text.grid(row=2, column=0, sticky="nsew")
        for var in ("BASE_URL", "IP", "PORT", "USERNAME", "PASSWORD"):
            ctk.CTkButton(chips, text="{{%s}}" % var, width=20, height=24, font=("Consolas", 11),
                          fg_color=theme.CARD, text_color=theme.INFO, hover_color=theme.CARD_BORDER,
                          border_width=1, border_color=theme.CARD_BORDER,
                          command=lambda v=var: text.insert("insert", "{{%s}}" % v)).pack(side="left", padx=(0, 4))
        ctk.CTkLabel(chips, text="  + colunas da planilha: {{SSID}}, {{VLAN}}…", text_color=theme.MUTED,
                     font=theme.font(11)).pack(side="left")
        status = ctk.CTkLabel(left, text="", anchor="w")
        status.grid(row=3, column=0, sticky="ew", pady=(4, 0))

        def check(_=None):
            err = validate_template(text.get("1.0", "end-1c"))
            custom = sorted(template_variables(text.get("1.0", "end-1c")) - BUILTIN_VARS)
            if err:
                status.configure(text=f"✖ Erro de sintaxe: {err}", text_color=theme.ERROR)
            else:
                status.configure(text="✔ Sintaxe OK" + (f" · variáveis da planilha: {', '.join(custom)}" if custom else ""),
                                 text_color=theme.SUCCESS)
            return err
        text.bind("<KeyRelease>", lambda e: modal.after_idle(check))

        # ---- coluna direita: dados + credenciais + gravação
        right = ctk.CTkScrollableFrame(modal, fg_color="transparent")
        right.grid(row=1, column=1, sticky="nsew", padx=(8, 20))
        info = theme.card(right)
        info.pack(fill="x")
        entries = {}
        for i, (key, lbl) in enumerate((("vendor", "Fabricante *"), ("model", "Modelo *"),
                                        ("fw", "Versão de firmware"), ("hw", "Versão de hardware"))):
            ctk.CTkLabel(info, text=lbl, text_color=theme.MUTED, font=theme.font(11)).grid(
                row=i * 2, column=0, sticky="w", padx=12, pady=(8 if i == 0 else 2, 0))
            e = ctk.CTkEntry(info)
            e.grid(row=i * 2 + 1, column=0, sticky="ew", padx=12, pady=(0, 6))
            entries[key] = e
        info.grid_columnconfigure(0, weight=1)

        def_card = theme.card(right)
        def_card.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(def_card, text="Valores padrão (NOME=valor por linha)", font=theme.font(12, "bold")).pack(
            anchor="w", padx=12, pady=(8, 0))
        ctk.CTkLabel(def_card, text="Porta e protocolo de acesso deste modelo e valores fixos usados no script.\n"
                                    "A planilha/scanner sobrescreve por equipamento.",
                     text_color=theme.MUTED, font=theme.font(11), justify="left", wraplength=235).pack(anchor="w", padx=12)
        defaults_box = ctk.CTkTextbox(def_card, height=80, font=("Consolas", 12))
        defaults_box.pack(fill="x", padx=12, pady=(4, 10))

        cred_card = theme.card(right)
        cred_card.pack(fill="x", pady=10)
        ctk.CTkLabel(cred_card, text="Credenciais (uma por linha  usuario:senha)",
                     font=theme.font(12, "bold")).pack(anchor="w", padx=12, pady=(8, 0))
        ctk.CTkLabel(cred_card, text="O bot tenta em ordem até o login funcionar.", text_color=theme.MUTED,
                     font=theme.font(11)).pack(anchor="w", padx=12)
        creds_box = ctk.CTkTextbox(cred_card, height=90, font=("Consolas", 12))
        creds_box.pack(fill="x", padx=12, pady=(4, 10))

        rec = theme.card(right)
        rec.pack(fill="x")
        ctk.CTkLabel(rec, text="Gravar ações num roteador", font=theme.font(12, "bold")).pack(
            anchor="w", padx=12, pady=(8, 0))
        ctk.CTkLabel(rec, text=RECORD_HELP, justify="left", wraplength=235, anchor="w", text_color=theme.MUTED,
                     font=theme.font(11)).pack(anchor="w", padx=12, pady=4)
        rr = ctk.CTkFrame(rec, fg_color="transparent")
        rr.pack(fill="x", padx=12)
        rec_user = ctk.CTkEntry(rr, placeholder_text="usuário digitado", width=135)
        rec_user.pack(side="left")
        rec_pass = ctk.CTkEntry(rr, placeholder_text="senha digitada", width=135, show="•")
        rec_pass.pack(side="left", padx=6)
        rb = ctk.CTkFrame(rec, fg_color="transparent")
        rb.pack(fill="x", padx=12, pady=8)
        rec_browser = ctk.StringVar(value="Chromium")
        ctk.CTkOptionMenu(rb, variable=rec_browser, values=["Chromium", "Firefox", "WebKit"], width=110).pack(side="left")
        btn_rec = theme.button(rb, "Gravar", "danger", width=110, height=32)
        btn_rec.pack(side="left", padx=8)

        # ---- carregar
        if row:
            for k, i in (("vendor", 1), ("model", 2), ("fw", 3), ("hw", 4)):
                entries[k].insert(0, row[i] or "")
            text.insert("1.0", row[5])
            creds_box.insert("1.0", row[6] or "")
            defaults_box.insert("1.0", row[7] or "")
        else:
            text.insert("1.0", NEW_TEMPLATE_TIP)
            creds_box.insert("1.0", "admin:admin\n")
            defaults_box.insert("1.0", "PORT=80\nSCHEME=http\n# PORTA_REMOTA=8080\n")
        check()

        def launch_recorder():
            browser = rec_browser.get().lower()

            def record():
                fd, tmp = tempfile.mkstemp(suffix=".py", prefix="gravacao_")
                os.close(fd)
                try:
                    cmd = [sys.executable, "-m", "playwright", "codegen", "-b", browser,
                           "--target", "python-async", "-o", tmp]
                    subprocess.run(cmd, check=False)
                    with open(tmp, encoding="utf-8") as f:
                        code = f.read()
                except Exception as e:  # noqa: BLE001
                    modal.after(0, lambda: messagebox.showerror("Gravação", f"Falha ao gravar: {e}", parent=modal))
                    return
                finally:
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
                script = clean_recording(code, rec_user.get().strip(), rec_pass.get())

                def put():
                    if not modal.winfo_exists():
                        return
                    btn_rec.configure(state="normal", text="Gravar")
                    if not script.strip():
                        messagebox.showwarning("Gravação", "Nenhuma ação foi gravada.", parent=modal)
                        return
                    if text.get("1.0", "end-1c").strip() not in ("", NEW_TEMPLATE_TIP.strip()) and not \
                            messagebox.askyesno("Gravação", "Substituir o script atual pela gravação?\n"
                                                "(Não = adicionar ao final)", parent=modal):
                        text.insert("end", "\n" + script)
                    else:
                        text.delete("1.0", "end")
                        text.insert("1.0", script)
                    check()
                modal.after(0, put)

            btn_rec.configure(state="disabled", text="Gravando...")
            app = self.winfo_toplevel()
            start = lambda: threading.Thread(target=record, daemon=True).start()  # noqa: E731
            if hasattr(app, "download_browser_if_missing"):
                app.download_browser_if_missing(browser, callback=start)
            else:
                start()
        btn_rec.configure(command=launch_recorder)

        def save():
            v, m = entries["vendor"].get().strip(), entries["model"].get().strip()
            script = text.get("1.0", "end-1c").strip()
            if not v or not m or len(script) < 5:
                messagebox.showerror("Salvar", "Fabricante, Modelo e Script são obrigatórios.", parent=modal)
                return
            err = check()
            if err and not messagebox.askyesno("Salvar", f"O script tem erro de sintaxe:\n{err}\n\nSalvar mesmo assim?",
                                               parent=modal):
                return
            creds = creds_box.get("1.0", "end").strip()
            uses_login = any(x in script for x in ("{{USERNAME}}", "{{PASSWORD}}"))
            if not uses_login and re.search(r"\.fill\([^)]*(pass|senha|pwd)", script, re.I) and not messagebox.askyesno(
                    "Salvar", "O script preenche um campo de senha com valor fixo e não usa {{PASSWORD}}.\n"
                              "Assim a lista de credenciais não terá efeito.\n\nSalvar mesmo assim?", parent=modal):
                return
            fw, hw = entries["fw"].get().strip(), entries["hw"].get().strip()
            if row:
                self.db.update_template(row[0], v, m, fw, hw, script, creds, defaults_box.get("1.0", "end").strip())
            else:
                self.db.save_template(v, m, fw, hw, script, creds, defaults_box.get("1.0", "end").strip())
            self._changed()
            modal.destroy()

        bar = ctk.CTkFrame(modal, fg_color="transparent")
        bar.grid(row=2, column=0, columnspan=2, sticky="ew", padx=20, pady=14)
        theme.button(bar, "Salvar template", "primary", width=160, command=save).pack(side="right")
        theme.button(bar, "Cancelar", width=100, command=modal.destroy).pack(side="right", padx=8)
        modal.bind("<Control-s>", lambda e: save())
