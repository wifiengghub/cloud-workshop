# Kubernetes startup guide

This project runs the complete AI flow as Kubernetes pods:

```text
MQTT over ngrok
    -> consumer pod
    -> TimescaleDB pod
    -> Grafana pod
    -> inference pod init container trains on 1,000 records
    -> inference.py publishes MQTT alerts
```

The supported startup path is `startup.py`. It creates a local kind cluster,
builds the image, loads it into kind, installs the required Kubernetes tools,
installs the Helm chart, waits for the pods, and follows their logs.

## Prerequisites

On a raw Ubuntu WSL install, `start_linux.sh` installs missing Git, Python,
`python3-venv`, certificates, and curl packages using `sudo apt-get`. It also
downloads `kind`, `kubectl`, and `helm` into `~/.local/bin` when they are not
already installed.

The launcher installs the Ubuntu `docker.io` package when Docker is missing and
starts Docker Engine with systemd, the service command, or a background
`dockerd` fallback. Your WSL distribution must support the Docker Engine
kernel/cgroup requirements; if the daemon cannot start, inspect
`/tmp/devicedatahub-dockerd.log`.

GitHub CLI is optional. This public HTTPS repository clones with Git and does
not require `gh auth login`. Use GitHub CLI authentication only for a private
repository or private SSH workflow.

Verify Docker and Python:

```bash
docker info
python3 --version
```

The Docker daemon must be running before the script starts.

## 1. Configure MQTT

From the repository root:

```bash
cp .env.example .env
```

Edit `.env` with the broker values that apply to your ngrok tunnel:

```text
MQTT_BROKER_HOST=0.tcp.in.ngrok.io
MQTT_BROKER_PORT=24839
MQTT_TOPIC=weh-device/network
MQTT_HOST=0.tcp.in.ngrok.io
MQTT_PORT=24839
MQTT_BASE_TOPIC=wifi/alerts
```

The Helm command reads these values only when they are supplied through Helm
values. The easiest approach is to create a private `kubernetes-values.yaml`:

```yaml
mqtt:
  host: 0.tcp.in.ngrok.io
  port: 24839
  topic: weh-device/network
alerts:
  host: 0.tcp.in.ngrok.io
  port: 24839
  baseTopic: wifi/alerts
```

Do not commit real credentials.

## 2. Create the virtual environment

Run the launcher once. It creates `.venv`, upgrades pip, and installs the
merged Python requirements:

```bash
python3 startup.py --no-build --no-follow
```

The command also continues into Kubernetes startup. To activate the venv in
your current shell afterward:

```bash
source .venv/bin/activate
```

The Python venv is for local tooling and validation. The application itself
runs inside the Kubernetes image and Kubernetes pods.

## 3. Install kind, kubectl, and Helm

`startup.py` automatically downloads missing Linux binaries into:

```text
~/.local/bin
```

Make that directory available in future shells:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

The script checks or installs:

- `kind` for the local Kubernetes cluster
- `kubectl` for pod status and logs
- `helm` for deploying the chart

## 4. Build the image and start Kubernetes

Run:

```bash
python3 startup.py
```

For a Linux workstation, the complete one-command launcher is:

```bash
chmod +x start_linux.sh
./start_linux.sh
```

The launcher checks `$HOME/workspaces` when you log in to WSL. It creates that
directory if needed, clones the GitHub repository on the first run, and prints
a clone-skipped message before running `git pull --ff-only` on later runs:

```text
$HOME/workspaces/devicedatahub-end-to-end
```

Override the repository or workspace location with `REPO_URL` and
`WORKSPACE_DIR`:

```bash
WORKSPACE_DIR="$HOME/workspaces" \
REPO_URL="https://github.com/arnabnexus/devicedatahub-end-to-end.git" \
./start_linux.sh
```

It creates and activates `.venv`, installs `requirements.txt`, runs
`startup.py --no-follow`, then opens two child terminal windows, or starts two
detached background processes in a headless shell, for:

Before deployment it asks:

```text
Enable local MQTT simulator and broker? [y/N]:
```

Answer `Yes` to deploy the local Mosquitto broker and `simulator.py`. The
simulator publishes randomized normal and anomaly payloads to the consumer.
Answer `No` to preserve the normal ngrok MQTT flow.

```bash
kubectl port-forward service/ai-flow-grafana 3000:3000 \
  --namespace=devicedatahub
kubectl port-forward service/ai-flow-timescaledb 5433:5432 \
  --namespace=devicedatahub
```

