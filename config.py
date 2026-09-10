import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    broker_host: str = os.getenv("MQTT_BROKER_HOST", "localhost")
    broker_port: int = int(os.getenv("MQTT_BROKER_PORT", "1883"))
    client_id: str = os.getenv("MQTT_CLIENT_ID", "mqtt-client")
    username: str | None = os.getenv("MQTT_USERNAME") or None
    password: str | None = os.getenv("MQTT_PASSWORD") or None
    topic: str = os.getenv("MQTT_TOPIC", "devices/telemetry")
    qos: int = int(os.getenv("MQTT_QOS", "0"))
    keepalive: int = int(os.getenv("MQTT_KEEPALIVE", "60"))
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    db_host: str = os.getenv("DB_HOST", "timescaledb")
    db_port: int = int(os.getenv("DB_PORT", "5432"))
    db_name: str = os.getenv("DB_NAME", "telemetry")
    db_user: str = os.getenv("DB_USER", "postgres")
    db_password: str = os.getenv("DB_PASSWORD", "postgres")
    db_table: str = os.getenv("DB_TABLE", "telemetry")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
