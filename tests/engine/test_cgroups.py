"""
Tests for CgroupController and CgroupLimits
=============================================

These tests mock filesystem operations since cgroup files only exist on Linux.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

import pytest

from engine.cgroups import CgroupController, CgroupLimits, verify_cgroup_v2


class TestCgroupLimits:

    def test_dataclass_fields(self):
        limits = CgroupLimits(
            cpu_quota_us=50000,
            cpu_period_us=100000,
            memory_limit_bytes=64 * 1024 * 1024,
        )
        assert limits.cpu_quota_us == 50000
        assert limits.cpu_period_us == 100000
        assert limits.memory_limit_bytes == 64 * 1024 * 1024


class TestCgroupController:

    @pytest.fixture
    def controller(self):
        limits = CgroupLimits(
            cpu_quota_us=50000,
            cpu_period_us=100000,
            memory_limit_bytes=64 * 1024 * 1024,
        )
        return CgroupController("crate-test01", limits)

    def test_cgroup_path(self, controller):
        # Check path components, not string representation (Windows uses backslash)
        assert controller.cgroup_path.parts[-2:] == ("pycrate", "crate-test01")

    @patch("engine.cgroups.Path.write_text")
    @patch("engine.cgroups.Path.mkdir")
    @patch("engine.cgroups.CGROUP_BASE_PATH")
    def test_create_writes_cpu_and_memory(self, mock_base, mock_mkdir, mock_write, controller):
        mock_base.mkdir = MagicMock()
        controller.cgroup_path = MagicMock()
        controller.cgroup_path.mkdir = MagicMock()
        controller.cgroup_path.__truediv__ = lambda self, name: MagicMock()

        controller.create()

    @patch.object(Path, "write_text")
    def test_assign_writes_pid(self, mock_write, controller):
        controller.cgroup_path = MagicMock()
        mock_file = MagicMock()
        controller.cgroup_path.__truediv__ = lambda self, name: mock_file

        controller.assign(12345)

    @patch.object(Path, "read_text", return_value="usage_usec 50000\nuser_usec 30000\nsystem_usec 20000\nnr_periods 100\nnr_throttled 5\nthrottled_usec 1000\n")
    def test_read_cpu_usage(self, mock_read, controller):
        controller.cgroup_path = MagicMock()
        mock_file = MagicMock()
        mock_file.read_text = mock_read
        controller.cgroup_path.__truediv__ = lambda self, name: mock_file

        stats = controller.read_cpu_usage()
        assert stats["usage_usec"] == 50000
        assert stats["nr_throttled"] == 5
        assert stats["throttled_usec"] == 1000

    @patch.object(Path, "read_text", return_value="33554432\n")
    def test_read_memory_usage(self, mock_read, controller):
        controller.cgroup_path = MagicMock()
        mock_file = MagicMock()
        mock_file.read_text = mock_read
        controller.cgroup_path.__truediv__ = lambda self, name: mock_file

        usage = controller.read_memory_usage()
        assert usage == 33554432  # 32MB

    @patch.object(Path, "read_text", return_value="max\n")
    def test_read_memory_limit_max(self, mock_read, controller):
        controller.cgroup_path = MagicMock()
        mock_file = MagicMock()
        mock_file.read_text = mock_read
        controller.cgroup_path.__truediv__ = lambda self, name: mock_file

        limit = controller.read_memory_limit()
        assert limit == 0  # "max" = no limit

    @patch.object(Path, "read_text", return_value="low 0\nhigh 0\nmax 0\noom 0\noom_kill 1\noom_group_kill 0\n")
    def test_check_oom_detected(self, mock_read, controller):
        controller.cgroup_path = MagicMock()
        mock_file = MagicMock()
        mock_file.read_text = mock_read
        controller.cgroup_path.__truediv__ = lambda self, name: mock_file

        assert controller.check_oom() is True

    @patch.object(Path, "read_text", return_value="low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\noom_group_kill 0\n")
    def test_check_oom_not_detected(self, mock_read, controller):
        controller.cgroup_path = MagicMock()
        mock_file = MagicMock()
        mock_file.read_text = mock_read
        controller.cgroup_path.__truediv__ = lambda self, name: mock_file

        assert controller.check_oom() is False


class TestVerifyCgroupV2:

    @patch("builtins.open", mock_open(read_data="cgroup2 /sys/fs/cgroup cgroup2 rw,nosuid,nodev,noexec,relatime 0 0\n"))
    def test_cgroup_v2_present(self):
        assert verify_cgroup_v2() is True

    @patch("builtins.open", mock_open(read_data="tmpfs /sys/fs/cgroup tmpfs rw 0 0\n"))
    def test_cgroup_v1_only(self):
        assert verify_cgroup_v2() is False

    @patch("builtins.open", side_effect=OSError("no /proc"))
    def test_proc_not_available(self, mock_file):
        assert verify_cgroup_v2() is False
