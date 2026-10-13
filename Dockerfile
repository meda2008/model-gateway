FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -i https://pypi.org/simple -r requirements.txt

COPY gateway/ ./gateway/
COPY manage_keys.py .env.example ./

ENV GATEWAY_HOST=0.0.0.0 \
    GATEWAY_PORT=8317 \
    GATEWAY_DB=/data/gateway.db

VOLUME ["/data"]
EXPOSE 8317

CMD ["python", "-m", "gateway"]
