# Gerador de BOT - Provisionamento de Roteadores

O **Gerador de BOT** é uma ferramenta profissional desenvolvida em Python para automatizar a configuração em lote de roteadores e ONTs. Utilizando o motor do Playwright, o sistema permite "gravar" uma ação manual e replicá-la em centenas de equipamentos simultaneamente.

## ✨ Principais Funcionalidades

- **Gravação de Macros:** Grave suas ações no navegador e transforme-as em scripts de automação sem escrever uma linha de código.
- **Scanner de Rede Integrado:** Varredura rápida de IPs para identificar dispositivos online antes da execução.
- **Execução em Lote:** Importe planilhas (CSV/XLSX) e execute automações em múltiplos dispositivos ao mesmo tempo.
-  **Calculadora de RAM:** Monitoramento dinâmico do uso de memória com base no número de navegadores simultâneos.
- **Portabilidade Total:** Banco de dados e motores de navegação integrados na pasta do aplicativo.

## 🚀 Como Executar (Desenvolvedor)

1. Clone o repositório:
   ```bash
   git clone https://github.com/SEU_USUARIO/gerador-de-bot.git
   cd gerador-de-bot
   ```

2. Instale as dependências:
   ```bash
   pip install -r requirements.txt
   ```

3. Instale o motor do Playwright:
   ```bash
   playwright install chromium
   ```

4. Execute o aplicativo:
   ```bash
   python main.py
   ```

## 🛠️ Tecnologias Utilizadas

- **Python 3.12+**
- **CustomTkinter:** Interface moderna e responsiva.
- **Playwright:** Motor de automação web de alta performance.
- **SQLite:** Persistência de templates de forma local e leve.

## 📦 Compilação (.EXE)

Para gerar o executável portátil:
```bash
python build_exe.py
```
O resultado estará na pasta `dist/GeradorDeBOT`.

---
Desenvolvido por **Bruce Kawly**
