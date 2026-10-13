FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY gateway/ ./gateway/
COPY manage_keys.py ./

# Data volume /data holds gateway.db. For the Qoder backend, run
# `python -m gateway qoder-login` on the host first, then mount the token file:
#   docker run -d -p 8317:8317 \
#     -v /srv/model-gateway:/data \
#     -v ~/.qoder2api-auth.json:/data/qoder-auth.json \
#     --env-file .env model-gateway
# QODER_AUTH_PATH=/data/qoder-auth.json tells the container where to find it;
# without the mount the Qoder backend reports "not logged in" (WorkBuddy still works).
ENV GATEWAY_HOST=0.0.0.0 \
    GATEWAY_PORT=8317 \
    GATEWAY_DB=/data/gateway.db \
    QODER_AUTH_PATH=/data/qoder-auth.json

VOLUME ["/data"]
EXPOSE 8317

CMD ["python", "-m", "gateway"]
