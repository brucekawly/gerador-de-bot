# 🤖 Gerador de BOT - Automação de Roteadores & ONTs

O **Gerador de BOT** é uma ferramenta robusta desenvolvida em Python para simplificar o provisionamento massivo de roteadores e ONTs (Optical Network Terminals). Através de uma interface moderna e intuitiva, você pode "ensinar" o sistema a configurar qualquer equipamento e replicar essa configuração em centenas de dispositivos automaticamente.

---

## 🔥 Por que usar o Gerador de BOT?

Em cenários de migração de tecnologia ou alteração de servidores (como TR-069), a configuração manual terminal a terminal é lenta e propensa a erros humanos. Este projeto resolve esse problema unindo a facilidade de uma interface visual com o poder do **Playwright** para automação web de ultra performance.

---

## 🚀 Funcionalidades Principais

### 1. 🎥 Gravador de Macros Inteligente
*   **Sem Código:** Clique em "Gravar Ações" e configure o roteador no navegador que abrirá. O sistema captura seus cliques, preenchimentos de formulários e salvamentos.
*   **Limpeza Automática:** O robô remove redundâncias do script gravado para garantir uma execução limpa.
*   **Variáveis Dinâmicas:** Substituição automática de IP, Porta, Usuário e Senha por variáveis `{{IP}}`, `{{PORT}}`, permitindo o uso do mesmo template para todo o seu parque de equipamentos.

### 2. ⚡ Scanner de Rede Integrado
*   **Filtro de Ativos:** Antes de iniciar a automação, escaneie a sua faixa de IPs.
*   **Performance:** Utiliza múltiplas threads para testar a porta web (80/443) de centenas de IPs simultaneamente.
*   **Fila Inteligente:** Adiciona apenas os dispositivos que estão realmente online à fila de execução, economizando tempo precioso.

### 3. 📊 Execução em Lote & Gestão de Recursos
*   **Importação de Planilhas:** Suporte total a arquivos `.csv` e `.xlsx`.
*   **Controle de Concorrência:** Slider dinâmico para definir quantos navegadores rodarão simultaneamente (de 1 a 15).
*   **Monitoramento de RAM:** O sistema estima em tempo real quanta memória o seu computador usará para a operação.
*   **Logs Detalhados:** Exportação dos resultados de sucesso ou erro para auditoria.

### 4. 🧳 Portabilidade Total
*   **EXE Standalone:** O aplicativo pode ser compilado em um executável portátil.
*   **Auto-Instalação:** Ao rodar pela primeira vez em um novo PC, o app baixa e configura o motor Chromium automaticamente dentro da própria pasta.
*   **Banco Local:** Seus templates ficam salvos em um arquivo `.db` portátil ao lado do executável.

---

## 🛠️ Instalação (Para Desenvolvedores)

1.  **Clone o projeto:**
    ```bash
    git clone https://github.com/SEU_USUARIO/gerador-de-bot.git
    cd gerador-de-bot
    ```

2.  **Instale as dependências:**
    ```bash
    pip install -r requirements.txt
    ```

3.  **Configure o motor do robô:**
    ```bash
    playwright install chromium
    ```

4.  **Inicie o aplicativo:**
    ```bash
    python main.py
    ```

---

## 📦 Como Gerar o Executável (.exe)

Para criar a versão portátil para sua equipe técnica:
1. Execute o script de build:
   ```bash
   python build_exe.py
   ```
2. A pasta final estará disponível em `dist/GeradorDeBOT`.

---

## 📖 Como Usar (Fluxo Básico)

1.  **Criar Template:** Vá em "Gerenciador de Templates" -> "+ Novo Template". Clique em "Gravar Ações", faça a configuração no roteador e feche o navegador. Salve o template.
2.  **Importar Dados:** Vá em "Execução em Lote", importe sua planilha de clientes.
3.  **Scanner (Opcional):** Use o "Scanner de Rede" para garantir que os equipamentos estão acessíveis.
4.  **Executar:** Selecione o template, ajuste o número de navegadores e clique em "Iniciar Onda".

---

## 🤝 Contribuições

Sinta-se à vontade para abrir **Issues** ou enviar **Pull Requests**. Sugestões de melhorias na interface ou novos módulos de scanner são sempre bem-vindas!

---

## 📜 Licença

Este projeto está sob a licença **MIT**. Veja o arquivo [LICENSE](LICENSE) para detalhes.

Desenvolvido com ❤️ por **Bruce Kawly**
