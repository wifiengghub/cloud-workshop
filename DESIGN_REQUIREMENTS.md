# DeviceDataHub AI design requirements

## 1. Purpose

Provide an end-to-end Wi-Fi telemetry and anomaly-detection system that can be
started on a Linux workstation, run entirely in Kubernetes pods, expose a
Grafana dashboard, and publish actionable anomaly context over MQTT.

## 2. Functional requirements

| ID | Requirement | Acceptance criteria |
| --- | --- | --- |
| FR-01 | Consume MQTT telemetry | Consumer connects to the configured ngrok broker and subscribes to `weh-device/network` or the configured topic. |
| FR-02 | Validate Wi-Fi payloads | Invalid JSON and non-Wi-Fi payloads are ignored without terminating the consumer. |
| FR-03 | Persist telemetry | Valid radio rows are normalized and stored in `public.telemetry` in TimescaleDB. |
| FR-04 | Train the model | Inference initialization trains on `data/train_1000.json` and writes `model_artifacts/model.pkl`. |
| FR-05 | Run inference | Inference polls recent telemetry, scores each radio, and applies the configured alert threshold. |
| FR-06 | Explain anomalies | Every published anomaly contains a reason code, scenario, plain-language explanation, and recommended action. |
| FR-07 | Publish alerts | Anomaly, status, and summary payloads are published to the configured MQTT topics. |
| FR-08 | Provision Grafana | Grafana starts with the TimescaleDB datasource and dashboard JSON imported automatically. |
| FR-09 | Expose local access | Linux startup forwards Grafana to `localhost:3000` and TimescaleDB to `localhost:5433`. |
| FR-10 | Clean up | `scripts/shutdown.py` stops port-forwards and removes the Helm release, namespace, kind cluster, image, and venv by default. |

## 3. Non-functional requirements

| ID | Requirement | Acceptance criteria |
| --- | --- | --- |
| NFR-01 | Reproducible startup | `./scripts/start_linux.sh` creates the venv, installs requirements, builds the image, deploys Helm, and starts forwarding. |
| NFR-02 | Kubernetes-only application runtime | Consumer, database, Grafana, training init, and inference run as Kubernetes workloads. |
| NFR-03 | Resilient database startup | Database readiness probes, wait init containers, and inference connection retries handle PostgreSQL startup delays. |
| NFR-04 | Observable operation | Pod status, consumer logs, training logs, inference logs, and port-forward logs are available through documented commands. |
| NFR-05 | Credential hygiene | Real credentials are supplied through environment or private Helm values and are not committed. |
| NFR-06 | Single source of truth | Root Docker build files, `src/`, `config/`, `scripts/`, Helm chart, training dataset, and lifecycle scripts define the supported flow. |

## 4. Operational interfaces

### Grafana

```text
URL: http://localhost:3000
Username: admin
Password: admin by default
```

### TimescaleDB

```text
Host: localhost
Port: 5433
Database: telemetry
User: postgres
Password: postgres by default
Table: public.telemetry
```

### MQTT output

```text
wifi/alerts/{device_id}/anomaly
wifi/alerts/{device_id}/status
wifi/alerts/summary
```

## 5. Anomaly reason codes

| Code | Scenario | Recommended response |
| --- | --- | --- |
| `SEVERE_CONGESTION` | Severe congestion | Move the radio or clients to a cleaner channel. |
| `SEVERE_INTERFERENCE` | Severe interference | Investigate nearby APs and non-Wi-Fi interference. |
| `CO_CHANNEL_CONGESTION` | Co-channel congestion | Review channel assignments and select a less crowded channel. |
| `WEAK_CLIENTS` | Weak clients | Improve coverage, reposition the AP, or check client obstructions. |
| `HIGH_RETRIES_OR_FAILURES` | High packet retry or failure rate | Investigate signal quality, interference, and channel selection. |
| `HIGH_CLIENT_LOAD` | Too many active clients | Distribute clients across radios or access points. |
| `UNUSUAL_WIFI_CONDITIONS` | Unusual Wi-Fi conditions | Inspect the affected radio metrics and compare nearby APs. |

## 6. Validation checklist

- `bash -n scripts/start_linux.sh` passes.
- `python3 -m py_compile scripts/startup.py scripts/shutdown.py` passes.
- `helm lint helm/ai-flow` passes.
- `helm template ai-flow helm/ai-flow` renders successfully.
- All expected pods reach `Running` and `1/1 Ready`.
- Consumer logs show telemetry rows stored.
- Training logs show `model.pkl` saved.
- Inference logs show database and MQTT connections.
- Grafana loads the dashboard and displays telemetry after selecting a recent time range.
- `python3 scripts/shutdown.py` removes runtime resources after validation.
