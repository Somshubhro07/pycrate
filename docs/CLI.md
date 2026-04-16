# PyCrate CLI Reference

Command-line interface for the PyCrate container runtime.

---

## Installation

```bash
# From GitHub (recommended)
curl -sSL https://raw.githubusercontent.com/Somshubhro07/pycrate/main/install.sh | sudo bash

# From source
git clone https://github.com/Somshubhro07/pycrate.git
cd pycrate
pip install -e .

# With API server support
pip install -e ".[server]"
```

---

## Commands

### `pycrate run`

Create and start a container.

```bash
pycrate run <image> [command...] [options]
```

| Option | Short | Default | Description |
|---|---|---|---|
| `--name` | `-n` | auto | Container name |
| `--cpu` | `-c` | 50 | CPU limit (% of one core, 1-100) |
| `--memory` | `-m` | 64 | Memory limit in MB (min 4) |
| `--detach` | `-d` | false | Run in background |
| `--env` | `-e` | - | Environment variable (KEY=VALUE) |
| `--no-security` | - | false | Disable seccomp + capability hardening |

**Examples:**

```bash
# Interactive Alpine shell
sudo pycrate run alpine /bin/sh

# Ubuntu with resource limits
sudo pycrate run ubuntu:22.04 /bin/bash --name web --cpu 25 --memory 128

# Detached with env vars
sudo pycrate run alpine /bin/sh -c "echo hello" --detach --env MY_VAR=test

# Debug mode (no security restrictions)
sudo pycrate run alpine /bin/sh --no-security
```

---

### `pycrate ps`

List containers.

```bash
pycrate ps [options]
```

| Option | Short | Default | Description |
|---|---|---|---|
| `--all` | `-a` | true | Show all containers |
| `--status` | `-s` | - | Filter: created, running, stopped, error |

**Example output:**

```
 CONTAINER ID   NAME   IMAGE         STATUS    CPU   MEMORY   PID
 crate-a7f3b2   web    alpine:3.20   running   50%   64MB     12345
 crate-b4c8d1   api    ubuntu:22.04  stopped   25%   128MB
```

---

### `pycrate stop`

Stop a running container.

```bash
pycrate stop <container_id|name> [options]
```

| Option | Short | Default | Description |
|---|---|---|---|
| `--timeout` | `-t` | 10 | Seconds before SIGKILL |

Sends SIGTERM first, waits for graceful shutdown, then SIGKILL if timeout.

---

### `pycrate rm`

Remove a container and all its resources.

```bash
pycrate rm <container_id|name> [options]
```

| Option | Short | Default | Description |
|---|---|---|---|
| `--force` | `-f` | false | Stop the container first if running |

---

### `pycrate logs`

View container logs.

```bash
pycrate logs <container_id|name> [options]
```

| Option | Short | Default | Description |
|---|---|---|---|
| `--tail` | `-n` | all | Number of lines from the end |

---

### `pycrate inspect`

Show detailed container information.

```bash
pycrate inspect <container_id|name>
```

Shows: status, PID, image, CPU/memory limits, network IP, timestamps, errors.

---

### `pycrate pull`

Pull a base image and cache it locally.

```bash
pycrate pull <image>
```

**Supported images:**

| Image | Versions | Method | Size |
|---|---|---|---|
| `alpine` | 3.19, 3.20 | HTTP tarball | ~3MB |
| `ubuntu` | 22.04, 24.04 | debootstrap | ~150MB |
| `debian` | bookworm, bullseye | debootstrap | ~130MB |

```bash
sudo pycrate pull alpine          # pulls alpine:latest (3.20)
sudo pycrate pull alpine:3.19     # specific version
sudo pycrate pull ubuntu:22.04    # uses debootstrap
sudo pycrate pull debian:bookworm # uses debootstrap
```

---

### `pycrate images`

List cached images.

```bash
pycrate images
```

---

### `pycrate rmi`

Remove a cached image from disk.

```bash
pycrate rmi <image:version>
```

---

### `pycrate dashboard`

Start the API server and open the web dashboard.

```bash
pycrate dashboard [options]
```

| Option | Short | Default | Description |
|---|---|---|---|
| `--host` | - | 0.0.0.0 | Bind address |
| `--port` | `-p` | 8000 | Port number |
| `--no-browser` | - | false | Don't auto-open browser |

Requires `pycrate[server]` extras to be installed.

---

### `pycrate version`

Show PyCrate version and engine capabilities.

---

## Container Identification

Commands that accept a container identifier support:

1. **Full ID**: `crate-a7f3b2`
2. **Name**: `web`
3. **ID prefix**: `a7f3` (matches first container with matching prefix)

---

## Requirements

- Linux (Ubuntu 22.04+ recommended, WSL2 works)
- Python 3.11+
- Root privileges (sudo)
- cgroups v2 enabled (default on Ubuntu 22.04+)

### For Ubuntu/Debian images

- `debootstrap` package (`sudo apt-get install debootstrap`)

### For the web dashboard

- `pycrate[server]` extras (`pip install pycrate[server]`)
