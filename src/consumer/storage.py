import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

import psycopg

logger = logging.getLogger(__name__)


class TelemetryRepository:
    def __init__(self) -> None:
        self.host = os.getenv("DB_HOST", "timescaledb")
        self.port = int(os.getenv("DB_PORT", "5432"))
        self.database = os.getenv("DB_NAME", "telemetry")
        self.user = os.getenv("DB_USER", "postgres")
        self.password = os.getenv("DB_PASSWORD", "postgres")
        self.table_name = os.getenv("DB_TABLE", "telemetry")
        self.ensure_db()

    def _connect(self):
        return psycopg.connect(
            host=self.host,
            port=self.port,
            dbname=self.database,
            user=self.user,
            password=self.password,
            connect_timeout=10,
        )

    def _parse_timestamp_value(self, value: Any) -> tuple[datetime | None, int | None]:
        if value is None:
            return None, None

        if isinstance(value, (int, float)):
            numeric_value = int(value)
            try:
                return datetime.fromtimestamp(numeric_value, tz=timezone.utc), numeric_value
            except (OverflowError, OSError, ValueError):
                return None, None

        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return None, None

            try:
                numeric_value = int(float(raw))
                return datetime.fromtimestamp(numeric_value, tz=timezone.utc), numeric_value
            except ValueError:
                pass

            try:
                normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
                dt = datetime.fromisoformat(normalized)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc), int(dt.timestamp())
            except ValueError:
                return None, None

        return None, None

    def _parse_optional_int(self, value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _parse_optional_float(self, value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def ensure_db(self) -> None:
        last_error = None
        for attempt in range(1, 11):
            try:
                with self._connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            CREATE TABLE IF NOT EXISTS public.telemetry (
                                timestamp TIMESTAMPTZ NOT NULL,
                                schema_version TEXT,
                                device_id TEXT,
                                network_type TEXT,
                                radio TEXT,
                                ifname TEXT,
                                channel INTEGER,
                                frequency_mhz INTEGER,
                                bandwidth_mhz INTEGER,
                                channel_utilization_pct DOUBLE PRECISION,
                                tx_airtime_pct DOUBLE PRECISION,
                                rx_airtime_pct DOUBLE PRECISION,
                                cca_busy_pct DOUBLE PRECISION,
                                noise_floor_dbm DOUBLE PRECISION,
                                client_count INTEGER,
                                active_client_count INTEGER,
                                avg_rssi_dbm DOUBLE PRECISION,
                                min_rssi_dbm DOUBLE PRECISION,
                                avg_snr_db DOUBLE PRECISION,
                                min_snr_db DOUBLE PRECISION,
                                avg_tx_rate_mbps DOUBLE PRECISION,
                                avg_rx_rate_mbps DOUBLE PRECISION,
                                tx_packets BIGINT,
                                rx_packets BIGINT,
                                tx_bytes BIGINT,
                                rx_bytes BIGINT,
                                tx_retries BIGINT,
                                tx_failed BIGINT,
                                tx_airtime_client_pct DOUBLE PRECISION,
                                rx_airtime_client_pct DOUBLE PRECISION,
                                avg_mcs DOUBLE PRECISION,
                                min_mcs DOUBLE PRECISION,
                                avg_nss DOUBLE PRECISION,
                                weak_client_count INTEGER,
                                neighbor_ap_count INTEGER,
                                strong_neighbor_ap_count INTEGER,
                                same_channel_ap_count INTEGER,
                                strong_same_channel_ap_count INTEGER,
                                obss_utilization_pct DOUBLE PRECISION,
                                interference_utilization_pct DOUBLE PRECISION,
                                clients JSONB,
                                PRIMARY KEY (timestamp, device_id, radio)
                            );
                            """
                        )
                        cur.execute(
                            "SELECT create_hypertable('public.telemetry', 'timestamp', if_not_exists => TRUE);"
                        )
                logger.info("Telemetry table ready in database '%s'", self.database)
                return
            except Exception as exc:  # pragma: no cover - depends on DB startup timing
                last_error = exc
                logger.warning("Database not ready yet (attempt %s/10): %s", attempt, exc)
                time.sleep(2)

        raise RuntimeError(f"Could not initialize telemetry storage: {last_error}")

    def extract_wifi_rows(self, parsed: Any) -> list[dict[str, Any]]:
        if not isinstance(parsed, dict):
            return []

        network_items = parsed.get("network")
        if not isinstance(network_items, list):
            return []

        rows: list[dict[str, Any]] = []
        event_time, _ = self._parse_timestamp_value(parsed.get("timestamp"))

        for network in network_items:
            if not isinstance(network, dict) or network.get("type") != "wifi":
                continue

            radios = network.get("radios")
            if not isinstance(radios, list):
                continue

            for radio in radios:
                if not isinstance(radio, dict):
                    continue

                stats = radio.get("radio_stats") or {}
                if not isinstance(stats, dict):
                    continue

                client_payload = radio.get("clients")
                client_list = client_payload if isinstance(client_payload, list) else None

                row = {
                    "timestamp": event_time or datetime.now(timezone.utc),
                    "schema_version": parsed.get("schema_version"),
                    "device_id": parsed.get("device_id"),
                    "network_type": network.get("type"),
                    "radio": radio.get("radio"),
                    "ifname": radio.get("ifname"),
                    "channel": self._parse_optional_int(stats.get("channel")),
                    "frequency_mhz": self._parse_optional_int(stats.get("frequency_mhz")),
                    "bandwidth_mhz": self._parse_optional_int(stats.get("bandwidth_mhz")),
                    "channel_utilization_pct": self._parse_optional_float(stats.get("channel_utilization_pct")),
                    "tx_airtime_pct": self._parse_optional_float(stats.get("tx_airtime_pct")),
                    "rx_airtime_pct": self._parse_optional_float(stats.get("rx_airtime_pct")),
                    "cca_busy_pct": self._parse_optional_float(stats.get("cca_busy_pct")),
                    "noise_floor_dbm": self._parse_optional_float(stats.get("noise_floor_dbm")),
                    "client_count": self._parse_optional_int(stats.get("client_count")),
                    "active_client_count": self._parse_optional_int(stats.get("active_client_count")),
                    "avg_rssi_dbm": self._parse_optional_float(stats.get("avg_rssi_dbm")),
                    "min_rssi_dbm": self._parse_optional_float(stats.get("min_rssi_dbm")),
                    "avg_snr_db": self._parse_optional_float(stats.get("avg_snr_db")),
                    "min_snr_db": self._parse_optional_float(stats.get("min_snr_db")),
                    "avg_tx_rate_mbps": self._parse_optional_float(stats.get("avg_tx_rate_mbps")),
                    "avg_rx_rate_mbps": self._parse_optional_float(stats.get("avg_rx_rate_mbps")),
                    "tx_packets": self._parse_optional_int(stats.get("tx_packets")),
                    "rx_packets": self._parse_optional_int(stats.get("rx_packets")),
                    "tx_bytes": self._parse_optional_int(stats.get("tx_bytes")),
                    "rx_bytes": self._parse_optional_int(stats.get("rx_bytes")),
                    "tx_retries": self._parse_optional_int(stats.get("tx_retries")),
                    "tx_failed": self._parse_optional_int(stats.get("tx_failed")),
                    "tx_airtime_client_pct": self._parse_optional_float(stats.get("tx_airtime_client_pct")),
                    "rx_airtime_client_pct": self._parse_optional_float(stats.get("rx_airtime_client_pct")),
                    "avg_mcs": self._parse_optional_float(stats.get("avg_mcs")),
                    "min_mcs": self._parse_optional_float(stats.get("min_mcs")),
                    "avg_nss": self._parse_optional_float(stats.get("avg_nss")),
                    "weak_client_count": self._parse_optional_int(stats.get("weak_client_count")),
                    "neighbor_ap_count": self._parse_optional_int(stats.get("neighbor_ap_count")),
                    "strong_neighbor_ap_count": self._parse_optional_int(stats.get("strong_neighbor_ap_count")),
                    "same_channel_ap_count": self._parse_optional_int(stats.get("same_channel_ap_count")),
                    "strong_same_channel_ap_count": self._parse_optional_int(stats.get("strong_same_channel_ap_count")),
                    "obss_utilization_pct": self._parse_optional_float(stats.get("obss_utilization_pct")),
                    "interference_utilization_pct": self._parse_optional_float(stats.get("interference_utilization_pct")),
                    "clients": client_list,
                }
                rows.append(row)

        return rows

    def insert_wifi_metrics(self, payload: str, topic: str) -> None:
        parsed: Any = None
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return

        if not isinstance(parsed, dict):
            return

        wifi_rows = self.extract_wifi_rows(parsed)
        if not wifi_rows:
            logger.info("No wifi payloads found in MQTT message for topic=%s", topic)
            return

        with self._connect() as conn:
            with conn.cursor() as cur:
                for row in wifi_rows:
                    clients_value = row.get("clients")
                    if clients_value is not None:
                        clients_value = json.dumps(clients_value)

                    cur.execute(
                        """
                        INSERT INTO public.telemetry (
                            timestamp,
                            schema_version,
                            device_id,
                            network_type,
                            radio,
                            ifname,
                            channel,
                            frequency_mhz,
                            bandwidth_mhz,
                            channel_utilization_pct,
                            tx_airtime_pct,
                            rx_airtime_pct,
                            cca_busy_pct,
                            noise_floor_dbm,
                            client_count,
                            active_client_count,
                            avg_rssi_dbm,
                            min_rssi_dbm,
                            avg_snr_db,
                            min_snr_db,
                            avg_tx_rate_mbps,
                            avg_rx_rate_mbps,
                            tx_packets,
                            rx_packets,
                            tx_bytes,
                            rx_bytes,
                            tx_retries,
                            tx_failed,
                            tx_airtime_client_pct,
                            rx_airtime_client_pct,
                            avg_mcs,
                            min_mcs,
                            avg_nss,
                            weak_client_count,
                            neighbor_ap_count,
                            strong_neighbor_ap_count,
                            same_channel_ap_count,
                            strong_same_channel_ap_count,
                            obss_utilization_pct,
                            interference_utilization_pct,
                            clients
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        """,
                        (
                            row.get("timestamp"),
                            row.get("schema_version"),
                            row.get("device_id"),
                            row.get("network_type"),
                            row.get("radio"),
                            row.get("ifname"),
                            row.get("channel"),
                            row.get("frequency_mhz"),
                            row.get("bandwidth_mhz"),
                            row.get("channel_utilization_pct"),
                            row.get("tx_airtime_pct"),
                            row.get("rx_airtime_pct"),
                            row.get("cca_busy_pct"),
                            row.get("noise_floor_dbm"),
                            row.get("client_count"),
                            row.get("active_client_count"),
                            row.get("avg_rssi_dbm"),
                            row.get("min_rssi_dbm"),
                            row.get("avg_snr_db"),
                            row.get("min_snr_db"),
                            row.get("avg_tx_rate_mbps"),
                            row.get("avg_rx_rate_mbps"),
                            row.get("tx_packets"),
                            row.get("rx_packets"),
                            row.get("tx_bytes"),
                            row.get("rx_bytes"),
                            row.get("tx_retries"),
                            row.get("tx_failed"),
                            row.get("tx_airtime_client_pct"),
                            row.get("rx_airtime_client_pct"),
                            row.get("avg_mcs"),
                            row.get("min_mcs"),
                            row.get("avg_nss"),
                            row.get("weak_client_count"),
                            row.get("neighbor_ap_count"),
                            row.get("strong_neighbor_ap_count"),
                            row.get("same_channel_ap_count"),
                            row.get("strong_same_channel_ap_count"),
                            row.get("obss_utilization_pct"),
                            row.get("interference_utilization_pct"),
                            clients_value,
                        ),
                    )

        logger.info("Stored %s wifi telemetry rows for device_id=%s", len(wifi_rows), parsed.get("device_id"))

    def insert_message(
        self,
        topic: str,
        payload: str,
        device_id: str | None = None,
        ap_mac: str | None = None,
        serial_number: str | None = None,
        firmware_version: str | None = None,
        uptime_seconds: int | None = None,
        cpu_utilization_pct: float | None = None,
        memory_utilization_pct: float | None = None,
        connected_clients: int | None = None,
        radio_band: str | None = None,
        channel: int | None = None,
        channel_utilization_pct: float | None = None,
        noise_floor_dbm: float | None = None,
        max_connected_device: int | None = None,
        timestamp_epoch: int | None = None,
    ) -> None:
        self.insert_wifi_metrics(payload, topic)
