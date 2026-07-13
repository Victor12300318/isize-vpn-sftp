import pandas as pd
from sqlalchemy import create_engine, text, inspect
import re
import urllib.parse
import os
import time
import sys
import warnings
import socks
import socket
import paramiko
import json
import urllib.request

# --- CARREGADOR DE VARIÁVEIS DE AMBIENTE (ZERO-DEPENDENCY) ---
def carregar_env_se_existir():
    """Lê o arquivo .env se ele existir localmente e carrega no os.environ."""
    if os.path.exists(".env"):
        with open(".env", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                chave, valor = line.split("=", 1)
                os.environ[chave.strip()] = valor.strip()

# Carrega o .env antes de definir as configurações
carregar_env_se_existir()

def obter_env(chave, valor_padrao):
    """Retorna o valor da variável de ambiente. Se estiver ausente ou vazia (''), usa o padrão."""
    val = os.getenv(chave)
    if val is None or val.strip() == "":
        return valor_padrao
    return val.strip()

# --- CONFIGURAÇÃO ---

# 1. Configuração do Proxy SOCKS5 (Docker FortiClient)
PROXY_HABILITADO = obter_env("PROXY_HABILITADO", "True").lower() == "true"
PROXY_HOST = obter_env("PROXY_HOST", "127.0.0.1")
PROXY_PORT = int(obter_env("PROXY_PORT", "1080"))

# 2. Configuração do Servidor SFTP (VPN)
SFTP_HOST = obter_env("SFTP_HOST", "sua_vpn.empresa.com")
SFTP_PORT = int(obter_env("SFTP_PORT", "22"))
SFTP_USER = obter_env("SFTP_USER", "seu_usuario")
SFTP_PASS = obter_env("SFTP_PASS", "sua_senha")
SFTP_PASTA_REMOTA = obter_env("SFTP_PASTA_REMOTA", "/opt/sftp")

# 3. Configuração de Arquivos Locais
PASTA_TEMP_LOCAL = "./temp_downloads"
ARQUIVO_HISTORICO_LOCAL = obter_env("ARQUIVO_HISTORICO_LOCAL", "historico_arquivos_lidos.txt")

# 4. Configuração do Banco de Dados PostgreSQL (Conexão Direta)
NOME_DA_TABELA_SQL = "isize"
NOME_TABELA_TEMP = "isize_temp"
COLUNA_PRINCIPAL_DE_BUSCA = "Proposta_iSize"

DB_USER = obter_env("DB_USER", "postgres")
# Caso a senha possua caracteres especiais, fazemos o encoding para URL segura
raw_db_pass = obter_env("DB_PASS", "c6zawn8g30swqutta3za")
DB_PASS = urllib.parse.quote_plus(raw_db_pass)
DB_HOST = obter_env("DB_HOST", "31.97.251.104")
DB_PORT = obter_env("DB_PORT", "5432")
DB_NAME = obter_env("DB_NAME", "n8n_novo")

DATABASE_URL = f"postgresql+psycopg2://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
engine = create_engine(DATABASE_URL, pool_pre_ping=True)

# 5. URL do Webhook do n8n para Alertas de Erro
WEBHOOK_N8N_ERROS = obter_env("WEBHOOK_N8N_ERROS", "https://n8n-n8n.xjbony.easypanel.host/webhook/b7bb9042-97a1-48f5-8245-91cf28ffc412")

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

# --- FUNÇÃO DE NOTIFICAÇÃO N8N ---

def enviar_alerta_n8n(mensagem_erro, detalhes=None):
    """Envia um alerta em formato JSON para o webhook do n8n."""
    if not WEBHOOK_N8N_ERROS:
        return
    
    payload = {
        "status": "erro",
        "script": "atualiza_banco.py",
        "mensagem": mensagem_erro,
        "detalhes": detalhes or "",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            WEBHOOK_N8N_ERROS, 
            data=data, 
            headers={"Content-Type": "application/json"}
        )
        print(f"🔔 Enviando alerta de erro para o n8n...")
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status in (200, 201):
                print("✅ Alerta enviado com sucesso para o n8n!")
            else:
                print(f"⚠️ Resposta inesperada do webhook n8n: {response.status}")
    except Exception as e:
        print(f"⚠️ Falha ao enviar alerta para o n8n: {e}")

# --- FUNÇÕES AUXILIARES ---

def carregar_historico():
    if not os.path.exists(ARQUIVO_HISTORICO_LOCAL):
        return set()
    with open(ARQUIVO_HISTORICO_LOCAL, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())

def registrar_no_historico(nome_arquivo):
    with open(ARQUIVO_HISTORICO_LOCAL, "a", encoding="utf-8") as f:
        f.write(f"{nome_arquivo}\n")

def limpar_nome_coluna(nome_coluna):
    nome_coluna = str(nome_coluna)
    nome_limpo = re.sub(r'\W+', '_', nome_coluna)
    return nome_limpo.strip('_')

def obter_colunas_reais_do_banco():
    """Obtém os nomes exatos das colunas da tabela no Postgres."""
    inspector = inspect(engine)
    colunas = [col['name'] for col in inspector.get_columns(NOME_DA_TABELA_SQL)]
    return set(colunas)

# --- CONEXÃO SFTP VIA PROXY SOCKS5 ---

def conectar_sftp_via_proxy():
    """Cria conexão SFTP usando Proxy SOCKS5 se estiver habilitado."""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    sock = None
    if PROXY_HABILITADO:
        print(f"🌐 Estabelecendo túnel SOCKS5 através de {PROXY_HOST}:{PROXY_PORT}...")
        try:
            sock = socks.socksocket()
            sock.set_proxy(
                proxy_type=socks.SOCKS5,
                addr=PROXY_HOST,
                port=PROXY_PORT
            )
            # Conecta o socket SOCKS ao servidor SFTP antes de repassar ao Paramiko
            sock.connect((SFTP_HOST, SFTP_PORT))
        except Exception as e:
            msg = f"Erro ao conectar ao Proxy SOCKS5 em {PROXY_HOST}:{PROXY_PORT}"
            print(f"❌ {msg}: {e}")
            enviar_alerta_n8n(msg, str(e))
            raise
    
    try:
        print(f"🔑 Autenticando no SFTP {SFTP_HOST}:{SFTP_PORT} como '{SFTP_USER}'...")
        ssh.connect(
            hostname=SFTP_HOST,
            port=SFTP_PORT,
            username=SFTP_USER,
            password=SFTP_PASS,
            sock=sock
        )
        print("✅ Conexão SSH/SFTP estabelecida!")
        return ssh
    except Exception as e:
        msg = f"Erro de autenticação ou conexão SFTP com {SFTP_HOST}"
        print(f"❌ {msg}: {e}")
        enviar_alerta_n8n(msg, str(e))
        if sock:
            sock.close()
        raise

# --- LÓGICA DE PROCESSAMENTO BANCO DE DADOS ---

def processar_arquivo_excel(caminho_completo, nome_arquivo):
    print(f"\n🔄 Iniciando processamento de banco para: {nome_arquivo}")
    
    try:
        # 1. LEITURA
        df = pd.read_excel(caminho_completo, dtype=str)
        df.columns = [limpar_nome_coluna(col) for col in df.columns]

        # 2. VALIDAÇÃO DE COLUNA CHAVE
        if COLUNA_PRINCIPAL_DE_BUSCA not in df.columns:
            msg = f"Coluna chave '{COLUNA_PRINCIPAL_DE_BUSCA}' ausente no arquivo {nome_arquivo}"
            print(f"❌ {msg}. Arquivo ignorado.")
            enviar_alerta_n8n(msg, f"Colunas presentes no Excel: {list(df.columns)}")
            return False

        # 3. FILTRAGEM RIGOROSA (Garante apenas colunas existentes no banco)
        colunas_banco = obter_colunas_reais_do_banco()
        
        # Filtra o DataFrame: mantém apenas o que REALMENTE existe no banco
        colunas_validas = [col for col in df.columns if col in colunas_banco]
        colunas_ignoradas = [col for col in df.columns if col not in colunas_banco]

        if colunas_ignoradas:
            print(f"  ⚠️ Colunas ignoradas por não existirem no banco: {colunas_ignoradas}")
        
        df = df[colunas_validas].copy()

        # 4. LIMPEZA E DUPLICATAS
        df = df.where(pd.notnull(df), None)
        df = df.drop_duplicates(subset=[COLUNA_PRINCIPAL_DE_BUSCA], keep='last')

        # 5. BANCO DE DADOS (Conexão Direta, sem passar pelo Proxy SOCKS5)
        with engine.connect() as connection:
            with connection.begin():
                # Envia para a tabela temporária
                df.to_sql(NOME_TABELA_TEMP, connection, if_exists='replace', index=False)
                
                # Montagem dinâmica da Query SQL
                colunas_formatadas = [f'"{col}"' for col in df.columns]
                colunas_str = ", ".join(colunas_formatadas)
                
                # Gerar o "DO UPDATE SET" excluindo a chave primária
                update_stmt = ", ".join([
                    f'"{col}" = EXCLUDED."{col}"' 
                    for col in df.columns if col != COLUNA_PRINCIPAL_DE_BUSCA
                ])
                
                sql_upsert = text(f"""
                    INSERT INTO "{NOME_DA_TABELA_SQL}" ({colunas_str})
                    SELECT {colunas_str} FROM "{NOME_TABELA_TEMP}"
                    ON CONFLICT ("{COLUNA_PRINCIPAL_DE_BUSCA}")
                    DO UPDATE SET {update_stmt};
                """)
                
                connection.execute(sql_upsert)
        
        print(f"✅ Dados inseridos/atualizados com sucesso para o arquivo {nome_arquivo}!")
        registrar_no_historico(nome_arquivo)
        return True

    except Exception as e:
        msg = f"Erro no processamento do Excel / Banco de Dados para {nome_arquivo}"
        print(f"❌ {msg}: {e}")
        enviar_alerta_n8n(msg, str(e))
        return False

# --- ORQUESTRADOR PRINCIPAL ---

def main():
    print("🚀 Iniciando rotina de automação SFTP para PostgreSQL...")
    
    # Certifica-se de que a pasta temporária exista
    if not os.path.exists(PASTA_TEMP_LOCAL):
        os.makedirs(PASTA_TEMP_LOCAL)
        
    historico = carregar_historico()
    ssh = None
    arquivos_processados = []

    try:
        # 1. Conexão SFTP
        ssh = conectar_sftp_via_proxy()
        sftp = ssh.open_sftp()
        
        # Navega para o diretório remoto
        print(f"📂 Acessando pasta remota: {SFTP_PASTA_REMOTA}")
        sftp.chdir(SFTP_PASTA_REMOTA)
        
        # Lista arquivos remotos
        arquivos_remotos = sftp.listdir()
        arquivos_excel = [f for f in arquivos_remotos if f.endswith(('.xlsx', '.xls'))]
        
        # LÓGICA DE PRIMEIRA EXECUÇÃO (IGNORA EXISTENTES)
        if not historico and arquivos_excel:
            print("🆕 Primeira execução detectada! Adicionando todos os arquivos atualmente no SFTP ao histórico para ignorá-los...")
            for nome_arquivo in arquivos_excel:
                registrar_no_historico(nome_arquivo)
                print(f"  ✓ {nome_arquivo} marcado como já lido (ignorado)")
            print(f"✅ {len(arquivos_excel)} arquivos existentes marcados no histórico. O script irá processar apenas novas planilhas que chegarem a partir de agora!")
            sftp.close()
            return
        
        novos_arquivos = [f for f in arquivos_excel if f not in historico]
        
        if not novos_arquivos:
            print("ℹ️ Nenhum arquivo novo encontrado para processar no SFTP.")
            sftp.close()
            return

        print(f"🔍 Encontrados {len(novos_arquivos)} novos arquivos para download: {novos_arquivos}")

        # 2. Downloads e Processamento Sequencial
        for nome_arquivo in novos_arquivos:
            caminho_local = os.path.join(PASTA_TEMP_LOCAL, nome_arquivo)
            
            try:
                print(f"\n📥 Baixando {nome_arquivo}...")
                sftp.get(nome_arquivo, caminho_local)
                print(f"✅ Download finalizado: {nome_arquivo}")
                
                # Executa o processamento do Excel para o Banco de Dados
                sucesso = processar_arquivo_excel(caminho_local, nome_arquivo)
                
                if sucesso:
                    arquivos_processados.append(nome_arquivo)
                else:
                    print(f"⚠️ Processamento mal sucedido para {nome_arquivo}. Não adicionado ao histórico.")
                    
            except Exception as e:
                msg = f"Falha ao baixar/processar arquivo remoto {nome_arquivo}"
                print(f"❌ {msg}: {e}")
                enviar_alerta_n8n(msg, str(e))
            finally:
                # Remove o arquivo local baixado imediatamente para economizar espaço
                if os.path.exists(caminho_local):
                    os.remove(caminho_local)
                    print(f"🗑️ Arquivo temporário {nome_arquivo} removido localmente.")

        sftp.close()

    except Exception as e:
        msg = "Falha catastrófica na rotina de automação"
        print(f"💥 {msg}: {e}")
        enviar_alerta_n8n(msg, str(e))
    finally:
        if ssh:
            ssh.close()
            print("🔒 Conexão SFTP encerrada.")
            
        # Garante a limpeza de arquivos temporários órfãos em caso de erro no loop
        try:
            if os.path.exists(PASTA_TEMP_LOCAL):
                for f in os.listdir(PASTA_TEMP_LOCAL):
                    caminho_f = os.path.join(PASTA_TEMP_LOCAL, f)
                    if os.path.isfile(caminho_f):
                        os.remove(caminho_f)
                os.rmdir(PASTA_TEMP_LOCAL)
                print("🧹 Pasta temporária local limpa e removida.")
        except Exception:
            pass
            
    print(f"\n✨ Processo finalizado! Arquivos atualizados nesta execução: {len(arquivos_processados)}")

if __name__ == "__main__":
    main()