Open Grafana at [http://localhost:3000](http://localhost:3000). If no graphical
terminal emulator is installed, the forwards run in the background and write
logs and PIDs under `.runtime/`. Use `python3 shutdown.py` to stop them.

The script performs these steps:

1. Creates or reuses the `devicedatahub` kind cluster.
2. Builds `devicedatahub-ai-flow:latest` with the root Dockerfile.
3. Loads that image into the kind nodes.
4. Creates the `devicedatahub` namespace.
5. Installs or upgrades the `helm/ai-flow` chart.
6. Creates TimescaleDB and Grafana pods.
7. Starts the MQTT consumer pod.
8. Runs `train.py` in the inference pod's init container using
   `data/train_1000.json`.
9. Starts `inference.py` only after training succeeds.
10. Follows logs from all AI flow pods.

Use `Ctrl+C` to stop following logs. It does not delete the cluster or pods.

To reuse an already-built image:

```bash
python3 startup.py --no-build
```

To pass private broker or database settings into Helm:

```bash
python3 startup.py --values kubernetes-values.yaml
```

To start pods and return after displaying their status:

```bash
python3 startup.py --no-follow
```

To delete the local kind cluster:

```bash
python3 startup.py --delete-cluster
```

## Stop and clean everything

After starting the flow with `startup.py`, run:

```bash
python3 shutdown.py
```

This removes the `ai-flow` Helm release, deletes the `devicedatahub` namespace,
deletes the `devicedatahub` kind cluster, removes the local Docker image, and
removes the project `.venv`. Downloaded shared tools in `~/.local/bin` are kept.

Preserve selected local resources:

```bash
python3 shutdown.py --keep-cluster
python3 shutdown.py --keep-image
python3 shutdown.py --keep-venv
```

Remove the downloaded `kind`, `kubectl`, and `helm` binaries too:

```bash
python3 shutdown.py --remove-tools
```

The Kubernetes cleanup order is Helm release, namespace, then kind cluster, so
the pods, Services, ConfigMaps, Secrets, and PVCs created by the chart are
removed before the cluster is deleted.

## 5. Check pod status and logs

In another terminal, activate the tools path if needed:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

List all pods:

```bash
kubectl get pods -n devicedatahub -o wide
```

Expected workloads include:

```text
ai-flow-timescaledb-0
ai-flow-consumer-...
ai-flow-inference-...
ai-flow-grafana-...
```

Check the complete logs:

```bash
kubectl logs -n devicedatahub \
  -l app.kubernetes.io/instance=ai-flow \
  --all-containers=true \
  --prefix \
  --tail=100
```

Follow only the consumer:

```bash
kubectl logs -n devicedatahub deploy/ai-flow-consumer -f
```

Follow training and inference:

```bash
kubectl logs -n devicedatahub deploy/ai-flow-inference \
  -c train-model --tail=100
kubectl logs -n devicedatahub deploy/ai-flow-inference \
  -c inference -f
```

The training container must complete successfully before the `inference`
container starts. A successful training log ends with a model saved at
`model_artifacts/model.pkl`.

## 6. Check Grafana

Get the Grafana NodePort:

```bash
kubectl get svc ai-flow-grafana -n devicedatahub
```

With the default chart values, open:

```text
http://localhost:30300
```

If the NodePort is not reachable from the host, use port forwarding instead:

```bash
kubectl port-forward service/ai-flow-grafana 3000:3000 \
  --namespace=devicedatahub
```

Then open [http://localhost:3000](http://localhost:3000) in your browser. Keep
the port-forward command running while using Grafana.

To connect to TimescaleDB from a local SQL client, use:

```bash
kubectl port-forward service/ai-flow-timescaledb 5433:5432 \
  --namespace=devicedatahub
```

Connect with host `localhost`, port `5433`, database `telemetry`, user
`postgres`, and the configured database password. If port `5433` is already in
use, choose another local port, such as `55432:5432`.

The dashboard and TimescaleDB datasource are provisioned from the JSON and YAML
files in the Helm chart. The default login is `admin` / `admin` unless changed
in `helm/ai-flow/values.yaml` or a private values file.

Use a recent dashboard time range such as **Last 15 minutes**. The consumer
must also be receiving messages; verify with:

```bash
kubectl logs -n devicedatahub deploy/ai-flow-consumer --tail=50
```

## 7. Confirm the data path

Consumer logs should show an MQTT connection and telemetry inserts:

```bash
kubectl logs -n devicedatahub deploy/ai-flow-consumer --tail=100
```

Inference logs should show database polling and alert publishing:

```bash
kubectl logs -n devicedatahub deploy/ai-flow-inference \
  -c inference --tail=100
```

Expected alert topics are:

```text
wifi/alerts/{device_id}/anomaly
wifi/alerts/{device_id}/status
wifi/alerts/summary
```

## Troubleshooting

**`ImagePullBackOff`**

Run the image load step again:

```bash
kind load docker-image devicedatahub-ai-flow:latest --name devicedatahub
kubectl delete pod -n devicedatahub -l app.kubernetes.io/instance=ai-flow
```

**Training container fails**

Inspect the init container directly:

```bash
kubectl logs -n devicedatahub deploy/ai-flow-inference \
  -c train-model --tail=200
```

**Consumer cannot connect to MQTT**

Verify the ngrok tunnel, host, port, and topic in the Helm values. Then inspect
consumer logs. The broker must be reachable from inside the kind node network.

**Inference cannot connect to TimescaleDB**

Check the database pod and service:

```bash
kubectl get pod,svc -n devicedatahub
kubectl logs -n devicedatahub statefulset/ai-flow-timescaledb --tail=100
```

**Restart everything without deleting the cluster**

```bash
helm uninstall ai-flow -n devicedatahub
python3 startup.py --no-build
```