import customtkinter as ctk
import tkinter as tk
from tkinter import ttk, messagebox
import threading
import subprocess
import os
import sys
import json
from tkinter import filedialog
import re

class TemplatesView(ctk.CTkFrame):
    def __init__(self, master, db):
        super().__init__(master, corner_radius=10)
        self.db = db
        
        # Configure layout
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # Header
        self.header_frame = ctk.CTkFrame(self)
        self.header_frame.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="ew")
        
        ctk.CTkLabel(self.header_frame, text="Gerenciador de Receitas (Templates)", font=ctk.CTkFont(size=20, weight="bold")).pack(side="left", padx=10, pady=10)
        
        self.btn_new = ctk.CTkButton(self.header_frame, text="+ Novo Template", command=self.open_new_template_modal)
        self.btn_new.pack(side="right", padx=10, pady=10)
        
        self.btn_import = ctk.CTkButton(self.header_frame, text="📥 Importar", command=self.import_templates, fg_color="#1F6AA5", hover_color="#144870")
        self.btn_import.pack(side="right", padx=5, pady=10)
        
        self.btn_export = ctk.CTkButton(self.header_frame, text="📤 Exportar", command=self.export_templates, fg_color="#1F6AA5", hover_color="#144870")
        self.btn_export.pack(side="right", padx=5, pady=10)
        
        self.btn_edit = ctk.CTkButton(self.header_frame, text="✏️ Editar", command=self.edit_template, fg_color="#D1911B", hover_color="#9C6B11")
        self.btn_edit.pack(side="right", padx=5, pady=10)
        
        self.btn_delete = ctk.CTkButton(self.header_frame, text="🗑️ Deletar", command=self.delete_template, fg_color="#C93B3B", hover_color="#912828")
        self.btn_delete.pack(side="right", padx=10, pady=10)

        # Treeview (Table)
        self.tree_frame = ctk.CTkFrame(self)
        self.tree_frame.grid(row=1, column=0, padx=20, pady=10, sticky="nsew")
        
        # Using ttk.Treeview for tabular data (CTk lacks a native table view yet)
        style = ttk.Style()
        style.theme_use("default")
        style.configure("Treeview", background="#2a2d2e", foreground="white", rowheight=25, fieldbackground="#343638", bordercolor="#343638", borderwidth=0)
        style.map('Treeview', background=[('selected', '#22559b')])
        style.configure("Treeview.Heading", background="#565b5e", foreground="white", relief="flat")
        style.map("Treeview.Heading", background=[('active', '#3484F0')])

        self.tree = ttk.Treeview(self.tree_frame, columns=("ID", "Vendor", "Model", "Firmware", "Hardware"), show="headings")
        self.tree.heading("ID", text="ID", anchor="center")
        self.tree.heading("Vendor", text="Fabricante", anchor="w")
        self.tree.heading("Model", text="Modelo", anchor="w")
        self.tree.heading("Firmware", text="Firmware", anchor="w")
        self.tree.heading("Hardware", text="Hardware", anchor="w")
        
        self.tree.column("ID", width=50, minwidth=50, stretch=False, anchor="center")
        self.tree.column("Vendor", width=250, minwidth=150, stretch=False, anchor="w")
        self.tree.column("Model", width=250, minwidth=150, stretch=False, anchor="w")
        self.tree.column("Firmware", width=200, minwidth=150, stretch=False, anchor="w")
        self.tree.column("Hardware", width=200, minwidth=150, stretch=True, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=10, pady=10)

        self.refresh_table()

    def refresh_table(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for row in self.db.get_all_templates():
            self.tree.insert("", "end", values=row[:5])

    def delete_template(self):
        selected_item = self.tree.selection()
        if not selected_item:
            messagebox.showwarning("Aviso", "Selecione um template na lista para deletar.")
            return
            
        # Get values of selected row. Index 0 is the ID.
        item_values = self.tree.item(selected_item[0], "values")
        template_id = int(item_values[0])
        template_name = f"{item_values[1]} {item_values[2]}"
        
        confirm = messagebox.askyesno("Confirmar Exclusão", f"Tem certeza que deseja deletar o template:\n{template_name}?")
        if confirm:
            self.db.delete_template(template_id)
            self.refresh_table()
            messagebox.showinfo("Sucesso", "Template deletado com sucesso!")

    def edit_template(self):
        selected_item = self.tree.selection()
        if not selected_item:
            messagebox.showwarning("Aviso", "Selecione um template na lista para editar.")
            return
            
        item_values = self.tree.item(selected_item[0], "values")
        template_id = int(item_values[0])
        self.open_new_template_modal(template_id=template_id)

    def export_templates(self):
        templates = self.db.get_all_templates()
        if not templates:
            messagebox.showwarning("Aviso", "Não há templates para exportar.")
            return
            
        filepath = filedialog.asksaveasfilename(
            title="Exportar Templates",
            defaultextension=".json",
            filetypes=[("JSON Files", "*.json")]
        )
        if not filepath:
            return
            
        export_data = []
        for row in templates:
            export_data.append({
                "vendor": row[1],
                "model": row[2],
                "firmware": row[3],
                "hardware": row[4],
                "script": row[5]
            })
            
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, indent=4)
            messagebox.showinfo("Sucesso", f"{len(export_data)} templates exportados com sucesso!")
        except Exception as e:
            messagebox.showerror("Erro", f"Erro ao exportar: {str(e)}")

    def import_templates(self):
        filepath = filedialog.askopenfilename(
            title="Importar Templates",
            filetypes=[("JSON Files", "*.json")]
        )
        if not filepath:
            return
            
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                import_data = json.load(f)
                
            count = 0
            for item in import_data:
                if 'vendor' in item and 'model' in item and 'script' in item:
                    self.db.save_template(
                        item['vendor'],
                        item['model'],
                        item.get('firmware', ''),
                        item.get('hardware', ''),
                        item['script']
                    )
                    count += 1
            
            self.refresh_table()
            messagebox.showinfo("Sucesso", f"{count} templates importados com sucesso!")
        except Exception as e:
            messagebox.showerror("Erro", f"Erro ao importar: {str(e)}")

    def open_new_template_modal(self, template_id=None):
        modal = ctk.CTkToplevel(self)
        modal.title("Editar Template" if template_id else "Novo Template")
        modal.geometry("500x800")
        modal.transient(self.winfo_toplevel())
        modal.grab_set()

        title_text = "Editar Template" if template_id else "Adicionar Novo Template"
        ctk.CTkLabel(modal, text=title_text, font=ctk.CTkFont(size=18, weight="bold")).pack(pady=20)

        form_frame = ctk.CTkFrame(modal)
        form_frame.pack(fill="x", padx=20, pady=10)

        # Fields
        ctk.CTkLabel(form_frame, text="Fabricante:").grid(row=0, column=0, padx=10, pady=10, sticky="w")
        entry_vendor = ctk.CTkEntry(form_frame, width=250)
        entry_vendor.grid(row=0, column=1, padx=10, pady=10)

        ctk.CTkLabel(form_frame, text="Modelo:").grid(row=1, column=0, padx=10, pady=10, sticky="w")
        entry_model = ctk.CTkEntry(form_frame, width=250)
        entry_model.grid(row=1, column=1, padx=10, pady=10)

        ctk.CTkLabel(form_frame, text="Versão Firmware:").grid(row=2, column=0, padx=10, pady=10, sticky="w")
        entry_fw = ctk.CTkEntry(form_frame, width=250)
        entry_fw.grid(row=2, column=1, padx=10, pady=10)

        ctk.CTkLabel(form_frame, text="Versão Hardware:").grid(row=3, column=0, padx=10, pady=10, sticky="w")
        entry_hw = ctk.CTkEntry(form_frame, width=250)
        entry_hw.grid(row=3, column=1, padx=10, pady=10)

        # Code section
        ctk.CTkLabel(modal, text="Script de Automação (Python Playwright):").pack(anchor="w", padx=20, pady=(10, 0))
        text_script = ctk.CTkTextbox(modal, height=120)
        text_script.pack(fill="x", padx=20, pady=10)
        
        # Load existing
        if template_id:
            row = self.db.get_template(template_id)
            if row:
                entry_vendor.insert(0, row[1])
                entry_model.insert(0, row[2])
                entry_fw.insert(0, row[3] if row[3] else "")
                entry_hw.insert(0, row[4] if row[4] else "")
                text_script.insert("0.0", row[5])
        else:
            # Inject standard variable tips
            tip = '# Variáveis injetadas durante execução: {{IP}}, {{PORT}}\n# Ex: await page.goto(f"http://{{IP}}:{{PORT}}/")\n'
            text_script.insert("0.0", tip)

        # Instructions Section
        inst_frame = ctk.CTkFrame(modal, fg_color="#2b2b2b")
        inst_frame.pack(fill="x", padx=20, pady=5)
        
        inst_text = """COMO GRAVAR UMA NOVA RECEITA:
1. Clique no botão vermelho "Gravar Ações" abaixo.
2. Um navegador em branco e uma janela do "Playwright Inspector" irão abrir.
3. No navegador, digite o IP do roteador que está na sua mesa e dê Enter.
4. Faça o login normalmente e aplique as configurações devidas (ex: criar WAN, PPPOE, etc).
5. IMPORTANTE: Não clique em lugares desnecessários para não sujar o código.
6. Quando terminar e salvar as configs no roteador, FECHE O NAVEGADOR.
7. O código limpo aparecerá automaticamente na caixa abaixo!"""

        lbl_inst = ctk.CTkLabel(inst_frame, text=inst_text, justify="left", font=("Arial", 11))
        lbl_inst.pack(padx=10, pady=10, anchor="w")

        # Record Button Magic
        def launch_recorder():
            messagebox.showinfo("Gravador Iniciado", "Atenção:\n1. O navegador vai abrir agora.\n2. Faça a configuração no roteador.\n3. Feche o navegador para o código ser gerado aqui na tela.")
            
            def run_codegen():
                # Runs playwright CLI to record actions
                # Target asynchronous python target
                try:
                    # Creating a temporary file to hold the generated code
                    tmp_file = "template_rec.py"
                    
                    if getattr(sys, 'frozen', False):
                        # Se estiver rodando como EXE (PyInstaller)
                        from playwright._impl._driver import compute_driver_executable
                        driver_executable, driver_cli = compute_driver_executable()
                        cmd = [driver_executable, driver_cli, "codegen", "--target", "python-async", "-o", tmp_file]
                    else:
                        cmd = [sys.executable, "-m", "playwright", "codegen", "--target", "python-async", "-o", tmp_file]
                        
                    subprocess.run(cmd, check=True)
                    
                    if os.path.exists(tmp_file):
                        with open(tmp_file, "r", encoding="utf-8") as f:
                            code = f.read()
                        
                        # Clean up the playwright wrapper and keep only the actions inside the main function
                        # A bit hacky but works for the MVP
                        lines = code.splitlines()
                        action_lines = []
                        inside_func = False
                        
                        for line in lines:
                            if "def run(playwright: Playwright)" in line or "async def run(playwright" in line:
                                inside_func = True
                                continue
                                
                            if inside_func:
                                # A blank line or comment might have no indent but standard codegen uses 4 spaces.
                                # If we encounter a non-empty line that DOES NOT start with 4 spaces, the function is over.
                                if line.strip() and not line.startswith("    "):
                                    break
                                    
                                # Strip up to 4 spaces of indentation
                                cleaned_line = line[4:] if line.startswith("    ") else line
                                
                                # Skip playwright launching/context boilerplate usually at start/end
                                if any(skip in cleaned_line for skip in [
                                    "browser = await", "context = await", "page = await", 
                                    "await context.close()", "await browser.close()"
                                ]):
                                    continue
                                    
                                action_lines.append(cleaned_line)
                        
                        final_script = "\n".join(action_lines)
                        
                        # Sub the actual typed IP in the recording to {{IP}} template variable
                        final_script = re.sub(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b', '{{IP}}', final_script)
                        
                        # Update GUI safely from main thread using after()
                        def update_text():
                            text_script.delete("1.0", "end")
                            if final_script.strip():
                                text_script.insert("0.0", final_script)
                            else:
                                text_script.insert("0.0", "# Nenhuma ação gravada pelo Playwright. Certifique-se de realizar cliques na página antes de fechar.")
                            
                            try:
                                os.remove(tmp_file)
                            except:
                                pass
                        
                        modal.after(0, update_text)
                except Exception as e:
                    print(f"Error recording: {e}")
                    
            threading.Thread(target=run_codegen, daemon=True).start()

        btn_record = ctk.CTkButton(modal, text="🔴 Gravar Ações (Abrir Navegador)", command=launch_recorder, fg_color="#C93B3B", hover_color="#912828")
        btn_record.pack(pady=10)

        def save_template():
            v = entry_vendor.get()
            m = entry_model.get()
            s = text_script.get("1.0", "end-1c").strip()
            
            if not v or not m or len(s) < 5:
                messagebox.showerror("Erro", "Campos Fabricante, Modelo e Script são obrigatórios!")
                return
            
            if template_id:
                self.db.update_template(template_id, v, m, entry_fw.get(), entry_hw.get(), s)
                messagebox.showinfo("Sucesso", "Template atualizado com sucesso!")
            else:
                self.db.save_template(v, m, entry_fw.get(), entry_hw.get(), s)
                messagebox.showinfo("Sucesso", "Novo template cadastrado com sucesso!")
                
            self.refresh_table()
            modal.destroy()

        btn_save = ctk.CTkButton(modal, text="Salvar Template", command=save_template)
        btn_save.pack(pady=(10, 20))
