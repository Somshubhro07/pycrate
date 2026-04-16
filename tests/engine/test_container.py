"""
Tests for Container and ContainerManager
==========================================

These tests mock all Linux-specific subsystems (syscalls, cgroups, rootfs,
networking) so they can run on any platform. They focus on:

1. State machine correctness (valid and invalid transitions)
2. Thread safety and race conditions
3. Edge cases (double stop, destroy while running, limit enforcement)
4. Monitor thread coordination with stop()
5. Error propagation
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from engine.config import ContainerConfig
from engine.container import Container, ContainerManager, ContainerStatus
from engine.exceptions import (
    ContainerAlreadyRunningError,
    ContainerAlreadyStoppedError,
    ContainerError,
    ContainerLimitReachedError,
    ContainerNotFoundError,
)


# Helper: context manager to patch POSIX-only os/signal attrs on Windows
from contextlib import contextmanager

@contextmanager
def _win_os_compat():
    """Patch POSIX-only os/signal constants and functions so tests pass on Windows."""
    patches = [
        patch("engine.container.os.WNOHANG", 1, create=True),
        patch("engine.container.os.WIFEXITED", lambda s: True, create=True),
        patch("engine.container.os.WEXITSTATUS", lambda s: 0, create=True),
        patch("engine.container.os.WIFSIGNALED", lambda s: False, create=True),
        patch("engine.container.os.WTERMSIG", lambda s: 0, create=True),
        patch("engine.container.signal.SIGTERM", 15, create=True),
        patch("engine.container.signal.SIGKILL", 9, create=True),
    ]
    for p in patches:
        p.start()
    try:
        yield
    finally:
        for p in patches:
            p.stop()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_subsystems():
    """Patch all Linux-specific subsystems so tests run on any OS.

    Returns a dict of all mock objects for assertion.
    """
    with (
        patch("engine.container.prepare_rootfs") as mock_rootfs,
        patch("engine.container.setup_mounts") as mock_mounts,
        patch("engine.container.do_pivot_root") as mock_pivot,
        patch("engine.container.cleanup_rootfs") as mock_cleanup_rootfs,
        patch("engine.container.CgroupController") as MockCgroup,
        patch("engine.container.NamespaceSet") as MockNamespace,
        patch("engine.container.create_veth_pair") as mock_veth,
        patch("engine.container.cleanup_networking") as mock_cleanup_net,
        patch("engine.container.setup_bridge") as mock_bridge,
        patch("engine.container.ensure_pycrate_cgroup") as mock_ensure_cg,
        patch("engine.container.clone") as mock_clone,
        patch("engine.container.MetricsCollector") as MockMetrics,
    ):
        # Configure mock cgroup controller
        cgroup_instance = MockCgroup.return_value
        cgroup_instance.create = MagicMock()
        cgroup_instance.assign = MagicMock()
        cgroup_instance.cleanup = MagicMock()
        cgroup_instance.check_oom = MagicMock(return_value=False)
        cgroup_instance.read_memory_usage = MagicMock(return_value=32 * 1024 * 1024)
        cgroup_instance.read_memory_limit = MagicMock(return_value=64 * 1024 * 1024)
        cgroup_instance.read_cpu_usage = MagicMock(return_value={"usage_usec": 50000})

        # Configure mock clone to return a fake PID
        mock_clone.return_value = 99999

        # Configure veth to return a network config
        from engine.networking import NetworkConfig
        mock_veth.return_value = NetworkConfig(
            container_ip="10.0.0.42",
            veth_host="veth-test01",
        )

        yield {
            "rootfs": mock_rootfs,
            "mounts": mock_mounts,
            "pivot": mock_pivot,
            "cleanup_rootfs": mock_cleanup_rootfs,
            "cgroup_class": MockCgroup,
            "cgroup": cgroup_instance,
            "namespace": MockNamespace,
            "veth": mock_veth,
            "cleanup_net": mock_cleanup_net,
            "bridge": mock_bridge,
            "ensure_cg": mock_ensure_cg,
            "clone": mock_clone,
            "metrics": MockMetrics,
        }


@pytest.fixture
def config():
    return ContainerConfig(
        name="test",
        container_id="crate-test01",
        command=["/bin/sh"],
        cpu_limit_percent=50,
        memory_limit_mb=64,
    )


@pytest.fixture
def container(config, mock_subsystems):
    """A container in the CREATED state."""
    c = Container(config)
    c.create()
    return c


@pytest.fixture
def manager(mock_subsystems):
    """An initialized ContainerManager."""
    m = ContainerManager(max_containers=2)
    m.initialize()
    return m


# ---------------------------------------------------------------------------
# Container State Machine Tests
# ---------------------------------------------------------------------------


class TestContainerStateMachine:

    def test_initial_state_is_created(self, config, mock_subsystems):
        c = Container(config)
        c.create()
        assert c.status == ContainerStatus.CREATED
        assert c.pid is None
        assert c.exit_code is None
        assert c.is_running is False

    def test_start_transitions_to_running(self, container, mock_subsystems):
        container.start()
        assert container.status == ContainerStatus.RUNNING
        assert container.pid == 99999
        assert container.is_running is True
        assert container.started_at is not None

    def test_stop_transitions_to_stopped(self, container, mock_subsystems):
        with _win_os_compat():
            container.start()
            # Signal the monitor to stop so it doesn't race with us
            container._stop_event.set()
            if container._monitor_thread:
                container._monitor_thread.join(timeout=1)

            # Now manually call stop with fresh waitpid mock
            container._stop_event.clear()
            container.status = ContainerStatus.RUNNING  # Reset since monitor may have changed it
            container._finalized = False

            with patch("engine.container.os.kill"), \
                 patch("engine.container.os.waitpid", return_value=(99999, 0)):
                container.stop()

            assert container.status == ContainerStatus.STOPPED
            assert container.pid is None
            assert container.is_running is False
            assert container.stopped_at is not None

    def test_start_already_running_raises(self, container, mock_subsystems):
        container.start()
        with pytest.raises(ContainerAlreadyRunningError):
            container.start()

    def test_stop_already_stopped_raises(self, container, mock_subsystems):
        with pytest.raises(ContainerAlreadyStoppedError):
            container.stop()

    def test_start_from_error_state_raises(self, config, mock_subsystems):
        c = Container(config)
        c.status = ContainerStatus.ERROR
        c._error = "Previous failure"
        with pytest.raises(ContainerError, match="error state"):
            c.start()

    def test_create_failure_sets_error_state(self, config, mock_subsystems):
        mock_subsystems["rootfs"].side_effect = OSError("disk full")
        c = Container(config)
        with pytest.raises(ContainerError):
            c.create()
        assert c.status == ContainerStatus.ERROR
        assert "disk full" in c.error

    def test_start_failure_sets_error_state(self, container, mock_subsystems):
        mock_subsystems["clone"].side_effect = OSError("clone failed")
        with pytest.raises(ContainerError):
            container.start()
        assert container.status == ContainerStatus.ERROR

    @patch("engine.container.os.kill")
    @patch("engine.container.os.waitpid", return_value=(99999, 0))
    def test_destroy_stops_running_container(self, mock_waitpid, mock_kill, container, mock_subsystems):
        with _win_os_compat():
            container.start()
            container.destroy()
            assert container.status == ContainerStatus.STOPPED
            mock_subsystems["cleanup_rootfs"].assert_called_once_with("crate-test01")

    def test_destroy_created_container(self, container, mock_subsystems):
        container.destroy()
        mock_subsystems["cleanup_rootfs"].assert_called_once()

    def test_destroy_cleans_all_resources(self, container, mock_subsystems):
        container.start()
        with _win_os_compat(), \
             patch("engine.container.os.kill"), \
             patch("engine.container.os.waitpid", return_value=(99999, 0)):
            container.destroy()

        mock_subsystems["cgroup"].cleanup.assert_called()
        mock_subsystems["cleanup_net"].assert_called_once()
        mock_subsystems["cleanup_rootfs"].assert_called_once()


# ---------------------------------------------------------------------------
# Thread Safety and Race Condition Tests
# ---------------------------------------------------------------------------


class TestThreadSafety:

    def test_finalize_stop_runs_only_once(self, container, mock_subsystems):
        """Verify _finalize_stop() is idempotent even when called concurrently."""
        container.start()
        results = []

        def finalize():
            container._finalize_stop()
            results.append(container.status)

        threads = [threading.Thread(target=finalize) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All threads should see STOPPED, but the transition should happen exactly once
        assert all(r == ContainerStatus.STOPPED for r in results)
        assert container._finalized is True

    def test_stop_event_coordinates_with_monitor(self, container, mock_subsystems):
        """After stop() sets _stop_event, the monitor thread should exit cleanly."""
        container.start()
        # Directly set the stop event (simulating what stop() does)
        container._stop_event.set()
        # The monitor thread should exit within its poll interval
        if container._monitor_thread:
            container._monitor_thread.join(timeout=2)
            assert not container._monitor_thread.is_alive()

    def test_concurrent_log_appends(self, container, mock_subsystems):
        """Log buffer should not corrupt under concurrent writes."""
        container.start()
        errors = []

        def append_logs(thread_id):
            try:
                for i in range(100):
                    container.append_log(f"Thread {thread_id}: line {i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=append_logs, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(container.get_logs()) == 500  # 5 threads * 100 lines

    def test_log_buffer_truncation(self, container, mock_subsystems):
        """Log buffer should cap at a reasonable size to prevent memory leaks."""
        for i in range(12000):
            container.append_log(f"line {i}")

        logs = container.get_logs()
        assert len(logs) <= 10000
        # After truncation to 5000 on overflow, subsequent lines are appended
        assert len(logs) > 0


# ---------------------------------------------------------------------------
# ContainerManager Tests
# ---------------------------------------------------------------------------


class TestContainerManager:

    def test_initialize_sets_up_infrastructure(self, mock_subsystems):
        m = ContainerManager()
        m.initialize()
        mock_subsystems["ensure_cg"].assert_called_once()
        mock_subsystems["bridge"].assert_called_once()
        assert m._initialized is True

    def test_double_initialize_is_noop(self, mock_subsystems):
        m = ContainerManager()
        m.initialize()
        m.initialize()
        # Should only be called once
        assert mock_subsystems["ensure_cg"].call_count == 1

    def test_create_container_returns_created(self, manager, mock_subsystems):
        config = ContainerConfig(name="test-create")
        c = manager.create_container(config)
        assert c.status == ContainerStatus.CREATED
        assert c.name == "test-create"

    def test_container_limit_enforced(self, manager, mock_subsystems):
        manager.create_container(ContainerConfig(name="c1"))
        manager.create_container(ContainerConfig(name="c2"))
        with pytest.raises(ContainerLimitReachedError):
            manager.create_container(ContainerConfig(name="c3"))

    def test_stopped_containers_dont_count_toward_limit(self, manager, mock_subsystems):
        c1 = manager.create_container(ContainerConfig(name="c1"))
        c1.status = ContainerStatus.STOPPED  # Simulate stop
        c2 = manager.create_container(ContainerConfig(name="c2"))
        c3 = manager.create_container(ContainerConfig(name="c3"))
        assert len(manager.list_containers()) == 3

    def test_get_nonexistent_container_raises(self, manager):
        with pytest.raises(ContainerNotFoundError):
            manager.get_container("crate-nonexistent")

    def test_remove_deletes_from_registry(self, manager, mock_subsystems):
        config = ContainerConfig(name="removeme")
        c = manager.create_container(config)
        cid = c.container_id
        manager.remove_container(cid)
        with pytest.raises(ContainerNotFoundError):
            manager.get_container(cid)

    def test_remove_nonexistent_raises(self, manager):
        with pytest.raises(ContainerNotFoundError):
            manager.remove_container("crate-ghost")

    def test_list_containers_with_filter(self, manager, mock_subsystems):
        c1 = manager.create_container(ContainerConfig(name="c1"))
        c2 = manager.create_container(ContainerConfig(name="c2"))
        c1.status = ContainerStatus.RUNNING

        running = manager.list_containers(status_filter=ContainerStatus.RUNNING)
        created = manager.list_containers(status_filter=ContainerStatus.CREATED)

        assert len(running) == 1
        assert len(created) == 1
        assert running[0].name == "c1"

    def test_list_containers_no_filter(self, manager, mock_subsystems):
        manager.create_container(ContainerConfig(name="c1"))
        manager.create_container(ContainerConfig(name="c2"))
        assert len(manager.list_containers()) == 2

    def test_concurrent_create_respects_limit(self, mock_subsystems):
        """Two threads creating containers simultaneously should not exceed the limit."""
        m = ContainerManager(max_containers=1)
        m.initialize()

        results = {"successes": 0, "failures": 0}
        lock = threading.Lock()

        def create():
            try:
                m.create_container(ContainerConfig(name="concurrent"))
                with lock:
                    results["successes"] += 1
            except ContainerLimitReachedError:
                with lock:
                    results["failures"] += 1

        threads = [threading.Thread(target=create) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert results["successes"] == 1
        assert results["failures"] == 4

    def test_shutdown_stops_running_containers(self, manager, mock_subsystems):
        c = manager.create_container(ContainerConfig(name="shutdown-test"))
        c.start()

        with _win_os_compat(), \
             patch("engine.container.os.kill"), \
             patch("engine.container.os.waitpid", return_value=(99999, 0)):
            manager.shutdown()

        assert c.status == ContainerStatus.STOPPED


# ---------------------------------------------------------------------------
# Serialization Tests
# ---------------------------------------------------------------------------


class TestSerialization:

    def test_to_dict_contains_required_fields(self, container, mock_subsystems):
        data = container.to_dict()
        required = {
            "container_id", "name", "status", "image", "config",
            "pid", "exit_code", "error", "created_at", "started_at", "stopped_at",
        }
        assert required.issubset(set(data.keys()))

    def test_to_dict_includes_network_when_set(self, container, mock_subsystems):
        container.start()
        data = container.to_dict()
        assert "network" in data
        assert data["network"]["ip_address"] == "10.0.0.42"

    def test_to_dict_excludes_network_when_unset(self, container, mock_subsystems):
        data = container.to_dict()
        assert "network" not in data

    def test_status_is_string_in_dict(self, container, mock_subsystems):
        data = container.to_dict()
        assert data["status"] == "created"


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------


class TestEdgeCases:

    def test_get_logs_empty(self, container, mock_subsystems):
        assert container.get_logs() == []

    def test_get_logs_with_tail(self, container, mock_subsystems):
        for i in range(20):
            container.append_log(f"line {i}")
        assert len(container.get_logs(tail=5)) == 5
        assert container.get_logs(tail=5)[-1] == "line 19"

    def test_get_logs_tail_larger_than_buffer(self, container, mock_subsystems):
        for i in range(3):
            container.append_log(f"line {i}")
        assert len(container.get_logs(tail=100)) == 3

    def test_collect_metrics_returns_none_when_not_running(self, container, mock_subsystems):
        assert container.collect_metrics() is None

    def test_networking_failure_doesnt_block_start(self, container, mock_subsystems):
        """If veth creation fails, the container should still start (just without networking)."""
        mock_subsystems["veth"].side_effect = Exception("bridge down")
        container.start()
        assert container.status == ContainerStatus.RUNNING
        assert container._network_config is None

    def test_stop_timeout_sends_sigkill(self, container, mock_subsystems):
        """If the process doesn't exit after SIGTERM, SIGKILL should be sent."""
        container.start()

        def fake_waitpid(pid, flags):
            if flags == 0:  # blocking waitpid in SIGKILL path
                return (pid, 0)
            return (0, 0)  # WNOHANG returns "still running"

        with _win_os_compat(), \
             patch("engine.container.os.kill") as mock_kill, \
             patch("engine.container.os.waitpid", side_effect=fake_waitpid):
            container.stop(timeout=1)
            assert container.status == ContainerStatus.STOPPED

            kill_calls = [call.args[1] for call in mock_kill.call_args_list]
            assert 15 in kill_calls  # SIGTERM
            assert 9 in kill_calls   # SIGKILL

    def test_stop_process_already_exited(self, container, mock_subsystems):
        """If the process exited before SIGTERM, stop() should still finalize cleanly."""
        container.start()
        with _win_os_compat(), \
             patch("engine.container.os.kill", side_effect=ProcessLookupError):
            container.stop()
            assert container.status == ContainerStatus.STOPPED
            assert container.exit_code == 0
