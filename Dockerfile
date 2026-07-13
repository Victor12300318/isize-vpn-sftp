FROM python:3.10-slim

# Evita que o Python grave arquivos .pyc no container e força logs não-bufferizados
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Instala dependências de rede e utilitários
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copia e instala as bibliotecas Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código da automação
COPY atualiza_banco.py .

# Comando padrão
CMD ["python", "atualiza_banco.py"]
