FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY tests ./tests
COPY benchmarks ./benchmarks
EXPOSE 8000 8080
CMD ["python", "-m", "app.servers.node", "--role", "primary", "--db", "/data/primary.db", "--port", "8000", "--node-url", "http://primary:8000"]
