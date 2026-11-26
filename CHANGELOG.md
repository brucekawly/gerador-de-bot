# Changelog

## v2.0.1 — 2026-11-26

### Scanner e criação de templates
- **Criar template direto do scanner:** selecione um equipamento e use **Criar template** (ou o botão direito). O editor abre preenchido com fabricante, modelo, firmware, porta, protocolo, endereço do equipamento e o mesmo servidor. O mesmo vale pelo botão direito da fila de execução.
- **Abrir no navegador** também como botão no rodapé do scanner.
- **Servidores e sistemas não são confundidos com equipamentos:** o scanner registra para onde a página redireciona. O OLT Cloud e servidores web comuns (página padrão do nginx/Apache, servidor Ubuntu/Debian) aparecem em cinza como "não é equipamento", ficam fora do "Adicionar todos" e avisam antes de criar template. Redirecionamento para domínio de fabricante (ex.: `tplinkwifi.net`) continua contando como equipamento.
- **Modelo e firmware lidos dos scripts da página de login** (`modelName`, `productName`, `softwareVersion`…), na página inteira. Ex.: TP-Link EX141 (Aginet).

### Gravador
- Avisa quando o endereço informado é do OLT Cloud (ou outro site) e não o IP do equipamento.

## v2.0.0 — 2026-11-25

Versão reescrita: motor mais rápido e confiável, scanner que identifica o modelo, acesso aos equipamentos pela rede do cliente e visual novo.

### Execução em lote
- **Um navegador por lote**, com um contexto isolado por equipamento: muito menos memória e mais rápido que abrir um navegador por IP.
- **Retry com espera crescente** para falhas passageiras, e **classificação das falhas** com mensagens claras: sem resposta, tempo esgotado, login recusado, erro na página e erro no template.
- **Screenshot de toda falha** (dois cliques na linha abre a imagem), filtros, **Reexecutar falhas** e relatório em Excel/CSV.
- **Várias credenciais por template**, testadas em ordem até o login funcionar. Usuário e senha da planilha têm prioridade naquele equipamento. Login HTTP Basic é detectado.
- **Colunas da planilha viram variáveis** `{{COLUNA}}` (SSID, SENHA_WIFI, VLAN…).
- **Valores padrão por template** (`PORT`, `SCHEME` e valores fixos) e a variável `{{BASE_URL}}`, para modelos com porta ou protocolo diferentes.
- **Segurança:** os valores das variáveis são lidos na hora da execução e nunca viram código Python.

### Scanner de rede
- Identifica **fabricante, modelo e firmware sem login** pela página web, UPnP, SNMP, sondas por fabricante (ex.: Fiberhome) e MAC, e **sugere o template** correspondente.
- **Varredura automática:** com o campo de faixas vazio, o app descobre as redes (interfaces, rotas, tabela ARP e CPEs conectadas ao ACS) e já marca as mais prováveis.
- Mostra também os aparelhos que estão na rede mas não têm web, e explica o que conferir quando não acha nada.

### Acesso pelo servidor do cliente (novo)
- Cadastro de **Servidores** (SSH com chave ou senha; a senha fica no cofre do sistema).
- **Acessar via servidor** no scanner, no gravador de template, em "abrir no navegador" e na execução do lote, usando um túnel SSH com proxy SOCKS5. Nada é instalado no servidor, e só rodam lá comandos de consulta.
- **Verificar servidor:** checkup do que é obrigatório, recomendado e opcional, com o que falta e como instalar.

### Templates
- Editor com validação de sintaxe, valores padrão, credenciais e gravação já apontando para o endereço do equipamento.
- O gravador troca automaticamente o endereço, o usuário e a senha gravados pelas variáveis.
- Aviso de segurança ao importar templates de terceiros.

### Plataformas e integração
- **Roda direto do Python em Windows, macOS e Linux** (`iniciar.bat` / `iniciar.sh`). Não há mais `.exe`.
- **Modo sem interface:** `python -m core run | scan | templates`, com saída em JSON e `--via` para sair pelo servidor do cliente.
- Guia de integração em `docs/INTEGRACAO.md` e ideias futuras em `docs/ROADMAP.md`.

### Interface
- Visual no padrão **OLT Cloud** (laranja, fonte Lato), **tema claro** por padrão, fluxo em passos e ícone novo.

### Testes
- 43 testes automáticos com roteador, servidor HTTP/SNMP e servidor SSH falsos locais.

### Atualizando da v1.x
- O banco de templates (`bot_templates.db`) é migrado sozinho na primeira abertura.
- Para rodar, instale o **Python 3.10+** e abra pelo `iniciar.bat` (Windows) ou `./iniciar.sh` (macOS/Linux).
