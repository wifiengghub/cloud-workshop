# DeviceDataHub Workshop Prerequisites

This guide prepares a Windows laptop running Ubuntu on WSL2 to run the
DeviceDataHub AI flow entirely in Kubernetes.

At the end, you will be in a WSL terminal inside the repository and ready to
run:

```bash
./scripts/start_linux.sh
```

The flow starts an MQTT consumer, TimescaleDB, Grafana, model training,
inference, and optional local MQTT simulation.

## 1. Hardware and Windows prerequisites

### Required hardware

- 64-bit Windows 10 version 2004 or newer, or Windows 11
- CPU virtualization support: Intel VT-x or AMD-V
- At least 8 GB RAM; 16 GB is recommended
- At least 20 GB free disk space
- Administrator access to Windows
- Internet access for Windows features, Ubuntu packages, GitHub, Docker Engine, and Kubernetes images

### Enable virtualization in BIOS/UEFI

Virtualization must be enabled before WSL2 can run reliably.

1. Save your work and restart the laptop.
2. Enter BIOS/UEFI during boot. Common keys are `F2`, `F10`, `F12`, `Delete`, or `Esc`.
3. Open a menu named **Advanced**, **Security**, **CPU Configuration**, or **Virtualization**.
4. Enable the matching setting:
   - Intel: `Intel Virtualization Technology`, `VT-x`, or `Intel VT-d`
   - AMD: `SVM Mode`, `AMD-V`, or `Secure Virtual Machine`
5. Choose **Save and Exit**.
6. Let Windows boot normally.

The exact menu names depend on the laptop manufacturer. If Windows Task
Manager already shows **Virtualization: Enabled**, this step is complete.

### Verify virtualization in Windows

Open **PowerShell as Administrator** and run:

```powershell
systeminfo.exe
```

Look near the Hyper-V requirements. Virtualization-based requirements should
not be reported as unavailable.

You can also check:

1. Press `Ctrl+Shift+Esc`.
2. Open **Performance**.
3. Select **CPU**.
4. Confirm **Virtualization: Enabled**.

## 2. Enable Windows WSL features

Open **PowerShell as Administrator** and run:

```powershell
wsl --install
```

Restart Windows if prompted.

If WSL is already installed, explicitly enable the required Windows features:

```powershell
 dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart
 dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart
```

Restart Windows after enabling the features:

```powershell
Restart-Computer
```

After Windows restarts, set WSL2 as the default:

```powershell
wsl --set-default-version 2
wsl --update
wsl --status
```

Expected output should identify WSL2 as the default version.

## 3. Install Ubuntu for WSL

List available distributions:

```powershell
wsl --list --online
```

Install Ubuntu:

```powershell
wsl --install -d Ubuntu
```

If Ubuntu is already installed, list the installed distributions:

```powershell
wsl --list --verbose
```

The Ubuntu distribution should show:

```text
VERSION 2
```

If it shows version 1, convert it:

```powershell
wsl --set-version Ubuntu 2
```

Launch Ubuntu from the Start menu or PowerShell:

```powershell
wsl -d Ubuntu
```

## 4. Complete the first Ubuntu login

On the first Ubuntu launch, create a Linux username and password.

These credentials are separate from your Windows account. The password is used
for `sudo` commands and will not be displayed while typing.

After login, confirm that you are inside Linux:

```bash
whoami
pwd
cat /etc/os-release
```

You should see Ubuntu details and a Linux home directory similar to:

```text
/home/<your-linux-user>
```

## 5. Prepare Ubuntu packages

The project startup script installs missing packages automatically, but install
the base package set once so the workshop starts predictably:

```bash
sudo apt-get update
sudo apt-get upgrade -y
sudo apt-get install -y \
  git \
  curl \
  ca-certificates \
  python3 \
  python3-venv \
  python3-pip \
  docker.io
```

Verify the tools:

```bash
git --version
python3 --version
python3 -m venv --help >/dev/null && echo "python venv support: OK"
docker --version
```

## 6. Enable WSL systemd for Docker Engine

The project can start Docker Engine through systemd, the service command, or a
background `dockerd` fallback. Systemd is the most reliable WSL setup.

Create or edit `/etc/wsl.conf` inside Ubuntu:

```bash
sudo nano /etc/wsl.conf
```

Add:

```ini
[boot]
systemd=true
```

Save in nano with `Ctrl+O`, press Enter, then exit with `Ctrl+X`.

Close all Ubuntu/WSL terminals. From **PowerShell**, restart WSL:

```powershell
wsl --shutdown
```

Open Ubuntu again and verify systemd:

```bash
ps -p 1 -o comm=
```

Expected output:

```text
systemd
```

Enable and start Docker Engine:

```bash
sudo systemctl enable --now docker
```

Verify Docker Engine:

```bash
sudo systemctl status docker --no-pager
sudo docker info
```

Allow the current Linux user to run Docker without `sudo`:

```bash
sudo usermod -aG docker "$USER"
```

Close the Ubuntu terminal, open a new Ubuntu terminal, and verify:

```bash
docker info
```

If `docker info` works without `sudo`, Docker Engine is ready.

If systemd is not available, the project launcher attempts `service docker
start`, then a background `dockerd` fallback. If that fallback fails, inspect:

```bash
cat /tmp/devicedatahub-dockerd.log
```

## 7. Verify WSL networking and Docker

Run:

```bash
printf 'Linux user: '; whoami
printf 'Linux home: '; printf '%s\n' "$HOME"
printf 'WSL IP: '; hostname -I
git --version
python3 --version
docker version
docker info >/dev/null && echo "Docker daemon: OK"
```

