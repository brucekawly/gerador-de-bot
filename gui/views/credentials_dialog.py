"""Editor da lista de credenciais de um template (uma por linha, usuario:senha)."""
import customtkinter as ctk

from core.devices import parse_credentials
from gui import theme

HELP = ("Uma credencial por linha no formato  usuario:senha  (também aceita ; ou TAB).\n"
        "O bot tenta na ordem até o login funcionar. Linhas com # são ignoradas.\n"
        "No template, use {{USERNAME}} e {{PASSWORD}} onde o login é digitado.\n"
        "Se a planilha tiver colunas Usuário/Senha, elas têm prioridade naquele equipamento.")


class CredentialsDialog(ctk.CTkToplevel):
    def __init__(self, master, title, current_text, on_save):
        super().__init__(master)
        self.title(f"Credenciais · {title}")
        self.geometry("520x460")
        self.transient(master.winfo_toplevel())
        self.on_save = on_save
        ctk.CTkLabel(self, text="Credenciais de acesso", font=theme.font(18, "bold")).pack(anchor="w", padx=20,
                                                                                          pady=(18, 2))
        ctk.CTkLabel(self, text=title, text_color=theme.MUTED).pack(anchor="w", padx=20)
        ctk.CTkLabel(self, text=HELP, justify="left", text_color=theme.MUTED, font=theme.font(11),
                     wraplength=480).pack(anchor="w", padx=20, pady=(10, 6))
        self.box = ctk.CTkTextbox(self, height=200, font=("Consolas", 13))
        self.box.pack(fill="both", expand=True, padx=20)
        self.box.insert("1.0", current_text or "admin:admin\n")
        self.lbl = ctk.CTkLabel(self, text="", text_color=theme.MUTED)
        self.lbl.pack(anchor="w", padx=20, pady=4)
        self.box.bind("<KeyRelease>", lambda e: self._count())
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=20, pady=(4, 16))
        theme.button(bar, "Salvar", "primary", width=120, command=self._save).pack(side="right")
        theme.button(bar, "Cancelar", width=100, command=self.destroy).pack(side="right", padx=8)
        ctk.CTkLabel(bar, text="Salvas sem criptografia no banco local.", text_color=theme.WARNING,
                     font=theme.font(11)).pack(side="left")
        self._count()
        self.after(100, self.grab_set)

    def _count(self):
        n = len(parse_credentials(self.box.get("1.0", "end")))
        self.lbl.configure(text=f"{n} credencial(is)")

    def _save(self):
        self.on_save(self.box.get("1.0", "end").strip())
        self.destroy()
