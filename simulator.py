#!/usr/bin/env python3
"""Publish randomized Wi-Fi telemetry to the local simulation broker."""

from __future__ import annotations

import json
import os
import random
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt


HOST = os.getenv("SIMULATOR_MQTT_HOST", "ai-flow-mqtt-broker")
PORT = int(os.getenv("SIMULATOR_MQTT_PORT", "1883"))
TOPIC = os.getenv("SIMULATOR_MQTT_TOPIC", "weh-device/network")
INTERVAL = float(os.getenv("SIMULATOR_INTERVAL_SECONDS", "5"))
ANOMALY_RATE = float(os.getenv("SIMULATOR_ANOMALY_RATE", "0.35"))
DEVICE_ID = os.getenv("SIMULATOR_DEVICE_ID", "SIM-AP-001")


def message() -> tuple[dict, str]:
    anomaly = random.random() < ANOMALY_RATE
    scenario = random.choice(("Severe congestion", "Co-channel congestion", "Severe interference", "Weak clients")) if anomaly else "Normal"
    if scenario == "Severe congestion":
        stats = {"channel_utilization_pct": 94, "cca_busy_pct": 96, "tx_retries": 28, "tx_failed": 5, "interference_utilization_pct": 12, "avg_rssi_dbm": -58, "client_count": 8, "active_client_count": 7, "weak_client_count": 1, "same_channel_ap_count": 4, "strong_same_channel_ap_count": 2, "obss_utilization_pct": 8}
    elif scenario == "Co-channel congestion":
        stats = {"channel_utilization_pct": 78, "cca_busy_pct": 82, "tx_retries": 18, "tx_failed": 3, "interference_utilization_pct": 18, "avg_rssi_dbm": -61, "client_count": 7, "active_client_count": 6, "weak_client_count": 1, "same_channel_ap_count": 10, "strong_same_channel_ap_count": 5, "obss_utilization_pct": 18}
    elif scenario == "Severe interference":
        stats = {"channel_utilization_pct": 72, "cca_busy_pct": 91, "tx_retries": 22, "tx_failed": 4, "interference_utilization_pct": 58, "avg_rssi_dbm": -62, "client_count": 6, "active_client_count": 5, "weak_client_count": 1, "same_channel_ap_count": 5, "strong_same_channel_ap_count": 3, "obss_utilization_pct": 52}
    else:
        stats = {"channel_utilization_pct": 38, "cca_busy_pct": 42, "tx_retries": 3, "tx_failed": 0, "interference_utilization_pct": 4, "avg_rssi_dbm": -76, "client_count": 5, "active_client_count": 4, "weak_client_count": 4, "same_channel_ap_count": 2, "strong_same_channel_ap_count": 1, "obss_utilization_pct": 3}

    defaults = {"tx_airtime_pct": 25, "rx_airtime_pct": 20, "noise_floor_dbm": -88, "active_client_count": 3, "min_rssi_dbm": -82, "avg_snr_db": 25, "min_snr_db": 15, "avg_tx_rate_mbps": 250, "avg_rx_rate_mbps": 220, "tx_airtime_client_pct": 25, "rx_airtime_client_pct": 20, "avg_mcs": 5, "min_mcs": 2, "avg_nss": 2, "bandwidth_mhz": 80, "channel": 36, "frequency_mhz": 5180, "neighbor_ap_count": 8, "strong_neighbor_ap_count": 3}
    defaults.update(stats)
    payload = {"schema_version": "1.0", "device_id": DEVICE_ID, "timestamp": datetime.now(timezone.utc).isoformat(), "network": [{"type": "wifi", "radios": [{"radio": "5GHz", "ifname": "sim-wlan0", "radio_stats": defaults, "clients": []}]}]}
    return payload, scenario


def main() -> None:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="wifi-simulator")
    while True:
        try:
            client.connect(HOST, PORT, keepalive=60)
            client.loop_start()
            print(f"Simulator connected to {HOST}:{PORT}, publishing {TOPIC}", flush=True)
            while True:
                payload, scenario = message()
                client.publish(TOPIC, json.dumps(payload), qos=1)
                print(f"Published simulated scenario={scenario} topic={TOPIC}", flush=True)
                time.sleep(INTERVAL)
        except Exception as error:
            print(f"Simulator broker connection failed: {error}; retrying in 5s", flush=True)
            try:
                client.loop_stop()
                client.disconnect()
            except Exception:
                pass
            time.sleep(5)


if __name__ == "__main__":
    main()
