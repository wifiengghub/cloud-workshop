# Local MQTT simulation

The simulation profile is opt-in. It starts Mosquitto inside Kubernetes and a
`simulator.py` pod that publishes randomized normal and anomalous Wi-Fi payloads
to the same topic consumed by `consumer.py`.

Use `./start_linux.sh` and answer `Yes` when asked to enable local simulation.
Answering `No` preserves the normal ngrok MQTT flow.

Simulation resources:

```text
ai-flow-mqtt-broker  MQTT Service on 1883
ai-flow-simulator    Random telemetry publisher
consumer             Existing telemetry consumer
```

The simulator publishes:

```text
weh-device/network
```

The consumer writes those messages to TimescaleDB, and inference publishes
anomaly explanations to:

```text
wifi/alerts/{device_id}/anomaly
wifi/alerts/{device_id}/status
wifi/alerts/summary
```

Override simulator settings in `helm/ai-flow/values.simulate.yaml`:

```yaml
simulator:
  intervalSeconds: 5
  anomalyRate: 0.35
```

Run `python3 shutdown.py` to remove the broker, simulator, pods, Helm release,
namespace, kind cluster, image, and local runtime processes.