The Docker client and server sections should both be present in `docker version`.

Run a test container:

```bash
docker run --rm hello-world
```

This confirms that Docker can pull and run images.

## 8. Configure Git identity

Configure a Git identity for local commits if this laptop will be used for
workshop changes:

```bash
git config --global user.name "Your Name"
git config --global user.email "your-email@example.com"
```

GitHub CLI is optional. This project uses a public HTTPS repository, so cloning
does not require `gh auth login`.

For private repositories, install and authenticate GitHub CLI separately:

```bash
sudo apt-get install -y gh
gh auth login
```

## 9. Clone the repository

Create the standard workspace directory:

```bash
mkdir -p "$HOME/workspaces"
cd "$HOME/workspaces"
```

Clone the repository:

```bash
git clone https://github.com/arnabnexus/devicedatahub-end-to-end.git
cd devicedatahub-end-to-end
```

Verify the repository:

```bash
git remote -v
git status
find src config scripts helm data -maxdepth 2 -type f | sort
```

The important layout is:

```text
Dockerfile
.dockerignore
docker-compose.yml
requirements.txt
src/consumer/
src/ai/
config/
scripts/
helm/ai-flow/
data/
```

Docker files and `requirements.txt` intentionally remain at the repository root
because the repository root is the Docker build context.

## 10. Make the Linux launcher executable

Run:

```bash
chmod +x scripts/start_linux.sh
```

Confirm the script is executable:

```bash
ls -l scripts/start_linux.sh
bash -n scripts/start_linux.sh
```

The mode should contain `x`, for example:

```text
-rwxr-xr-x
```

## 11. Verify the startup dependencies before launch

Run this final preflight:

```bash
command -v git
command -v python3
command -v docker
command -v curl
python3 -m venv --help >/dev/null && echo "venv: OK"
docker info >/dev/null && echo "Docker Engine: OK"
git ls-remote https://github.com/arnabnexus/devicedatahub-end-to-end.git HEAD
```

If the last command prints a commit hash, GitHub access is working.

## 12. Start the workshop flow

You are now in the repository and ready to run:

```bash
./scripts/start_linux.sh
```

The launcher will:

1. Check or update `$HOME/workspaces/devicedatahub-end-to-end`.
2. Create and activate `.venv`.
3. Install Python requirements.
4. Install Docker Engine if missing.
5. Ask whether to enable the local MQTT simulator.
6. Create or repair the kind cluster.
7. Build and load the application image.
8. Deploy the Helm release.
9. Train the model in the inference init container.
10. Start the consumer, inference, TimescaleDB, and Grafana workloads.
11. Start local port-forwards.
12. Stream Kubernetes logs.

At the simulator prompt:

```text
Enable local MQTT simulator and broker? [y/N]:
```

- Enter `y` or `yes` to start Mosquitto and randomized simulated telemetry.
- Press Enter or enter `n` to use the configured ngrok broker.

Grafana will be available at:

```text
http://localhost:3000
```

TimescaleDB will be available to local tools at:

```text
Host: localhost
Port: 5433
Database: telemetry
User: postgres
Password: postgres
```

## 13. Verify after startup

In another WSL terminal:

```bash
cd "$HOME/workspaces/devicedatahub-end-to-end"
export PATH="$HOME/.local/bin:$PATH"

kubectl get pods -n devicedatahub -o wide
kubectl get services -n devicedatahub
kubectl logs -n devicedatahub deploy/ai-flow-consumer --tail=50
kubectl logs -n devicedatahub deploy/ai-flow-inference -c inference --tail=50
```

Expected pod state:

```text
Running
1/1 Ready
```

Expected log milestones:

```text
Telemetry table ready
Stored wifi telemetry rows
Model loaded
TimescaleDB connection established
MQTT connected
Published anomaly message
```

## 14. Shutdown after the workshop

When validation is complete, stop all local port-forwards and Kubernetes
resources:

```bash
cd "$HOME/workspaces/devicedatahub-end-to-end"
python3 scripts/shutdown.py
```

This removes the Helm release, namespace, kind cluster, Docker image, project
virtual environment, simulator resources, and background port-forward
processes.

## Troubleshooting

### Virtualization is disabled

Return to BIOS/UEFI and enable Intel VT-x or AMD SVM/AMD-V. Then restart
Windows and verify virtualization in Task Manager.

### WSL reports version 1

From PowerShell:

```powershell
wsl --set-default-version 2
wsl --set-version Ubuntu 2
```

### Docker permission denied

Run:

```bash
sudo usermod -aG docker "$USER"
```

Close and reopen Ubuntu, then test:

```bash
docker info
```

### Docker daemon cannot start

Check:

```bash
sudo systemctl status docker --no-pager
cat /tmp/devicedatahub-dockerd.log
```

### Git clone fails

Check connectivity:

```bash
git ls-remote https://github.com/arnabnexus/devicedatahub-end-to-end.git HEAD
```

### The repository folder already exists

The launcher updates an existing Git clone with `git pull --ff-only`. If the
folder is not a Git repository, move it or choose a different workspace:

```bash
WORKSPACE_DIR="$HOME/another-workspace" ./scripts/start_linux.sh
```

### Grafana shows no data

Use a recent time range such as **Last 15 minutes**, then inspect:

```bash
kubectl logs -n devicedatahub deploy/ai-flow-consumer --tail=100
kubectl logs -n devicedatahub deploy/ai-flow-grafana --tail=100
```

The Grafana datasource must use:

```text
Host: ai-flow-timescaledb:5432
Database: telemetry
User: postgres
Password: postgres
```
