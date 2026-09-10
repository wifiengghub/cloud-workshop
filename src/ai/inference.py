import json
import os
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

from .model_io import load_model
from .mqtt_publisher import MQTTAlertPublisher
from .db import get_conn, fetch_window_features
from .data import ML_FEATURE_COLUMNS

THRESHOLD = float(os.getenv("ALERT_THRESHOLD", "0.5"))
MODEL_PATH = os.getenv("MODEL_PATH", "model_artifacts/model.pkl")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", "30"))
MQTT_ENABLED = os.getenv("MQTT_ENABLED", "true").lower() in ("1", "true", "yes")
ANOMALY_LOG_DIR = Path(os.getenv("ANOMALY_LOG_DIR", "logs/anomalies"))


def json_safe(value):
    """Convert DB/numpy values into JSON-serializable Python types."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, float) and (np.isnan(value) or np.isinf(value)):
        return None
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if pd.isna(value):
        return None
    return value


def to_jsonable_dict(row_dict):
    """Normalize a telemetry row dict for JSON logging / MQTT."""
    return {str(k): json_safe(v) for k, v in row_dict.items()}


def prepare_inference_data(row_data):
    """
    Prepare a single row for inference.

    Expects dict with all radio_stats / telemetry columns.
    Extracts features used by the trained model.
    """
    features = {}
    for col in ML_FEATURE_COLUMNS:
        val = row_data.get(col, None)
        if val is not None:
            try:
                features[col] = float(val)
            except (ValueError, TypeError):
                features[col] = 0.0
        else:
            features[col] = 0.0
    return features


def explain_anomaly(features: dict, prob: float) -> str:
    """Convert model score into plain-language reasons for an anomaly."""
    reasons = []

    if features.get("channel_utilization_pct", 0.0) > 80:
        reasons.append("radio traffic is unusually high")
    if features.get("cca_busy_pct", 0.0) > 70:
        reasons.append("the air is very busy or congested")
    if features.get("tx_retries", 0.0) > 10 or features.get("tx_failed", 0.0) > 2:
        reasons.append("wireless retries and failures are elevated")
    if features.get("avg_rssi_dbm", 0.0) < -70:
        reasons.append("signal quality is weak")
    if features.get("obss_utilization_pct", 0.0) > 40:
        reasons.append("neighboring Wi-Fi networks are creating strong interference")
    if features.get("client_count", 0.0) > 20 or features.get("active_client_count", 0.0) > 12:
        reasons.append("many clients are connected to this radio")
    if features.get("noise_floor_dbm", 0.0) < -85:
        reasons.append("the noise floor is high")

    if not reasons:
        reasons.append("the model sees a combination of unusual wireless conditions")

    reason_text = "; ".join(reasons[:3])
    return f"{reason_text}. Model confidence: {prob:.1%}."


def classify_reason(features: dict) -> tuple[str, str]:
    """Return a stable reason code and training-like scenario name."""
    if features.get("channel_utilization_pct", 0.0) >= 90 or features.get("cca_busy_pct", 0.0) >= 90:
        return "SEVERE_CONGESTION", "Severe congestion"
    if features.get("obss_utilization_pct", 0.0) >= 40 or features.get("interference_utilization_pct", 0.0) >= 40:
        return "SEVERE_INTERFERENCE", "Severe interference"
    if features.get("same_channel_ap_count", 0.0) >= 8 or features.get("strong_same_channel_ap_count", 0.0) >= 4:
        return "CO_CHANNEL_CONGESTION", "Co-channel congestion"
    if features.get("avg_rssi_dbm", 0.0) < -70 or features.get("weak_client_count", 0.0) >= 3:
        return "WEAK_CLIENTS", "Weak clients"
    if features.get("tx_retries", 0.0) >= 10 or features.get("tx_failed", 0.0) >= 2:
        return "HIGH_RETRIES_OR_FAILURES", "High packet retry or failure rate"
    if features.get("client_count", 0.0) >= 20 or features.get("active_client_count", 0.0) >= 12:
        return "HIGH_CLIENT_LOAD", "Too many active clients"
    return "UNUSUAL_WIFI_CONDITIONS", "Unusual Wi-Fi conditions"


def layman_explanation(reason_code: str, scenario: str) -> tuple[str, str]:
    """Explain an anomaly without ML or radio-engineering terminology."""
    explanations = {
        "SEVERE_CONGESTION": ("The Wi-Fi channel is extremely busy, so devices may be slow or unreliable.", "Reduce channel utilization by moving clients to a cleaner channel or another radio."),
        "SEVERE_INTERFERENCE": ("Nearby wireless activity is strongly competing with this network.", "Check neighboring access points and non-Wi-Fi interference, then select a cleaner channel."),
        "CO_CHANNEL_CONGESTION": ("Several nearby access points are using the same channel and competing for airtime.", "Review channel assignments and move this radio to a less crowded channel."),
        "WEAK_CLIENTS": ("One or more clients have a weak connection to the access point.", "Check client distance and obstructions, then reposition the access point or improve coverage."),
        "HIGH_RETRIES_OR_FAILURES": ("Many wireless messages are being retried or failing to arrive.", "Investigate signal quality, interference, and channel selection for the affected radio."),
        "HIGH_CLIENT_LOAD": ("The access point is serving many active devices at the same time.", "Distribute clients across radios or access points and review capacity."),
    }
    return explanations.get(
        reason_code,
        ("The network measurements differ from the healthy patterns learned by the model.", "Inspect the affected radio metrics and compare them with nearby access points."),
    )


def rows_to_df(rows):
    """Convert database rows to DataFrame with ML features."""
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def _score_rows(model, model_type, scaler, feature_columns, df):
    """Run model scoring; return list of (device_id, radio, timestamp, prob, row_dict)."""
    results = []
    X_list = []
    meta = []

    for _, row in df.iterrows():
        row_dict = row.to_dict()
        features_dict = prepare_inference_data(row_dict)
        X_row = [features_dict.get(col, 0.0) for col in feature_columns]
        X_list.append(X_row)
        meta.append(
            (
                row_dict.get("device_id", "unknown"),
                row_dict.get("radio", ""),
                row_dict.get("timestamp"),
                row_dict,
                features_dict,
            )
        )

    X = pd.DataFrame(X_list, columns=feature_columns)
    X_scaled = scaler.transform(X) if scaler is not None else X.values

    if model_type == "supervised" and hasattr(model, "predict_proba"):
        probs = model.predict_proba(X_scaled)[:, 1]
    else:
        if hasattr(model, "decision_function"):
            scores = -model.decision_function(X_scaled)
        elif hasattr(model, "score_samples"):
            scores = -model.score_samples(X_scaled)
        else:
            preds = model.predict(X_scaled)
            scores = (preds == -1).astype(float)

        minv = float(np.min(scores))
        maxv = float(np.max(scores))
        if maxv - minv > 0:
            probs = (scores - minv) / (maxv - minv)
        else:
            probs = (scores > 0).astype(float)

    for (device_id, radio, ts, row_dict, features_dict), prob in zip(meta, probs):
        results.append((device_id, radio, ts, float(prob), row_dict, features_dict))
    return results


def _log_anomaly_case(record: dict) -> Path:
    """
    Persist full anomaly input for offline replay/testing.

    Writes:
      - one JSONL line to logs/anomalies/anomalies.jsonl
      - one detailed JSON file per anomaly under logs/anomalies/
    """
    ANOMALY_LOG_DIR.mkdir(parents=True, exist_ok=True)
    safe_device = str(record.get("device_id", "unknown")).replace("/", "_")
    safe_radio = str(record.get("radio", "na")).replace("/", "_")
    ts_tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    case_path = ANOMALY_LOG_DIR / f"anomaly_{safe_device}_{safe_radio}_{ts_tag}.json"

    with case_path.open("w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, default=json_safe)

    with (ANOMALY_LOG_DIR / "anomalies.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=json_safe) + "\n")

    return case_path


def run_loop():
    """Run continuous anomaly detection loop using telemetry data from database."""
    print("=" * 70, flush=True)
    print("Starting Inference Loop (30-second polling)", flush=True)
    print("=" * 70, flush=True)

    raw = load_model(MODEL_PATH)

    if isinstance(raw, dict) and "model" in raw and "type" in raw:
        model = raw["model"]
        model_type = raw["type"]
        feature_columns = raw.get("feature_columns", ML_FEATURE_COLUMNS)
        scaler = raw.get("scaler", None)
        model_version = raw.get("model_version", "unknown")
    else:
        model = raw
        model_type = "supervised" if hasattr(model, "predict_proba") else "unsupervised"
        feature_columns = ML_FEATURE_COLUMNS
        scaler = None
        model_version = "unknown"

    print(f"\n📦 Model loaded:", flush=True)
    print(f"   - Type: {model_type}", flush=True)
    print(f"   - Version: {model_version}", flush=True)
    print(f"   - Features: {len(feature_columns)}", flush=True)
    print(f"   - Alert threshold: {THRESHOLD}", flush=True)
    print(f"   - Poll interval: {POLL_INTERVAL}s", flush=True)
    print(f"   - MQTT publishing: {MQTT_ENABLED}", flush=True)
    print(f"   - Anomaly log dir: {ANOMALY_LOG_DIR}", flush=True)
    print("=" * 70, flush=True)

    mqtt_pub = None
    if MQTT_ENABLED:
        mqtt_pub = MQTTAlertPublisher()
        if mqtt_pub.connect():
            print(
                f"   ✅ MQTT connected: "
                f"{mqtt_pub.broker_host}:{mqtt_pub.broker_port}",
                flush=True,
            )
        else:
            print(
                "   ⚠️  MQTT connect failed at startup; "
                "will retry on each anomaly publish",
                flush=True,
            )

    print("   ⏳ Waiting for TimescaleDB connection...", flush=True)
    conn = get_conn(retries=60, retry_delay=5)
    print("   ✅ TimescaleDB connection established", flush=True)
    poll_count = 0
    anomaly_cooldown_seconds = int(os.getenv("ANOMALY_ALERT_COOLDOWN_SECONDS", "300"))
    last_anomaly_by_key = {}

    try:
        while True:
            poll_count += 1
            print(f"\n[Poll #{poll_count}] Fetching telemetry data...", flush=True)

            try:
                rows = fetch_window_features(conn)
            except Exception as e:
                print(f"   ❌ DB fetch failed: {e}", flush=True)
                conn = get_conn()
                print(f"   ⏱️  Next poll in {POLL_INTERVAL}s...", flush=True)
                time.sleep(POLL_INTERVAL)
                continue

            df = rows_to_df(rows)

            if df.empty:
                print("   ⚠️  No data returned from database", flush=True)
            else:
                print(f"   📊 Fetched {len(df)} device measurements", flush=True)
                print(f"\n   🎯 Running anomaly detection...", flush=True)

                scored = _score_rows(model, model_type, scaler, feature_columns, df)
                healthy_count = 0
                anomaly_count = 0
                published = 0
                now_iso = datetime.now(timezone.utc).isoformat()

                for device_id, radio, ts, prob, row_dict, features_dict in scored:
                    if prob >= THRESHOLD:
                        key = (str(device_id), str(radio or ""))
                        current_ts = json_safe(ts) if ts is not None else now_iso
                        last_alert = last_anomaly_by_key.get(key)
                        now_monotonic = time.monotonic()
                        should_alert = True

                        if last_alert is not None:
                            last_timestamp = last_alert.get("timestamp")
                            if last_timestamp == current_ts:
                                should_alert = False
                            else:
                                last_seen = last_alert.get("at", 0.0)
                                if (now_monotonic - last_seen) < anomaly_cooldown_seconds:
                                    should_alert = False

                        if not should_alert:
                            print(
                                f"      ⏳ Suppressed duplicate anomaly for device={device_id} radio={radio} "
                                f"(cooldown active, last timestamp={last_alert.get('timestamp') if last_alert else 'n/a'})",
                                flush=True,
                            )
                            continue

                        anomaly_count += 1
                        print(
                            f"      🚨 ALERT: device={device_id} radio={radio} "
                            f"anomaly_prob={prob:.4f}",
                            flush=True,
                        )

                        input_row = to_jsonable_dict(row_dict)
                        features = {k: float(features_dict.get(k, 0.0)) for k in feature_columns}
                        reason = explain_anomaly(features, float(prob))
                        reason_code, scenario = classify_reason(features)
                        explanation, recommended_action = layman_explanation(reason_code, scenario)
                        anomaly_topic = f"{mqtt_pub.base_topic}/{device_id}/anomaly" if mqtt_pub else None
                        status_topic = f"{mqtt_pub.base_topic}/{device_id}/status" if mqtt_pub else None
                        anomaly_record = {
                            "detected_at": now_iso,
                            "poll": poll_count,
                            "device_id": str(device_id),
                            "radio": str(radio) if radio is not None else "",
                            "timestamp": json_safe(ts) if ts is not None else now_iso,
                            "anomaly_prob": float(prob),
                            "threshold": THRESHOLD,
                            "model_version": model_version,
                            "feature_columns": list(feature_columns),
                            "features": features,
                            "input_row": input_row,
                            "reason": reason,
                            "explanation": reason,
                            "reason_code": reason_code,
                            "scenario": scenario,
                            "layman_explanation": explanation,
                            "recommended_action": recommended_action,
                        }
                        last_anomaly_by_key[key] = {
                            "timestamp": anomaly_record["timestamp"],
                            "at": now_monotonic,
                        }

                        case_path = _log_anomaly_case(anomaly_record)
                        print(
                            f"      📝 Anomaly input logged: {case_path}",
                            flush=True,
                        )
                        print(
                            f"      💬 Why it was flagged: {reason}",
                            flush=True,
                        )
                        print(
                            "      🧾 Input details:\n"
                            + json.dumps(anomaly_record, indent=2, default=json_safe),
                            flush=True,
                        )

                        if mqtt_pub is not None:
                            payload = {
                                "device_id": anomaly_record["device_id"],
                                "radio": anomaly_record["radio"],
                                "timestamp": anomaly_record["timestamp"],
                                "anomaly_prob": anomaly_record["anomaly_prob"],
                                "threshold": THRESHOLD,
                                "model_version": model_version,
                                "features": features,
                                "reason": reason,
                                "explanation": reason,
                                "reason_code": reason_code,
                                "scenario": scenario,
                                "layman_explanation": explanation,
                                "recommended_action": recommended_action,
                                "mqtt_topic": anomaly_topic,
                                "message": reason,
                                "input_row": input_row,
                                "metrics": {
                                    k: input_row.get(k)
                                    for k in (
                                        "channel_utilization_pct",
                                        "cca_busy_pct",
                                        "tx_retries",
                                        "tx_failed",
                                        "avg_rssi_dbm",
                                        "obss_utilization_pct",
                                        "client_count",
                                        "active_client_count",
                                    )
                                    if k in input_row
                                },
                            }
                            print(
                                "\n   📦 MQTT payload being published:\n"
                                + json.dumps(payload, indent=2, default=json_safe),
                                flush=True,
                            )
                            alert_published = mqtt_pub.publish_anomaly_alert(device_id, payload)
                            payload["published_to_mqtt"] = alert_published
                            print(
                                f"   📡 Published anomaly message: topic={anomaly_topic} success={alert_published}\n"
                                + json.dumps(payload, indent=2, default=json_safe),
                                flush=True,
                            )
                            if alert_published:
                                published += 1
                            status_published = mqtt_pub.publish_device_status(
                                device_id,
                                "anomaly",
                                {
                                    "timestamp": anomaly_record["timestamp"],
                                    "anomaly_prob": float(prob),
                                    "reason_code": reason_code,
                                    "scenario": scenario,
                                    "layman_explanation": explanation,
                                    "recommended_action": recommended_action,
                                    "mqtt_topic": status_topic,
                                },
                            )
                            print(
                                f"   📡 Published status message: topic={status_topic} success={status_published}",
                                flush=True,
                            )
                    else:
                        healthy_count += 1

                print(
                    f"   📈 Results: {healthy_count} healthy, "
                    f"{anomaly_count} anomalies",
                    flush=True,
                )
                if mqtt_pub is not None:
                    print(
                        f"   📡 Published {published} anomaly alert(s) to MQTT",
                        flush=True,
                    )
                    if mqtt_pub.connected or (
                        mqtt_pub.client is not None and mqtt_pub.client.is_connected()
                    ):
                        mqtt_pub.publish_batch_summary(
                            {
                                "poll": poll_count,
                                "timestamp": now_iso,
                                "total_rows": len(df),
                                "healthy_count": healthy_count,
                                "anomaly_count": anomaly_count,
                                "published": published,
                            }
                        )

            print(f"   ⏱️  Next poll in {POLL_INTERVAL}s...", flush=True)
            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print("\n\n⛔ Stopping inference loop", flush=True)
    finally:
        conn.close()
        if mqtt_pub is not None:
            mqtt_pub.disconnect()


if __name__ == "__main__":
    run_loop()
