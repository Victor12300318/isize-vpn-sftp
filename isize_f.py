import pandas as pd
from sqlalchemy import create_engine, text, inspect
import re
import urllib.parse
import os
import time
import sys
import warnings

# --- CONFIGURAÇÃO ---
PASTA_ENTRADA = r"\\192.168.100.23\99 - compartilhado\TEMP.HEADSET.014\Documents"
ARQUIVO_HISTORICO_LOCAL = "historico_arquivos_lidos.txt"

NOME_DA_TABELA_SQL = "isize"
NOME_TABELA_TEMP = "isize_temp"
COLUNA_PRINCIPAL_DE_BUSCA = "Proposta_iSize"

USER = "postgres"
PASSWORD = urllib.parse.quote_plus("c6zawn8g30swqutta3za")
HOST = "31.97.251.104"
PORT = "5432"
DBNAME = "n8n_novo"

DATABASE_URL = f"postgresql+psycopg2://{USER}:{PASSWORD}@{HOST}:{PORT}/{DBNAME}"
engine = create_engine(DATABASE_URL, pool_pre_ping=True)

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

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
    # Retorna uma lista de nomes de colunas existentes na tabela real
    colunas = [col['name'] for col in inspector.get_columns(NOME_DA_TABELA_SQL)]
    return set(colunas)

# --- LÓGICA DE PROCESSAMENTO ---

def processar_arquivo_excel(caminho_completo, nome_arquivo):
    print(f"\n🔄 Iniciando processamento: {nome_arquivo}")
    
    try:
        # 1. LEITURA
        df = pd.read_excel(caminho_completo, dtype=str)
        df.columns = [limpar_nome_coluna(col) for col in df.columns]

        # 2. VALIDAÇÃO DE COLUNA CHAVE
        if COLUNA_PRINCIPAL_DE_BUSCA not in df.columns:
            print(f"❌ Coluna '{COLUNA_PRINCIPAL_DE_BUSCA}' ausente. Arquivo ignorado.")
            return False

        # 3. FILTRAGEM RIGOROSA (Onde o erro acontecia)
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

        # 5. BANCO DE DADOS
        with engine.connect() as connection:
            with connection.begin():
                # Envia para a tabela temporária (apenas colunas que existem no destino)
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
        
        print(f"✅ Arquivo processado com sucesso!")
        registrar_no_historico(nome_arquivo)
        return True

    except Exception as e:
        print(f"❌ ERRO CRÍTICO no arquivo {nome_arquivo}: {e}")
        return False

# --- MONITORAMENTO ---
def iniciar_monitoramento():
    print(f"\n🚀 Monitorando pasta: {PASTA_ENTRADA}")
    while True:
        historico = carregar_historico()
        try:
            arquivos = [f for f in os.listdir(PASTA_ENTRADA) if f.endswith(('.xlsx', '.xls'))]
            novos = [f for f in arquivos if f not in historico]
            
            for arq in novos:
                processar_arquivo_excel(os.path.join(PASTA_ENTRADA, arq), arq)
                
        except Exception as e:
            print(f"Erro ao listar pasta: {e}")
        
        time.sleep(10)

if __name__ == "__main__":
    iniciar_monitoramento()