import json
import logging
import time
from typing import Any

import paho.mqtt.client as mqtt

from .config import get_settings
from .storage import TelemetryRepository

logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    logging.basicConfig(
        level=get_settings().log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


class MqttTelemetryConsumer:
    def __init__(self) -> None:
        self.settings = get_settings()
        _setup_logging()
        self.storage = TelemetryRepository()
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=self.settings.client_id,
        )
        self.client.enable_logger(logger)

        if self.settings.username and self.settings.password:
            self.client.username_pw_set(self.settings.username, self.settings.password)

        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            logger.debug("Connected to MQTT broker at %s:%s", self.settings.broker_host, self.settings.broker_port)
            client.subscribe(self.settings.topic, qos=self.settings.qos)
            logger.debug("Subscribed to telemetry topic: %s", self.settings.topic)
        else:
            logger.error("Connection failed with MQTT code %s", rc)

    def _on_message(self, client, userdata, msg):
        payload = msg.payload.decode("utf-8", errors="replace")
        logger.info(" ------- Telemetry received from topic '%s': %s \n", msg.topic, payload)

        try:
            parsed = json.loads(payload)
            if not isinstance(parsed, dict):
                logger.info("Message is not a JSON object; ignoring it")
                return

            wifi_rows = self.storage.extract_wifi_rows(parsed)
            if not wifi_rows:
                logger.info("No wifi network payload found in message; ignoring it")
                return

            self.storage.insert_wifi_metrics(payload, msg.topic)
            logger.info("Saved wifi telemetry rows to TimescaleDB")
        except json.JSONDecodeError:
            logger.info("Payload is not valid JSON; ignoring it")
        except Exception:
            logger.exception("Failed to store wifi telemetry payload in TimescaleDB")

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties=None):
        logger.debug("Disconnected from MQTT broker with code %s", reason_code)

    def connect(self) -> None:
        logger.info("Connecting to broker at %s:%s", self.settings.broker_host, self.settings.broker_port)
        self.client.connect(self.settings.broker_host, self.settings.broker_port, self.settings.keepalive)
        self.client.loop_start()

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutdown requested")
            self.disconnect()

    def publish(self, payload: str | dict[str, Any], topic: str | None = None) -> None:
        target_topic = topic or self.settings.topic
        if isinstance(payload, dict):
            payload = json.dumps(payload)

        result = self.client.publish(target_topic, payload, qos=self.settings.qos)
        result.wait_for_publish()
        logger.info("Published telemetry to %s: %s", target_topic, payload)

    def disconnect(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()
        logger.info("Disconnected from broker")
