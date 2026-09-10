FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
COPY data ./data
RUN mkdir -p /app/model_artifacts /app/logs \
    && addgroup --system app \
    && adduser --system --ingroup app app \
    && chown -R app:app /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MODEL_PATH=model_artifacts/model.pkl \
    POLL_INTERVAL_SECONDS=30 \
    ALERT_THRESHOLD=0.5 \
    MQTT_ENABLED=true \
    MQTT_BASE_TOPIC=wifi/alerts

USER app

CMD ["python", "consumer.py"]
