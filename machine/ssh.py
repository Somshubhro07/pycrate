"""
SSH Client — Command Forwarding to PyCrate Machine
=====================================================

Pure-Python SSH client using ``paramiko`` for forwarding CLI commands
from the host (Windows/macOS) to the Linux VM. Also handles SSH key
generation and connection lifecycle.

Paramiko is the only external dependency added for cross-platform
support. It's pure Python — no system ``ssh`` binary needed.

Usage:
    client = SSHClient(host="127.0.0.1", port=2222, key_path=Path("~/.pycrate/machine_ed25519"))
    client.connect()
    exit_code, stdout, stderr = client.exec_command("pycrate ps")
    client.close()
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Connection defaults
CONNECT_TIMEOUT = 10
KEEPALIVE_INTERVAL = 30
MAX_RETRIES = 12       # 12 * 5s = 60s max wait for VM boot
RETRY_INTERVAL = 5


def generate_ssh_keypair(key_path: Path) -> Path:
    """Generate an ED25519 SSH keypair for machine authentication.

    Args:
        key_path: Path for the private key file (public key = key_path.pub).

    Returns:
        Path to the private key.
    """
    try:
        import paramiko
    except ImportError:
        raise RuntimeError(
            "paramiko is required for cross-platform support. "
            "Install with: pip install pycrate[machine]"
        )

    if key_path.exists():
        logger.debug("SSH key already exists at %s", key_path)
        return key_path

    key_path.parent.mkdir(parents=True, exist_ok=True)

    key = paramiko.Ed25519Key.generate()
    key.write_private_key_file(str(key_path))

    # Write public key
    pub_path = key_path.with_suffix(".pub")
    pub_key = f"{key.get_name()} {key.get_base64()} pycrate-machine"
    pub_path.write_text(pub_key)

    # Restrict permissions (best-effort on Windows)
    try:
        key_path.chmod(0o600)
        pub_path.chmod(0o644)
    except OSError:
        pass  # Windows doesn't support Unix permissions

    logger.info("Generated SSH keypair at %s", key_path)
    return key_path


def get_public_key(key_path: Path) -> str:
    """Read the public key string for injection into cloud-init."""
    pub_path = key_path.with_suffix(".pub")
    if pub_path.exists():
        return pub_path.read_text().strip()

    # Regenerate from private key
    import paramiko
    key = paramiko.Ed25519Key.from_private_key_file(str(key_path))
    return f"{key.get_name()} {key.get_base64()} pycrate-machine"


class SSHClient:
    """SSH client for executing commands inside the PyCrate Machine.

    Wraps paramiko with retry logic, keepalive, and streaming support.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 2222,
        username: str = "root",
        key_path: Path | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.key_path = key_path
        self._client = None

    def connect(self, retries: int = MAX_RETRIES) -> None:
        """Connect to the machine via SSH.

        Retries with exponential backoff until the VM is ready.
        """
        import paramiko

        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        for attempt in range(1, retries + 1):
            try:
                connect_kwargs = {
                    "hostname": self.host,
                    "port": self.port,
                    "username": self.username,
                    "timeout": CONNECT_TIMEOUT,
                    "allow_agent": False,
                    "look_for_keys": False,
                }

                if self.key_path and self.key_path.exists():
                    connect_kwargs["key_filename"] = str(self.key_path)

                self._client.connect(**connect_kwargs)
                self._client.get_transport().set_keepalive(KEEPALIVE_INTERVAL)

                logger.info("SSH connected to %s:%d", self.host, self.port)
                return

            except Exception as e:
                if attempt >= retries:
                    raise ConnectionError(
                        f"Could not connect to PyCrate Machine at "
                        f"{self.host}:{self.port} after {retries} attempts: {e}"
                    )

                logger.debug(
                    "SSH attempt %d/%d failed: %s. Retrying in %ds...",
                    attempt, retries, e, RETRY_INTERVAL,
                )
                time.sleep(RETRY_INTERVAL)

    def close(self) -> None:
        """Close the SSH connection."""
        if self._client:
            self._client.close()
            self._client = None

    @property
    def is_connected(self) -> bool:
        """Check if SSH connection is active."""
        if not self._client:
            return False
        transport = self._client.get_transport()
        return transport is not None and transport.is_active()

    def exec_command(self, command: str, timeout: int = 30) -> tuple[int, str, str]:
        """Execute a command and return (exit_code, stdout, stderr).

        For non-interactive commands that capture output.
        """
        if not self.is_connected:
            self.connect()

        _, stdout_ch, stderr_ch = self._client.exec_command(command, timeout=timeout)

        stdout = stdout_ch.read().decode("utf-8", errors="replace")
        stderr = stderr_ch.read().decode("utf-8", errors="replace")
        exit_code = stdout_ch.channel.recv_exit_status()

        return exit_code, stdout, stderr

    def exec_stream(self, command: str) -> int:
        """Execute a command and stream output to the terminal.

        For interactive commands where the user needs real-time output.
        Returns the exit code.
        """
        if not self.is_connected:
            self.connect()

        transport = self._client.get_transport()
        channel = transport.open_session()
        channel.exec_command(command)

        # Stream stdout and stderr
        while not channel.exit_status_ready():
            if channel.recv_ready():
                data = channel.recv(4096)
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()
            if channel.recv_stderr_ready():
                data = channel.recv_stderr(4096)
                sys.stderr.buffer.write(data)
                sys.stderr.buffer.flush()
            time.sleep(0.01)

        # Drain remaining output
        while channel.recv_ready():
            sys.stdout.buffer.write(channel.recv(4096))
        while channel.recv_stderr_ready():
            sys.stderr.buffer.write(channel.recv_stderr(4096))

        sys.stdout.flush()
        sys.stderr.flush()

        return channel.recv_exit_status()

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.close()
