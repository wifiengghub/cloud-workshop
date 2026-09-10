"""MQTT alert publishing for anomaly detection."""

import json
import logging
import os
import time
from typing import Dict, Any, Optional
import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)


class MQTTAlertPublisher:
    """Publishes anomaly alerts to MQTT broker with device-specific topics."""

    def __init__(
        self,
        broker_host: str = None,
        broker_port: int = None,
        base_topic: str = None,
        client_id: str = None,
        connect_timeout: float = None,
    ):
        self.broker_host = broker_host or os.getenv("MQTT_HOST", "0.tcp.in.ngrok.io")
        self.broker_port = int(
            broker_port if broker_port is not None else os.getenv("MQTT_PORT", "17241")
        )
        self.base_topic = base_topic or os.getenv("MQTT_BASE_TOPIC", "wifi/alerts")
        self.client_id = client_id or os.getenv(
            "MQTT_CLIENT_ID", f"anomaly-detector-{os.getpid()}"
        )
        self.username = os.getenv("MQTT_USER")
        self.password = os.getenv("MQTT_PASS")
        self.connect_timeout = float(
            connect_timeout
            if connect_timeout is not None
            else os.getenv("MQTT_CONNECT_TIMEOUT", "30")
        )
        self.client: Optional[mqtt.Client] = None
        self.connected = False
        self.disconnect_log_interval = float(
            os.getenv("MQTT_DISCONNECT_LOG_INTERVAL", "300")
        )
        self._last_disconnect_log = 0.0

    def _build_client(self) -> mqtt.Client:
        # Use VERSION1 API — proven reliable against ngro k TCP tunnels
        try:
            client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
                client_id=self.client_id,
            )
        except (AttributeError, TypeError, ValueError):
            client = mqtt.Client(client_id=self.client_id)

        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_publish = self._on_publish
        if self.username:
            client.username_pw_set(self.username, self.password)
        # Keep transient broker/network outages from producing a tight retry loop.
        client.reconnect_delay_set(min_delay=5, max_delay=60)
        # Default paho socket timeout is 5s; ngrok often needs more
        client._connect_timeout = self.connect_timeout
        return client

    def connect(self) -> bool:
        """Connect to MQTT broker. Returns True on success."""
        try:
            if self.client is not None:
                try:
                    self.client.loop_stop()
                    self.client.disconnect()
                except Exception:
                    pass

            self.connected = False
            self.client = self._build_client()

            logger.info(
                f"Connecting to MQTT broker at "
                f"{self.broker_host}:{self.broker_port} "
                f"(timeout={self.connect_timeout}s)"
            )
            # Blocking TCP+MQTT CONNECT (works reliably with ngrok)
            self.client.connect(
                self.broker_host, self.broker_port, keepalive=60
            )
            self.client.loop_start()

            deadline = time.time() + min(self.connect_timeout, 10.0)
            while time.time() < deadline:
                if self.connected:
                    return True
                time.sleep(0.05)

            # TCP connect succeeded but CONNACK slow — still usable if socket is up
            if self.client.is_connected():
                self.connected = True
                logger.info("✅ MQTT socket connected (CONNACK pending/late)")
                return True

            logger.error(
                f"Failed to connect to MQTT broker: timed out after "
                f"{self.connect_timeout}s"
            )
            return False
        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker: {e}")
            return False

    def ensure_connected(self) -> bool:
        """Reconnect if disconnected. Safe to call each poll."""
        if self.client is not None and (
            self.connected or self.client.is_connected()
        ):
            self.connected = True
            return True
        logger.warning("MQTT disconnected; attempting reconnect...")
        return self.connect()

    def disconnect(self):
        """Disconnect from MQTT broker."""
        if self.client:
            try:
                self.client.loop_stop()
                self.client.disconnect()
            except Exception:
                pass
            self.connected = False

    def _on_connect(self, client, userdata, flags, rc):
        self.connected = rc == 0
        if self.connected:
            logger.info(f"✅ Connected to MQTT broker (rc={rc})")
        else:
            logger.error(f"❌ MQTT connect failed (rc={rc})")

    def _on_disconnect(self, client, userdata, rc):
        self.connected = False
        now = time.monotonic()
        if (
            rc != 0
            and now - self._last_disconnect_log >= self.disconnect_log_interval
        ):
            self._last_disconnect_log = now
            #logger.warning(f"Unexpected MQTT disconnection (rc={rc})")

    def _on_publish(self, client, userdata, mid):
        pass

    def publish_anomaly_alert(
        self, device_id: str, anomaly_data: Dict[str, Any]
    ) -> bool:
        """Publish to {base_topic}/{device_id}/anomaly."""
        if not self.ensure_connected():
            logger.warning(f"MQTT not connected, skipping publish for {device_id}")
            return False

        topic = f"{self.base_topic}/{device_id}/anomaly"
        payload = json.dumps(anomaly_data, default=str)

        try:
            result = self.client.publish(topic, payload, qos=1, retain=False)
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                logger.info(f"📡 Published anomaly to {topic}")
                return True
            logger.error(f"Failed to publish to {topic}: {result.rc}")
            return False
        except Exception as e:
            logger.error(f"Error publishing to MQTT: {e}")
            self.connected = False
            return False

    def publish_device_status(
        self, device_id: str, status: str, metrics: Dict[str, Any]
    ) -> bool:
        """Publish to {base_topic}/{device_id}/status."""
        if not self.ensure_connected():
            return False

        topic = f"{self.base_topic}/{device_id}/status"
        payload = json.dumps(
            {
                "status": status,
                "timestamp": str(metrics.get("timestamp", "")),
                "anomaly_prob": metrics.get("anomaly_prob", 0.0),
                "reason_code": metrics.get("reason_code", ""),
                "scenario": metrics.get("scenario", ""),
                "layman_explanation": metrics.get("layman_explanation", ""),
                "recommended_action": metrics.get("recommended_action", ""),
                "mqtt_topic": topic,
                "published_to_mqtt": True,
            }
        )

        try:
            result = self.client.publish(topic, payload, qos=1, retain=True)
            return result.rc == mqtt.MQTT_ERR_SUCCESS
        except Exception as e:
            logger.error(f"Error publishing status: {e}")
            self.connected = False
            return False

    def publish_batch_summary(self, summary: Dict[str, Any]) -> bool:
        """Publish to {base_topic}/summary."""
        if not self.ensure_connected():
            return False

        topic = f"{self.base_topic}/summary"
        payload = json.dumps(summary)

        try:
            result = self.client.publish(topic, payload, qos=1, retain=True)
            return result.rc == mqtt.MQTT_ERR_SUCCESS
        except Exception as e:
            logger.error(f"Error publishing summary: {e}")
            self.connected = False
            return False
