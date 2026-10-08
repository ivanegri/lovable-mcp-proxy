FROM python:3.11-slim

# Evita criação de arquivos .pyc e buffer de logs
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Instala dependências
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia código da aplicação
COPY proxy.py .

# Expõe a porta da API
EXPOSE 8000

# Executa o Uvicorn
CMD ["uvicorn", "proxy:app", "--host", "0.0.0.0", "--port", "8000"]
