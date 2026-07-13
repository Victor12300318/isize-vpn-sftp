# Guia de Automação Integrada: SFTP (Proxy FortiClient) para PostgreSQL

Este guia consolida o plano de infraestrutura de rede (Docker + FortiClient VPN) e o script Python de alta resiliência (`atualiza_banco.py`) totalmente containerizado e integrado com alertas para seu n8n.

A integração une a segurança de download via VPN (através de um proxy isolado SOCKS5) à flexibilidade do script original, que faz o mapeamento dinâmico de colunas e atualiza diretamente o banco PostgreSQL externo sem interferir na conectividade da VPS.

---

## 🏗️ Resumo da Arquitetura

1. **Isolamento de VPN (Docker + FortiClient)**: Um container conecta-se à VPN corporativa e expõe um proxy SOCKS5 localmente. Se a VPN oscilar, apenas o tráfego do proxy é afetado; a VPS continua 100% online na rede pública.
2. **Download Resiliente (SFTP via Proxy)**: O script Python (`atualiza_banco.py`) roda dentro de seu próprio container Docker e usa o DNS interno (`forticlient:1080`) para conectar-se ao SFTP remoto via SOCKS5, baixando arquivos Excel novos.
3. **Atualização Direta do Postgres**: Uma vez baixados os arquivos, o proxy é desativado para a conexão do banco de dados. O script se conecta diretamente ao banco PostgreSQL usando `SQLAlchemy` e realiza um **UPSERT dinâmico**:
   - Cria uma tabela temporária de staging (`isize_temp`).
   - Mapeia e valida apenas as colunas que realmente existem no banco final (`isize`), descartando colunas extras da planilha de forma dinâmica.
   - Executa a query de sincronização (Insere novos registros e atualiza os existentes com base na coluna chave `Proposta_iSize`).
4. **Controle de Histórico**: Um arquivo local de histórico (`data/historico_arquivos_lidos.txt`) montado em volume garante que cada arquivo do SFTP seja processado **exatamente uma vez**.
5. **Monitoramento e Alertas (Webhook n8n)**: Qualquer falha crítica de conexão, autenticação ou leitura de banco enviará imediatamente um payload JSON para o webhook configurado no n8n.

---

## 🔑 PASSO 1: Configurar as Credenciais (`.env`)

Crie ou configure o arquivo `.env` no diretório do projeto com os seguintes parâmetros:

```env
# 1. Configurações da VPN (FortiClient)
VPN_ADDR=sua_vpn.empresa.com:10443
VPN_USER=seu_usuario_vpn
VPN_PASS=sua_senha_vpn

# 2. Configurações do Servidor SFTP (Remoto via VPN)
SFTP_HOST=seu_sftp.empresa.com
SFTP_PORT=22
SFTP_USER=seu_usuario_sftp
SFTP_PASS=sua_senha_sftp
SFTP_PASTA_REMOTA=/opt/sftp

# 3. Configurações do Banco de Dados PostgreSQL (Conexão Direta)
DB_USER=postgres
DB_PASS=c6zawn8g30swqutta3za
DB_HOST=31.97.251.104
DB_PORT=5432
DB_NAME=n8n_novo

# 4. Webhook do n8n para Alertas de Erros
WEBHOOK_N8N_ERROS=https://n8n-n8n.xjbony.easypanel.host/webhook/b7bb9042-97a1-48f5-8245-91cf28ffc412
```

---

## 🛠️ PASSO 2: A Infraestrutura Dockerizada

Criamos um `docker-compose.yml` que orquestra os containers perfeitamente.

