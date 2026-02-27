import customtkinter as ctk
import tkinter.messagebox as messagebox
import sys
import os
import subprocess
import threading
import re

from database.db_handler import DatabaseHandler
from gui.views.templates_view import TemplatesView
from gui.views.execution_view import ExecutionView

# Settings for Playwright in Frozen EXE
if getattr(sys, 'frozen', False):
    # Use a local folder next to the EXE for portability
    executable_dir = os.path.dirname(sys.executable)
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = os.path.join(executable_dir, "ms-playwright")

ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")

def install_browsers(progress_callback, result_callback):
    try:
        # Use the playwright CLI to install only chromium
        if getattr(sys, 'frozen', False):
            from playwright._impl._driver import compute_driver_executable
            driver_executable, driver_cli = compute_driver_executable()
            cmd = [driver_executable, driver_cli, "install", "chromium"]
        else:
            cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
            
        # We need to capture the output to show progress
        process = subprocess.Popen(
            cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.STDOUT, 
            text=True,
            creationflags=0x08000000 if os.name == 'nt' else 0
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
        self.geometry("1400x800")
        
        # Check for Playwright browsers
        self.after(500, self.verify_browsers)

        # Database initialization
        self.db = DatabaseHandler()

        # Grid configuration
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Sidebar create
        self.sidebar_frame = ctk.CTkFrame(self, width=200, corner_radius=0)
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(4, weight=1)

        self.logo_label = ctk.CTkLabel(self.sidebar_frame, text="Gerador de BOT", font=ctk.CTkFont(size=20, weight="bold"))
        self.logo_label.grid(row=0, column=0, padx=20, pady=(20, 10))

        self.btn_templates = ctk.CTkButton(self.sidebar_frame, text="Gerenciador de Templates", command=self.show_templates)
        self.btn_templates.grid(row=1, column=0, padx=20, pady=10)

        self.btn_execution = ctk.CTkButton(self.sidebar_frame, text="Execução em Lote", command=self.show_execution)
        self.btn_execution.grid(row=2, column=0, padx=20, pady=10)

        self.appearance_mode_optionemenu = ctk.CTkOptionMenu(self.sidebar_frame, values=["Light", "Dark", "System"], command=self.change_appearance_mode_event)
        self.appearance_mode_optionemenu.grid(row=5, column=0, padx=20, pady=(10, 20))

        # Copyright Footer
        self.lbl_copyright = ctk.CTkLabel(self.sidebar_frame, text="© 2026 Bruce Kawly\nAll Rights Reserved", font=ctk.CTkFont(size=10, slant="italic"), text_color="gray")
        self.lbl_copyright.grid(row=6, column=0, padx=20, pady=(0, 20), sticky="s")

        # Main content frame
        self.main_frame = ctk.CTkFrame(self, corner_radius=10)
        self.main_frame.grid(row=0, column=1, sticky="nsew", padx=20, pady=20)

        # Initialize Views
        self.views = {
            "templates": TemplatesView(self.main_frame, self.db),
            "execution": ExecutionView(self.main_frame, self.db)
        }

        # Show default view
        self.show_templates()

    def change_appearance_mode_event(self, new_appearance_mode: str):
        ctk.set_appearance_mode(new_appearance_mode)

    def _hide_all_views(self):
        for view in self.views.values():
            view.pack_forget()

    def show_templates(self):
        self._hide_all_views()
        self.views["templates"].pack(fill="both", expand=True)
        # Change button color to active
        self.btn_templates.configure(fg_color=["#3B8ED0", "#1F6AA5"])
        self.btn_execution.configure(fg_color="transparent")

    def show_execution(self):
        self._hide_all_views()
        self.views["execution"].refresh_templates()
        self.views["execution"].pack(fill="both", expand=True)
        self.btn_execution.configure(fg_color=["#3B8ED0", "#1F6AA5"])
        self.btn_templates.configure(fg_color="transparent")

    def verify_browsers(self):
        if getattr(sys, 'frozen', False):
            browser_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
            # If path doesn't exist or is empty
            if not os.path.exists(browser_path) or not os.listdir(browser_path):
                if messagebox.askyesno("Configuração Inicial", 
                    "O motor de navegação (Chromium) não foi encontrado.\n\nDeseja baixar e configurar automaticamente agora?\n(Isso levará cerca de 1 min e é necessário apenas uma vez)"):
                    
                    progress_modal = ctk.CTkToplevel(self)
                    progress_modal.title("Baixando Motor...")
                    progress_modal.geometry("400x150")
                    progress_modal.transient(self)
                    progress_modal.grab_set()
                    
                    ctk.CTkLabel(progress_modal, text="Baixando Chromium...\nIsso pode levar alguns minutos.", font=ctk.CTkFont(size=14)).pack(pady=(20, 10))
                    
                    progress_bar = ctk.CTkProgressBar(progress_modal, width=300)
                    progress_bar.set(0)
                    progress_bar.pack(pady=10)
                    
                    lbl_percent = ctk.CTkLabel(progress_modal, text="0%")
                    lbl_percent.pack()
                    
                    def update_progress(val):
                        progress_bar.set(val)
                        lbl_percent.configure(text=f"{int(val*100)}%")
                        self.update_idletasks()
                    
                    def on_done(success):
                        progress_modal.destroy()
                        if success:
                            messagebox.showinfo("Sucesso", "Motor configurado! O app está pronto para uso.")
                        else:
                            messagebox.showerror("Erro", "Falha ao instalar motor. Verifique sua conexão.")

                    threading.Thread(target=install_browsers, args=(update_progress, on_done), daemon=True).start()

if __name__ == "__main__":
    app = TR069ProvisionerApp()
    app.mainloop()
