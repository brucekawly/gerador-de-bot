import PyInstaller.__main__
import os
import sys

# O Playwright precisa ser informado onde estão os navegadores
# Durante a compilação, vamos dizer ao PyInstaller para incluir a pasta do Playwright.
import playwright

# Encontra onde o playwright está instalado
playwright_path = os.path.dirname(playwright.__file__)

# Arquivo principal
main_script = "main.py"

# Ícone (opcional - usar o app_icon.ico se existir, senão nulo)
icon_arg = []
if os.path.exists("app_icon.ico"):
    icon_arg = ["--icon=app_icon.ico"]

import customtkinter
ctk_path = os.path.dirname(customtkinter.__file__)

# Rodar PyInstaller
PyInstaller.__main__.run([
    main_script,
    "--name=GeradorDeBOT",
    "--windowed", # Não mostrar tela preta de console
    "--noconfirm", # Sobrescrever pastas antigas
    "--clean", # Limpar cache
    
    # Adicionar todo o pacote CustomTkinter (necessário pois ele carrega as fontes dinamicamente)
    f"--add-data={ctk_path};customtkinter/",
    
    # Adicionar pacote playwright
    f"--add-data={playwright_path};playwright/",
    
    *icon_arg
])

print("Compilação PyInstaller finalizada! O executável está na pasta 'dist/GeradorDeBOT'")
