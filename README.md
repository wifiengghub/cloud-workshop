# DeviceDataHub AI flow

End-to-end Wi-Fi anomaly detection running as Kubernetes workloads:

```text
MQTT over ngrok -> consumer -> TimescaleDB -> Grafana
                                      ^
                         train_1000.json -> train.py -> model.pkl
                                                        |
                                      inference.py -> MQTT alerts
```

## Start On Linux

```bash
chmod +x start_linux.sh
./start_linux.sh
```

The launcher uses `$HOME/workspaces` as the WSL workspace root, creates it when
needed, and clones:

```text
https://github.com/arnabnexus/devicedatahub-end-to-end.git
```

If `$HOME/workspaces/devicedatahub-end-to-end` already exists as a Git
repository, it prints a clone-skipped message, runs `git pull --ff-only`, and
starts from the updated project. Override the location or repository when
needed:

```bash
WORKSPACE_DIR="$HOME/workspaces" \
REPO_URL="https://github.com/arnabnexus/devicedatahub-end-to-end.git" \
./start_linux.sh
```

`start_linux.sh` performs the complete startup sequence:


2. Installs and starts Docker Engine when it is missing or stopped.
3. Creates `.venv` if it does not exist.
4. Activates the virtual environment.
5. Installs `requirements.txt`.
6. Asks whether to enable the local MQTT simulator.
7. Runs `startup.py --no-follow` with `values.simulate.yaml` only when enabled.
8. Creates or reuses the `devicedatahub` kind cluster.
9. Builds `devicedatahub-ai-flow:latest`.
10. Loads the image into the kind nodes.
11. Installs or upgrades the `helm/ai-flow` chart.
12. Starts TimescaleDB, Grafana, the MQTT consumer, and inference pods.
13. Trains the model from `data/train_1000.json` in the inference pod init container.
14. Starts `inference.py` only after training and database readiness succeed.
15. Starts Grafana and TimescaleDB port-forward processes.

GitHub CLI is not required for this public HTTPS repository. `git clone` works
without `gh auth login`. GitHub CLI authentication is only needed if you change
`REPO_URL` to a private repository or use an SSH/private GitHub workflow.

The script supports graphical terminals. In WSL or a headless Linux shell it
runs the port-forwards in the background and writes their logs and PIDs under
`.runtime/`.

Answer `Yes` to the simulator prompt to deploy Mosquitto and `simulator.py` as
Kubernetes workloads. The simulator publishes randomized normal and anomaly
telemetry to the same topic consumed by the existing consumer. Answer `No` to
keep the ngrok MQTT flow unchanged. See [README.simulate.md](README.simulate.md)
for simulator settings.

## Grafana And Database Access

Grafana is available at:

```text
http://localhost:3000
```

Default Grafana credentials:

```text
Username: admin
Password: admin
```

The dashboard is imported from `grafana/dashboard.json` and uses the
provisioned `TimescaleDB` datasource.

TimescaleDB is forwarded to:

```text
Host: localhost
Port: 5433
Database: telemetry
Username: postgres
Password: postgres
```

The equivalent manual commands are:

```bash
kubectl port-forward service/ai-flow-grafana 3000:3000 \
  --namespace=devicedatahub
```

```bash
kubectl port-forward service/ai-flow-timescaledb 5433:5432 \
  --namespace=devicedatahub
```

Keep each command running in its own terminal. In VS Code, configure a
PostgreSQL/TimescaleDB extension with host `localhost`, port `5433`, database
`telemetry`, user `postgres`, and password `postgres`.

## Verify The Pods

Use the namespace explicitly:

```bash
kubectl get pods -n devicedatahub -o wide
kubectl get services -n devicedatahub
```

Follow the consumer and inference logs:

```bash
kubectl logs -n devicedatahub deploy/ai-flow-consumer -f
kubectl logs -n devicedatahub deploy/ai-flow-inference -c inference -f
kubectl logs -n devicedatahub deploy/ai-flow-inference -c train-model --tail=200
```

The expected flow is:

```text
consumer: Connected to broker
consumer: Telemetry table ready
consumer: Stored Wi-Fi telemetry rows
train-model: Model successfully saved to model_artifacts/model.pkl
inference: Model loaded
inference: TimescaleDB connection established
inference: MQTT connected
inference: Published anomaly message
```

Anomaly messages are published to:

```text
wifi/alerts/{device_id}/anomaly
wifi/alerts/{device_id}/status
wifi/alerts/summary
```

Messages include the reason code, training-aligned scenario, layman
explanation, recommended action, metrics, and publish result.

## Configuration

Copy the example environment file for reference:

```bash
cp .env.example .env
```

For Kubernetes, use a private Helm values file for broker and credential
settings. Do not commit `.env` or private values files.

The main settings are:

```yaml
mqtt:
  host: 0.tcp.in.ngrok.io
  port: 24839
  topic: weh-device/network
alerts:
  host: 0.tcp.in.ngrok.io
  port: 24839
  baseTopic: wifi/alerts
database:
  name: telemetry
  user: postgres
  password: postgres
```

## Manual Kubernetes Commands

Build and load the image:

```bash
docker build -t devicedatahub-ai-flow:latest .
kind load docker-image devicedatahub-ai-flow:latest --name devicedatahub
```

Deploy the chart:

```bash
helm upgrade --install ai-flow helm/ai-flow \
  --namespace devicedatahub \
  --create-namespace \
  --set image.repository=devicedatahub-ai-flow \
  --set image.tag=latest \
  --wait \
  --timeout 10m
```

## Shutdown

After validation is complete, run the cleanup command:

```bash
python3 shutdown.py
```

This stops the port-forward processes, uninstalls the Helm release, deletes the
namespace, deletes the kind cluster, removes the local image, and removes the
project virtual environment.

Preserve selected local resources when needed:

```bash
python3 shutdown.py --keep-cluster
python3 shutdown.py --keep-image
python3 shutdown.py --keep-venv
```

## Project Files

- [start_linux.sh](start_linux.sh): Linux/WSL startup and port forwarding
- [startup.py](startup.py): kind, kubectl, Helm, image, and deployment bootstrap
- [shutdown.py](shutdown.py): process, Kubernetes, and local cleanup
- [helm/ai-flow](helm/ai-flow): complete Kubernetes chart
- [ARCHITECTURE.md](ARCHITECTURE.md): component and data-flow diagram
- [DESIGN_REQUIREMENTS.md](DESIGN_REQUIREMENTS.md): application requirements and acceptance criteria
- [README.simulate.md](README.simulate.md): local MQTT simulator operation
