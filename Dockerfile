FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install .

COPY config ./config

# El estado, el diario y los logs van a /data (volumen persistente en Railway).
ENV TRADEBOT_CONFIG=config/railway.yaml,config/railway-stocks.yaml
CMD ["tradebot", "paper"]