```yaml
version: '3.8'

services:
  # Container do FortiClient VPN que expõe o proxy SOCKS5 e HTTP
  forticlient:
    image: henry42/forticlient-with-proxy:latest
    container_name: fc_vpn_proxy
    privileged: true # Exige privilégios elevados para gerenciar a interface de rede TUN da VPN
    environment:
      - VPNADDR=${VPN_ADDR} # Mapeia as variáveis do seu .env para as variáveis esperadas pela imagem
      - VPNUSER=${VPN_USER}
      - VPNPASS=${VPN_PASS}
    ports:
      - "127.0.0.1:1080:1080"
      - "127.0.0.1:8123:8123"
    restart: unless-stopped

  # Container da Automação (Script Python)
  sftp_automation:
    build: .
    container_name: sftp_automation_app
    environment:
      - PROXY_HABILITADO=True
      - PROXY_HOST=forticlient # Usa o DNS interno do Docker para o Proxy
      - PROXY_PORT=1080
      - SFTP_HOST=${SFTP_HOST}
      - SFTP_PORT=${SFTP_PORT}
      - SFTP_USER=${SFTP_USER}
      - SFTP_PASS=${SFTP_PASS}
      - SFTP_PASTA_REMOTA=${SFTP_PASTA_REMOTA}
      - DB_USER=${DB_USER}
      - DB_PASS=${DB_PASS}
      - DB_HOST=${DB_HOST}
      - DB_PORT=${DB_PORT}
      - DB_NAME=${DB_NAME}
      - WEBHOOK_N8N_ERROS=${WEBHOOK_N8N_ERROS}
      - ARQUIVO_HISTORICO_LOCAL=/app/data/historico_arquivos_lidos.txt
    volumes:
      # Monta a pasta de histórico de forma persistente na VPS
      - ./data:/app/data
    depends_on:
      - forticlient
```

Crie a pasta de volumes antes de rodar o Docker:
```bash
mkdir -p data
```

---

## 🧪 PASSO 3: Executando e Testando a Automação

### 1. Inicializar os Serviços
Suba os containers e compile a imagem Python automaticamente:
```bash
docker compose up -d --build
```

### 2. Verificar Conectividade da VPN
Confirme se o FortiClient conectou com sucesso à sua VPN:
```bash
docker logs fc_vpn_proxy
```
> Busque por mensagens como `Tunnel is up`.

### 3. Executar o Script Manualmente via Docker
Para testar a rotina de download e atualização de banco imediatamente:
```bash
docker compose run --rm sftp_automation
```
Você verá os logs de conexão com o SFTP, leitura das planilhas, mapeamento dinâmico de colunas e inserção no banco de dados Postgres no console.

---

## 🔔 PASSO 4: Integração com n8n Webhook

Caso ocorra **qualquer erro** na execução (falha ao conectar na VPN, falha de autenticação do SFTP, planilhas inválidas, erro de banco ou senha incorreta), o script enviará um payload JSON via POST para o seu webhook do n8n:

### Estrutura do Payload Enviado ao n8n:
```json
{
  "status": "erro",
  "script": "atualiza_banco.py",
  "mensagem": "Breve descrição do erro (ex: Erro de conexão/autenticação SSH)",
  "detalhes": "Detalhes técnicos completos da Exception lançada",
  "timestamp": "2026-07-13 03:00:25"
}
```

No seu n8n, você pode criar um fluxo iniciado por este nó de webhook para enviar um alerta imediato no Telegram, WhatsApp, Slack ou e-mail com estes dados!

---

## ⏰ PASSO 5: Automatizar o Agendamento (Cron Job na VPS)

Para agendar o processamento automático todos os dias às **03:00 da manhã**, use o agendador de tarefas cron da VPS. O cron irá invocar o container temporário de automação (ele roda, atualiza o banco e se destrói automaticamente ao finalizar):

Abra o cron do usuário root da VPS:
```bash
sudo crontab -e
```

Adicione a seguinte linha no final do arquivo:
```cron
0 3 * * * cd /opt/sftp-automation && /usr/bin/docker compose run --rm sftp_automation >> /var/log/sftp_automation.log 2>&1
```

Salve e saia. Os logs de execuções automáticas diárias serão salvos em `/var/log/sftp_automation.log`.
