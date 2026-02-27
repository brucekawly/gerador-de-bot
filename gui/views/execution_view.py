import customtkinter as ctk
from tkinter import ttk, messagebox, filedialog
import threading
import asyncio
import pandas as pd
import json
import socket
from concurrent.futures import ThreadPoolExecutor

from core.automation_engine import AutomationEngine

class ExecutionView(ctk.CTkFrame):
    def __init__(self, master, db):
        super().__init__(master, corner_radius=10)
        self.db = db
        self.devices = []  # Will hold list of dicts: {'ip': '', 'port': '80', 'status': 'Pendente'}
        self.active_engine = None
        
        # Grid layout
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # 1. Config Header
        self.config_frame = ctk.CTkFrame(self)
        self.config_frame.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="ew")
        
        ctk.CTkLabel(self.config_frame, text="Execução em Lote", font=ctk.CTkFont(size=20, weight="bold")).grid(row=0, column=0, padx=10, pady=10, sticky="w")
        
        # Template selection
        self.template_var = ctk.StringVar(value="Selecione um Template")
        self.cb_templates = ctk.CTkOptionMenu(self.config_frame, variable=self.template_var, values=self._get_template_names(), width=300)
        self.cb_templates.grid(row=0, column=1, padx=20)
        
        # Concurrency scale
        ctk.CTkLabel(self.config_frame, text="Navegadores Simultâneos:").grid(row=0, column=2, padx=(20, 5))
        self.workers_var = ctk.IntVar(value=3)
        self.slider_workers = ctk.CTkSlider(self.config_frame, from_=1, to=15, variable=self.workers_var, number_of_steps=14, width=150, command=self._update_slider_label)
        self.slider_workers.grid(row=0, column=3, padx=5)
        self.lbl_workers = ctk.CTkLabel(self.config_frame, text="3 (~450MB RAM)")
        self.lbl_workers.grid(row=0, column=4, padx=5)

        # 2. Action Bar
        self.action_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.action_frame.grid(row=1, column=0, padx=20, pady=0, sticky="ew")
        
        self.btn_import = ctk.CTkButton(self.action_frame, text="📂 Importar Lista (CSV/Excel)", command=self.import_list)
        self.btn_import.pack(side="left", padx=5)
        
        self.btn_export = ctk.CTkButton(self.action_frame, text="📜 Exportar Log", command=self.export_log, fg_color="#565b5e", hover_color="#343638", state="disabled")
        self.btn_export.pack(side="left", padx=5)
        
        self.btn_range = ctk.CTkButton(self.action_frame, text="🌐 Inserir Range IP", command=self.open_ip_range_modal, fg_color="#1F6AA5", hover_color="#144870")
        self.btn_range.pack(side="left", padx=5)
        
        self.btn_scan = ctk.CTkButton(self.action_frame, text="📡 Scanner de Rede", command=self.open_ip_scanner_modal, fg_color="#6c3483", hover_color="#512e5f")
        self.btn_scan.pack(side="left", padx=5)
        
        self.btn_test = ctk.CTkButton(self.action_frame, text="🧪 Testar Único IP", command=self.open_test_modal, fg_color="#D1911B", hover_color="#9C6B11")
        self.btn_test.pack(side="left", padx=20)
        
        self.btn_play = ctk.CTkButton(self.action_frame, text="▶️ INICIAR AUTOMAÇÃO", command=self.start_execution, fg_color="#2EA043", hover_color="#238636")
        self.btn_play.pack(side="right", padx=5)
        
        self.btn_stop = ctk.CTkButton(self.action_frame, text="⏹️ PARAR", command=self.stop_execution, fg_color="#d12c2c", hover_color="#8f1919", width=100)
        self.btn_stop.pack(side="right", padx=5)
        self.btn_stop.pack_forget() # Hide initially

        # 3. Devices Table
        self.table_frame = ctk.CTkFrame(self)
        self.table_frame.grid(row=2, column=0, padx=20, pady=10, sticky="nsew")
        
        style = ttk.Style()
        style.configure("Treeview", background="#2a2d2e", foreground="white", rowheight=25, fieldbackground="#343638", bordercolor="#343638", borderwidth=0)
        self.tree = ttk.Treeview(self.table_frame, columns=("IP", "Porta", "Status"), show="headings")
        self.tree.heading("IP", text="Endereço IP / Host", anchor="w")
        self.tree.heading("Porta", text="Porta", anchor="center")
        self.tree.heading("Status", text="Status", anchor="w")
        
        self.tree.column("IP", width=150, minwidth=150, stretch=False, anchor="w")
        self.tree.column("Porta", width=100, minwidth=80, stretch=False, anchor="center")
        self.tree.column("Status", width=400, minwidth=200, stretch=True, anchor="w")
            
        self.tree.pack(fill="both", expand=True, padx=10, pady=10)

    def _update_slider_label(self, val):
        workers = int(val)
        ram_mb = workers * 150
        if ram_mb >= 1024:
            ram_str = f"{ram_mb / 1024:.1f}GB"
        else:
            ram_str = f"{ram_mb}MB"
        self.lbl_workers.configure(text=f"{workers} (~{ram_str} RAM)")

    def _get_template_names(self):
        templates = self.db.get_all_templates()
        if not templates:
            return ["Nenhum template cadastrado"]
        return [f"[{t[0]}] {t[1]} {t[2]}" for t in templates]
        
    def refresh_templates(self):
        new_values = self._get_template_names()
        self.cb_templates.configure(values=new_values)
        if new_values[0] != "Nenhum template cadastrado":
            self.cb_templates.set(new_values[-1])

    def import_list(self):
        # Refresh templates on import click just in case
        self.refresh_templates()
        
        filepath = filedialog.askopenfilename(
            title="Selecione a planilha",
            filetypes=(("CSV Files", "*.csv"), ("Excel Files", "*.xlsx"), ("All Files", "*.*"))
        )
        if not filepath:
            return
            
        try:
            if filepath.endswith('.csv'):
                df = pd.read_csv(filepath)
            else:
                df = pd.read_excel(filepath)
                
            # Expected columns: IP, Porta
            cols = df.columns.astype(str).str.lower()
            required = ['ip', 'porta']
            
            # Very basic validation, mapping expected columns
            mapped_df = pd.DataFrame()
            for req in required:
                match = [c for c in cols if req in c]
                if match:
                    mapped_df[req] = df.iloc[:, cols.tolist().index(match[0])].astype(str)
                else:
                    if req == 'porta':
                        mapped_df[req] = "80"
                    else:
                        mapped_df[req] = ""

            self.devices = []
            for item in self.tree.get_children():
                self.tree.delete(item)
                
            for _, row in mapped_df.iterrows():
                dev = {
                    "ip": row.get('ip', '').strip(),
                    "port": row.get('porta', '80').strip(),
                    "status": "Pendente"
                }
                if dev['ip'] and dev['ip'] != 'nan':
                    self.devices.append(dev)
                    self.tree.insert("", "end", values=(dev['ip'], dev['port'], dev['status']))
                    
            if self.devices:
                self.btn_export.configure(state="normal")
                    
        except Exception as e:
            messagebox.showerror("Erro de Importação", f"Erro ao ler arquivo: {str(e)}\nFormato esperado: colunas IP, Porta")

    def update_device_status(self, ip, message):
        # Update device internal list
        for dev in self.devices:
            if dev.get('ip') == ip:
                dev['status'] = str(message)
                break
                
        # Format for single-line treeview display
        clean_msg = str(message).replace('\n', ' | ').replace('\r', '')
        
        # Must be called from main thread ideally, or handled safely
        def _update():
            for item in self.tree.get_children():
                if self.tree.item(item, "values")[0] == ip:
                    vals = self.tree.item(item, "values")
                    self.tree.item(item, values=(vals[0], vals[1], clean_msg))
                    break
        self.after(0, _update)

    def export_log(self):
        if not self.devices:
            messagebox.showwarning("Aviso", "Nenhum dado para exportar.")
            return
            
        filepath = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV Files", "*.csv"), ("Text Files", "*.txt"), ("Excel Files", "*.xlsx")],
            title="Salvar Log de Execução"
        )
        if not filepath:
            return
            
        try:
            df = pd.DataFrame(self.devices)
            if filepath.endswith('.csv') or filepath.endswith('.txt'):
                df.to_csv(filepath, index=False, sep=';', encoding='utf-8')
            elif filepath.endswith('.xlsx'):
                df.to_excel(filepath, index=False)
                
            messagebox.showinfo("Sucesso", f"Log exportado com sucesso para:\n{filepath}")
        except Exception as e:
            messagebox.showerror("Erro", f"Falha ao exportar arquivo:\n{e}")

    def start_execution(self):
        if not self.devices:
            messagebox.showwarning("Aviso", "Importe uma lista de equipamentos primeiro.")
            return
            
        selected_text = self.template_var.get()
        if not selected_text or "Nenhum" in selected_text or "Selecione" in selected_text:
            messagebox.showwarning("Aviso", "Por favor, selecione um template do dropdown primeiro.")
            return
            
        template_id = int(selected_text.split("]")[0].replace("[", ""))
        template_row = self.db.get_template(template_id)
        
        if not template_row:
            messagebox.showerror("Erro", "Template não encontrado no banco.")
            return
            
        script = template_row[5] # actions_script column
        workers = int(self.workers_var.get())
        
        self.btn_play.pack_forget()
        self.btn_stop.pack(side="right", padx=5)
        self.btn_import.configure(state="disabled")
        self.btn_export.configure(state="disabled")
        self.slider_workers.configure(state="disabled")

        # Asyncio loop runner
        def run_async_loop():
            self.active_engine = AutomationEngine(max_concurrent=workers)
            
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            try:
                results = loop.run_until_complete(
                    self.active_engine.run_batch(self.devices, script, progress_callback=self.update_device_status)
                )
                
                # Execution finished
                def _finish():
                    self.btn_stop.pack_forget()
                    self.btn_play.pack(side="right", padx=5)
                    self.btn_play.configure(state="normal", text="▶️ INICIAR AUTOMAÇÃO")
                    self.btn_import.configure(state="normal")
                    self.btn_export.configure(state="normal")
                    messagebox.showinfo("Sucesso", "Todas as operações foram concluídas!")
                    self.slider_workers.configure(state="normal")
                    
                    success_count = sum(1 for r in results if isinstance(r, dict) and r.get('status') == 'success')
                    messagebox.showinfo("Finalizado", f"Execução concluída!\\nSucessos: {success_count} de {len(self.devices)}")
                
                self.after(0, _finish)
                
            except Exception as e:
                print(f"Loop error: {e}")
            finally:
                loop.close()
                self.active_engine = None

        # Start background thread for asyncio to not freeze tkinter GUI
        threading.Thread(target=run_async_loop, daemon=True).start()

    def stop_execution(self):
        if self.active_engine:
            self.active_engine.cancel()
            self.btn_stop.configure(state="disabled", text="Parando...")
            messagebox.showinfo("Cancelando", "Solicitação de parada enviada. Aguarde as execuções ativas finalizarem.")

    def open_ip_scanner_modal(self):
        modal = ctk.CTkToplevel(self)
        modal.title("Scanner de Rede (Apenas IPs Ativos)")
        modal.geometry("500x500")
        modal.transient(self.winfo_toplevel())
        modal.grab_set()
        
        ctk.CTkLabel(modal, text="Scanner de Rede Inteligente", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=20)
        ctk.CTkLabel(modal, text="Verifica quais IPs no Range possuem interface web ativa na porta informada.").pack(pady=(0, 20))
        
        form_frame = ctk.CTkFrame(modal)
        form_frame.pack(fill="x", padx=20, pady=10)
        
        ctk.CTkLabel(form_frame, text="IP Inicial:").grid(row=0, column=0, padx=10, pady=10, sticky="w")
        entry_start = ctk.CTkEntry(form_frame, width=200, placeholder_text="Ex: 192.168.1.100")
        entry_start.grid(row=0, column=1, padx=10, pady=10)
        
        ctk.CTkLabel(form_frame, text="IP Final:").grid(row=1, column=0, padx=10, pady=10, sticky="w")
        entry_end = ctk.CTkEntry(form_frame, width=200, placeholder_text="Ex: 192.168.1.254")
        entry_end.grid(row=1, column=1, padx=10, pady=10)
        
        ctk.CTkLabel(form_frame, text="Porta Base:").grid(row=2, column=0, padx=10, pady=10, sticky="w")
        entry_port = ctk.CTkEntry(form_frame, width=200)
        entry_port.insert(0, "80")
        entry_port.grid(row=2, column=1, padx=10, pady=10)

        lbl_progress = ctk.CTkLabel(modal, text="Aguardando início...", text_color="gray")
        lbl_progress.pack(pady=10)
        
        btn_action = ctk.CTkButton(modal, text="Iniciar Varredura")
        btn_action.pack(pady=10)

        def _check_port(ip, port):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1.5) # Fast timeout
                try:
                    s.connect((ip, int(port)))
                    return True
                except:
                    return False

        def scan_ips():
            import ipaddress
            start = entry_start.get().strip()
            end = entry_end.get().strip()
            port = entry_port.get().strip()
            
            try:
                start_ip = ipaddress.IPv4Address(start)
                end_ip = ipaddress.IPv4Address(end)
                if int(start_ip) > int(end_ip):
                    messagebox.showerror("Erro", "IP Inicial maior que o IP Final.")
                    return
                
                # Setup UI
                btn_action.configure(state="disabled", text="Escaneando...")
                lbl_progress.configure(text="Disparando pacotes...")
                
                # Generating pool
                ip_list = [str(ipaddress.IPv4Address(i)) for i in range(int(start_ip), int(end_ip) + 1)]
                total = len(ip_list)
                active_ips = []
                
                def _scan_thread():
                    count = 0
                    with ThreadPoolExecutor(max_workers=50) as executor:
                        # Map IPs to check_port future
                        future_to_ip = {executor.submit(_check_port, ip, port): ip for ip in ip_list}
                        for future in future_to_ip:
                            ip = future_to_ip[future]
                            try:
                                is_open = future.result()
                                if is_open:
                                    active_ips.append(ip)
                            except:
                                pass
                            
                            count += 1
                            lbl_progress.configure(text=f"Progresso: {count}/{total} - Encontrados: {len(active_ips)}")
                            self.update() # Force UI update safely
                            
                    # Finished scanning
                    def _sync_finish():
                        # Clear main list
                        self.devices = []
                        for item in self.tree.get_children():
                            self.tree.delete(item)
                            
                        # Add alive devices
                        for ip in active_ips:
                            dev = {"ip": ip, "port": port, "status": "Online"}
                            self.devices.append(dev)
                            self.tree.insert("", "end", values=(dev['ip'], dev['port'], dev['status']))
                            
                        if self.devices:
                            self.btn_export.configure(state="normal")
                            
                        messagebox.showinfo("Scanner Finalizado", f"Varredura concluída. {len(active_ips)} IPs ativos encontrados.")
                        modal.destroy()
                        
                    self.after(0, _sync_finish)
                    
                threading.Thread(target=_scan_thread, daemon=True).start()
                
            except Exception as e:
                messagebox.showerror("Erro de Formato", f"IPs inválidos: {str(e)}")
                btn_action.configure(state="normal", text="Iniciar Varredura")
                
        btn_action.configure(command=scan_ips)

    def open_ip_range_modal(self):
        modal = ctk.CTkToplevel(self)
        modal.title("Adicionar Range de IPs")
        modal.geometry("450x450")
        modal.transient(self.winfo_toplevel())
        modal.grab_set()
        
        ctk.CTkLabel(modal, text="Gerar Lista por Range", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=20)
        
        form_frame = ctk.CTkFrame(modal)
        form_frame.pack(fill="x", padx=20, pady=10)
        
        ctk.CTkLabel(form_frame, text="IP Inicial:").grid(row=0, column=0, padx=10, pady=10, sticky="w")
        entry_start = ctk.CTkEntry(form_frame, width=200, placeholder_text="Ex: 192.168.1.100")
        entry_start.grid(row=0, column=1, padx=10, pady=10)
        
        ctk.CTkLabel(form_frame, text="IP Final:").grid(row=1, column=0, padx=10, pady=10, sticky="w")
        entry_end = ctk.CTkEntry(form_frame, width=200, placeholder_text="Ex: 192.168.1.200")
        entry_end.grid(row=1, column=1, padx=10, pady=10)
        
        ctk.CTkLabel(form_frame, text="Porta:").grid(row=2, column=0, padx=10, pady=10, sticky="w")
        entry_port = ctk.CTkEntry(form_frame, width=200)
        entry_port.insert(0, "80")
        entry_port.grid(row=2, column=1, padx=10, pady=10)
        
        def generate_ips():
            import ipaddress
            start = entry_start.get().strip()
            end = entry_end.get().strip()
            
            try:
                start_ip = ipaddress.IPv4Address(start)
                end_ip = ipaddress.IPv4Address(end)
                
                if int(start_ip) > int(end_ip):
                    messagebox.showerror("Erro", "O IP Inicial não pode ser maior que o IP Final.")
                    return
                
                # Clear current table
                self.devices = []
                for item in self.tree.get_children():
                    self.tree.delete(item)
                    
                # Generate
                current_ip = int(start_ip)
                end_int = int(end_ip)
                port = entry_port.get().strip()
                
                count = 0
                while current_ip <= end_int:
                    ip_str = str(ipaddress.IPv4Address(current_ip))
                    dev = {
                        "ip": ip_str,
                        "port": port,
                        "status": "Pendente"
                    }
                    self.devices.append(dev)
                    self.tree.insert("", "end", values=(dev['ip'], dev['port'], dev['status']))
                    current_ip += 1
                    count += 1
                    
                messagebox.showinfo("Sucesso", f"{count} IPs gerados e adicionados à fila de execução!")
                modal.destroy()
                
            except ipaddress.AddressValueError:
                messagebox.showerror("Erro", "Formato de IP inválido. Use formato IPv4 (ex: 192.168.0.1).")
                
        btn_gen = ctk.CTkButton(modal, text="Gerar Fila", command=generate_ips)
        btn_gen.pack(pady=20)

    def open_test_modal(self):
        selected_text = self.template_var.get()
        if not selected_text or "Nenhum" in selected_text or "Selecione" in selected_text:
            messagebox.showwarning("Aviso", "Por favor, selecione um template válido antes de testar.")
            return
            
        template_id = int(selected_text.split("]")[0].replace("[", ""))
        template_row = self.db.get_template(template_id)
        if not template_row:
            return
            
        script = template_row[5]

        modal = ctk.CTkToplevel(self)
        modal.title("Testar Template Único")
        modal.geometry("400x500")
        modal.transient(self.winfo_toplevel())
        modal.grab_set()

        ctk.CTkLabel(modal, text="Teste Rápido de Automação", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=20)

        form_frame = ctk.CTkFrame(modal)
        form_frame.pack(fill="x", padx=20, pady=10)

        ctk.CTkLabel(form_frame, text="IP do Roteador:").grid(row=0, column=0, padx=10, pady=10, sticky="w")
        entry_ip = ctk.CTkEntry(form_frame, width=200)
        entry_ip.grid(row=0, column=1, padx=10, pady=10)

        ctk.CTkLabel(form_frame, text="Porta Web:").grid(row=1, column=0, padx=10, pady=10, sticky="w")
        entry_port = ctk.CTkEntry(form_frame, width=200)
        entry_port.insert(0, "80")
        entry_port.grid(row=1, column=1, padx=10, pady=10)
        
        lbl_status = ctk.CTkLabel(modal, text="Pronto para testar", text_color="yellow")
        lbl_status.pack(pady=10)

        def run_single_test():
            ip = entry_ip.get().strip()
            if not ip:
                messagebox.showerror("Erro", "O IP é obrigatório.")
                return
                
            btn_exec.configure(state="disabled", text="Executando...")
            lbl_status.configure(text="Iniciando navegador Playwright...", text_color="yellow")
            
            # Simple progress callback to update modal status
            def test_progress(test_ip, msg):
                def _upd():
                    lbl_status.configure(text=msg)
                modal.after(0, _upd)
            
            def async_runner():
                engine = AutomationEngine(max_concurrent=1)
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                # For single test, login is usually hardcoded in script by Codegen
                device = [{"ip": ip, "port": entry_port.get().strip()}]
                
                try:
                    result = loop.run_until_complete(
                        engine.run_batch(device, script, test_progress, visible=True)
                    )
                    
                    def _finish():
                        btn_exec.configure(state="normal", text="Executar Teste")
                        res = result[0]
                        if isinstance(res, dict) and res.get('status') == 'success':
                            lbl_status.configure(text="SUCESSO! Configuração aplicada.", text_color="#2EA043")
                            messagebox.showinfo("Sucesso", "O script rodou perfeitamente neste roteador!")
                        else:
                            lbl_status.configure(text="FALHA NA EXECUÇÃO", text_color="#C93B3B")
                            err_msg = res.get('message') if isinstance(res, dict) else str(res)
                            messagebox.showerror("Erro no Teste", f"Ocorreu um erro durante a execução:\n\n{err_msg}")
                    
                    modal.after(0, _finish)
                except Exception as e:
                    def _err():
                        btn_exec.configure(state="normal", text="Executar Teste")
                        lbl_status.configure(text="Erro crítico", text_color="#C93B3B")
                        messagebox.showerror("Exceção", str(e))
                    modal.after(0, _err)
                finally:
                    loop.close()
                    
            threading.Thread(target=async_runner, daemon=True).start()

        btn_exec = ctk.CTkButton(modal, text="Executar Teste", command=run_single_test, fg_color="#D1911B", hover_color="#9C6B11")
        btn_exec.pack(pady=10)
