# Roadmap: ideias de recursos

Levantamento feito em 2026-11-25 comparando com ferramentas parecidas.

## O que existe no mercado

| Ferramenta | O que faz | Limite |
|---|---|---|
| Huawei HiConfig | Configuração em massa de roteadores Huawei via arquivo de configuração | Só Huawei; acesso sob pedido ao fabricante |
| TP-Link Aginet Config / Cudy ISP Preset | Pré-configuração em lote de CPEs do próprio fabricante | Só a marca |
| Intelbras Remotize, Parks Easy | Perfil do provedor / templates XML para ONUs da marca | Só a marca |
| ACS TR-069 (ex.: OLT Cloud) | Configuração remota padronizada | Só equipamentos com TR-069 ativo e alcançando o ACS |
| UiPath, UI.Vision, WebRPA, Robocorp | RPA genérico de navegador, alguns com editor visual e preenchimento por CSV | Não conhecem ONT/roteador (sem scanner, sem templates por modelo) |

**Diferencial do Gerador de BOT:** é **multimarca** e funciona pela interface web, inclusive onde não há TR-069. Quase todas as ferramentas de provedor são presas a uma marca.

## Prioridade alta

1. **Lote multimodelo automático:** o scanner já identifica o modelo e sugere o template. Falta deixar um único lote rodar **cada equipamento com o seu template**. É o recurso que nenhuma ferramenta de fabricante oferece.
2. **Modo leitura / auditoria:** templates que só **leem** (firmware, SSID, PPPoE, sinal óptico, uptime) e exportam uma planilha. Isso permite inventário antes de alterar e conferência depois.
3. **Backup antes de alterar:** baixar o arquivo de configuração do equipamento antes de aplicar o template, guardado junto do log.
4. **Verificação depois de salvar:** passo opcional que relê o valor (ex.: o SSID) e só marca sucesso se bater.
5. **Trace do Playwright nas falhas:** além do screenshot, salvar o *trace* (vídeo + DOM + rede, aberto com `playwright show-trace`). Ajuda muito a corrigir template.

## Prioridade média

6. **Seletores mais robustos no gravador:** preferir `get_by_role` / `get_by_label` / texto em vez de CSS frágil, com lista de alternativas por passo. Firmware novo muda menos coisas.
7. **Formato declarativo (JSON) + editor visual de passos** (`ir para`, `preencher`, `clicar`, `esperar`, `ler`). Resolve a segurança para templates de cliente no OLT Cloud (ver INTEGRACAO.md §4) e permite montar bots sem programar.
8. **Passos condicionais:** "se aparecer o assistente de primeiro acesso / troca de senha obrigatória, faça X". É comum em equipamento resetado.
9. **Atualização de firmware em massa** pela interface web (upload do arquivo + espera do reboot + verificação da versão).
10. **TR-069 primeiro, web como reserva:** se o equipamento já está no ACS do OLT Cloud, usar TR-069; o bot web entra só quando não houver.

## Prioridade baixa / avaliar

11. **Agendamento** (rodar de madrugada) e reexecução automática das falhas.
12. **IA como último recurso para seletor quebrado:** desligada por padrão. Se o modelo não tiver certeza, falha em vez de clicar no lugar errado.
13. **Mais sondas no scanner** (ZTE, Huawei, TP-Link, Intelbras), aprendidas com equipamentos reais de clientes.
14. **Fila com múltiplos agentes:** vários PCs/servidores pegando jobs da mesma fila.

## Fontes

- Huawei HiConfig: https://consumer.huawei.com/br/routers/hiconfig/
- TP-Link Aginet Config: https://www.tp-link.com/uk/solution/agile-config/
- Cudy ISP solutions: https://www.cudy.com/pages/isp-solutions
- Intelbras Remotize (manual): https://remotize.intelbras.com.br/manual/res/pdf/remotize.pdf
- TR-069 no NIC.br: https://minhaagenda.cursoseventos.nic.br/files/apresentacao/arquivo/1806/TR069.pdf
- IFPB, sistema de automação de ONUs multimarca: https://repositorio.ifpb.edu.br/bitstream/177683/4754/1/
- Gravadores/RPA: https://github.com/sauermar/web-browser-recorder · https://github.com/robocorp/Robocorp-Recorder · https://www.browserstack.com/guide/playwright-record
- Seletores auto-corrigíveis: https://qaskills.sh/blog/playwright-self-healing-locators-2026 · https://amrsa1.github.io/healwright · https://blog.devgenius.io/building-playwright-eir-a-self-healing-locator-engine-with-a-measured-0-false-heal-rate-70eac1c27bf7
