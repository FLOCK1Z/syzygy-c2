# Base Python estável
FROM python:3.9-slim

# Define o diretório de trabalho
WORKDIR /app

# Instala dependências de sistema para o PostgreSQL
RUN apt-get update && apt-get install -y libpq-dev gcc && rm -rf /var/lib/apt/lists/*

# Copia e instala requisitos
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código fonte
COPY . .

# Expõe a porta que o Back4App monitora
EXPOSE 8080

# Comando de ignição
CMD ["python", "web_core.py"]
