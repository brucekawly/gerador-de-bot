import customtkinter as ctk
import tkinter.messagebox as messagebox
import sys
import os
import subprocess
import threading
import re

from gui import theme
from core.platform_utils import no_window_flags
from database.db_handler import DatabaseHandler
from gui.views.templates_view import TemplatesView
from gui.views.execution_view import ExecutionView

APP_VERSION = "1.3.0"

theme.setup()   # tema claro + cores e fonte do OLT Cloud

def install_browsers(browser_name, progress_callback, result_callback):
    try:
        cmd = [sys.executable, "-m", "playwright", "install", browser_name]
            
        # We need to capture the output to show progress
        process = subprocess.Popen(
            cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.STDOUT, 
            text=True,
            creationflags=no_window_flags()
        )
        
        # Regex to find percentage (e.g. " 45%")
        percent_re = re.compile(r"(\d+)%")
        
        while True:
            line = process.stdout.readline()
            if not line:
                break
                
            match = percent_re.search(line)
            if match:
                percent = int(match.group(1))
                progress_callback(percent / 100.0)
                
        process.wait()
        result_callback(process.returncode == 0)
    except Exception as e:
        print(f"Error installing browsers: {e}")
        result_callback(False)

class TR069ProvisionerApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Gerador de BOT")
        self.geometry("1440x860")
        self.minsize(1200, 700)
        
        # Database initialization
        self.db = DatabaseHandler()
        self._browsers_ok = set()

        # Grid configuration
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # ---- barra lateral
        self.sidebar_frame = ctk.CTkFrame(self, width=210, corner_radius=0, fg_color=theme.SIDEBAR, border_width=0)
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(4, weight=1)

        ctk.CTkLabel(self.sidebar_frame, text="Gerador de BOT", font=theme.font(19, "bold")).grid(
            row=0, column=0, padx=18, pady=(22, 2), sticky="w")
        ctk.CTkLabel(self.sidebar_frame, text=f"v{APP_VERSION} · ONTs e roteadores", text_color=theme.MUTED,
                     font=theme.font(11)).grid(row=1, column=0, padx=18, pady=(0, 18), sticky="w")

        self.nav = {}
        for r, (key, label) in enumerate((("templates", "Templates"), ("execution", "Execução em lote")), start=2):
            b = ctk.CTkButton(self.sidebar_frame, text=label, anchor="w", height=38, width=180,
                              fg_color="transparent", text_color=theme.TEXT_2,
                              hover_color=theme.HOVER, font=theme.font(13), corner_radius=8,
                              command=lambda k=key: self.show_view(k))
            b.grid(row=r, column=0, padx=14, pady=2, sticky="ew")
            self.nav[key] = b

        ctk.CTkLabel(self.sidebar_frame, text="Tema", text_color=theme.MUTED, font=theme.font(11)).grid(
            row=5, column=0, padx=18, sticky="w")
        self.appearance_mode_optionemenu = ctk.CTkOptionMenu(
            self.sidebar_frame, values=["Claro", "Escuro", "Sistema"], width=180,
            command=self.change_appearance_mode_event)
        self.appearance_mode_optionemenu.set("Claro")
        self.appearance_mode_optionemenu.grid(row=6, column=0, padx=14, pady=(2, 12))

        self.lbl_copyright = ctk.CTkLabel(self.sidebar_frame, text="© 2026 Bruce Kawly", font=theme.font(10),
                                          text_color=theme.MUTED)
        self.lbl_copyright.grid(row=7, column=0, padx=18, pady=(0, 16), sticky="w")

        # ---- conteúdo
        self.main_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.main_frame.grid(row=0, column=1, sticky="nsew", padx=22, pady=18)

        self.views = {
            "templates": TemplatesView(self.main_frame, self.db),
            "execution": ExecutionView(self.main_frame, self.db),
        }
        self.views["templates"].on_change = self.views["execution"].refresh_templates
        self.show_view("execution" if self.db.get_all_templates() else "templates")

    def change_appearance_mode_event(self, label: str):
        ctk.set_appearance_mode({"Escuro": "Dark", "Claro": "Light", "Sistema": "System"}[label])
        for v in self.views.values():
            if hasattr(v, "on_theme_change"):
                v.on_theme_change()

    def show_view(self, key):
        for v in self.views.values():
            v.pack_forget()
        if key == "execution":
            self.views["execution"].refresh_templates()
        self.views[key].pack(fill="both", expand=True)
        for k, b in self.nav.items():
            b.configure(fg_color=(theme.ACCENT_SOFT if k == key else "transparent"),
                        text_color=(theme.ACCENT_TEXT if k == key else theme.TEXT_2),
                        font=theme.font(13, "bold" if k == key else "normal"))

    # compatibilidade
    def show_templates(self):
        self.show_view("templates")

    def show_execution(self):
        self.show_view("execution")

    def download_browser_if_missing(self, browser_name="firefox", callback=None):
        """Confere se o navegador do Playwright está instalado; se não, oferece instalar (uma vez só)."""
        browser_name = browser_name.lower()
        if browser_name in self._browsers_ok:
            if callback:
                callback()
            return

        def check():
            ok = browser_installed(browser_name)
            self.after(0, lambda: on_checked(ok))

        def on_checked(ok):
            if ok:
                self._browsers_ok.add(browser_name)
                if callback:
                    callback()
                return
            display = browser_name.capitalize()
            if not messagebox.askyesno("Navegador não instalado",
                                       f"O navegador {display} do Playwright não está instalado.\n\n"
                                       "Baixar e instalar agora? (só precisa uma vez)"):
                return
            modal = ctk.CTkToplevel(self)
            modal.title(f"Instalando {display}")
            modal.geometry("400x150")
            modal.transient(self)
            modal.after(100, modal.grab_set)
            ctk.CTkLabel(modal, text=f"Baixando {display}...\nIsso pode levar alguns minutos.",
                         font=theme.font(14)).pack(pady=(20, 10))
            bar = ctk.CTkProgressBar(modal, width=300)
            bar.set(0)
            bar.pack(pady=10)
            pct = ctk.CTkLabel(modal, text="0%")
            pct.pack()

            def progress(val):
                self.after(0, lambda: (bar.set(val), pct.configure(text=f"{int(val * 100)}%")))

            def done(success):
                def ui():
                    modal.destroy()
                    if success:
                        self._browsers_ok.add(browser_name)
                        if callback:
                            callback()
                    else:
                        messagebox.showerror("Erro", f"Falha ao instalar o {display}.\n"
                                             f"Rode no terminal: python -m playwright install {browser_name}")
                self.after(0, ui)
            threading.Thread(target=install_browsers, args=(browser_name, progress, done), daemon=True).start()

        threading.Thread(target=check, daemon=True).start()


def browser_installed(name: str) -> bool:
    """True se o executável do navegador do Playwright existe nesta máquina."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            return os.path.exists(getattr(p, name).executable_path)
    except Exception:  # noqa: BLE001
        return False


if __name__ == "__main__":
    app = TR069ProvisionerApp()
    app.mainloop()
