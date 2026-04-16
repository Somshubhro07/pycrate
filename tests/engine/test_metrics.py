"""
Tests for MetricsSnapshot and MetricsCollector
"""

from unittest.mock import MagicMock

from engine.metrics import MetricsCollector, MetricsSnapshot, SystemMetrics


class TestMetricsSnapshot:

    def test_default_values(self):
        snap = MetricsSnapshot(container_id="test")
        assert snap.memory_usage_bytes == 0
        assert snap.cpu_usage_percent == 0.0
        assert snap.oom_killed is False

    def test_memory_usage_mb(self):
        snap = MetricsSnapshot(
            container_id="test",
            memory_usage_bytes=64 * 1024 * 1024,
        )
        assert snap.memory_usage_mb == 64.0

    def test_memory_usage_percent(self):
        snap = MetricsSnapshot(
            container_id="test",
            memory_usage_bytes=32 * 1024 * 1024,
            memory_limit_bytes=64 * 1024 * 1024,
        )
        assert snap.memory_usage_percent == 50.0

    def test_memory_usage_percent_zero_limit(self):
        snap = MetricsSnapshot(
            container_id="test",
            memory_usage_bytes=1000,
            memory_limit_bytes=0,
        )
        assert snap.memory_usage_percent == 0.0

    def test_to_dict_structure(self):
        snap = MetricsSnapshot(
            container_id="crate-abc",
            memory_usage_bytes=1024,
            memory_limit_bytes=2048,
            cpu_usage_percent=25.5,
        )
        data = snap.to_dict()
        assert data["container_id"] == "crate-abc"
        assert "memory" in data
        assert "cpu" in data
        assert data["cpu"]["usage_percent"] == 25.5
        assert data["memory"]["usage_bytes"] == 1024

    def test_to_dict_rounding(self):
        snap = MetricsSnapshot(
            container_id="test",
            cpu_usage_percent=33.33333333,
        )
        data = snap.to_dict()
        assert data["cpu"]["usage_percent"] == 33.33  # Rounded to 2 decimals


class TestMetricsCollector:

    def _make_cgroup(self, mem_current=0, mem_limit=64 * 1024 * 1024, cpu_usec=0, oom=False):
        cg = MagicMock()
        cg.read_memory_usage.return_value = mem_current
        cg.read_memory_limit.return_value = mem_limit
        cg.read_cpu_usage.return_value = {"usage_usec": cpu_usec, "nr_throttled": 0, "throttled_usec": 0}
        cg.check_oom.return_value = oom
        return cg

    def test_first_collection_shows_zero_cpu(self):
        cg = self._make_cgroup(cpu_usec=50000)
        collector = MetricsCollector("test", cg)
        snap = collector.collect()
        assert snap.cpu_usage_percent == 0.0  # No previous reading

    def test_second_collection_calculates_cpu(self):
        cg = self._make_cgroup(cpu_usec=0)
        collector = MetricsCollector("test", cg)

        # First reading (baseline)
        collector.collect()

        # Simulate CPU usage increase
        cg.read_cpu_usage.return_value = {"usage_usec": 100000, "nr_throttled": 0, "throttled_usec": 0}

        # Force a known time delta by setting _prev_timestamp
        import time
        collector._prev_timestamp = time.monotonic() - 1.0  # 1 second ago

        snap = collector.collect()
        # 100000 usec / 1000000 usec (1 second) * 100 = 10%
        assert 9.0 <= snap.cpu_usage_percent <= 11.0  # Allow slight timing variance

    def test_cpu_clamped_to_100(self):
        cg = self._make_cgroup()
        collector = MetricsCollector("test", cg)

        # First reading
        collector.collect()

        # Huge CPU jump in short time
        import time
        collector._prev_cpu_usec = 0
        collector._prev_timestamp = time.monotonic() - 0.001  # 1ms ago
        cg.read_cpu_usage.return_value = {"usage_usec": 1000000, "nr_throttled": 0, "throttled_usec": 0}

        snap = collector.collect()
        assert snap.cpu_usage_percent <= 100.0

    def test_memory_values_passed_through(self):
        cg = self._make_cgroup(mem_current=32 * 1024 * 1024, mem_limit=64 * 1024 * 1024)
        collector = MetricsCollector("test", cg)
        snap = collector.collect()
        assert snap.memory_usage_bytes == 32 * 1024 * 1024
        assert snap.memory_limit_bytes == 64 * 1024 * 1024

    def test_oom_detection(self):
        cg = self._make_cgroup(oom=True)
        collector = MetricsCollector("test", cg)
        snap = collector.collect()
        assert snap.oom_killed is True


class TestSystemMetrics:

    def test_to_dict_converts_to_mb(self):
        m = SystemMetrics(
            hostname="test-host",
            total_memory_bytes=1024 * 1024 * 1024,  # 1GB
            available_memory_bytes=512 * 1024 * 1024,  # 512MB
        )
        data = m.to_dict()
        assert data["total_memory_mb"] == 1024.0
        assert data["available_memory_mb"] == 512.0

    def test_default_values(self):
        m = SystemMetrics()
        assert m.hostname == ""
        assert m.cpu_count == 0
