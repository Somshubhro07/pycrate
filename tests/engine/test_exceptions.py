"""
Tests for the exception hierarchy.
"""

from engine.exceptions import (
    CgroupError,
    ContainerAlreadyRunningError,
    ContainerAlreadyStoppedError,
    ContainerError,
    ContainerLimitReachedError,
    ContainerNotFoundError,
    ImageNotFoundError,
    NamespaceError,
    NetworkError,
    OOMKilledError,
    PyCrateError,
    RootfsError,
)


class TestExceptionHierarchy:
    """All engine exceptions must inherit from PyCrateError."""

    def test_base_exception(self):
        err = PyCrateError("test error")
        assert str(err) == "test error"
        assert err.message == "test error"
        assert err.code == "ENGINE_ERROR"

    def test_container_not_found(self):
        err = ContainerNotFoundError("crate-abc123")
        assert "crate-abc123" in str(err)
        assert err.container_id == "crate-abc123"
        assert err.code == "CONTAINER_NOT_FOUND"
        assert isinstance(err, ContainerError)
        assert isinstance(err, PyCrateError)

    def test_container_already_running(self):
        err = ContainerAlreadyRunningError("crate-abc123")
        assert err.code == "CONTAINER_ALREADY_RUNNING"
        assert isinstance(err, ContainerError)

    def test_container_already_stopped(self):
        err = ContainerAlreadyStoppedError("crate-abc123")
        assert err.code == "CONTAINER_ALREADY_STOPPED"

    def test_container_limit_reached(self):
        err = ContainerLimitReachedError(4)
        assert "4" in str(err)
        assert err.code == "CONTAINER_LIMIT_REACHED"

    def test_namespace_error_with_details(self):
        err = NamespaceError("permission denied", syscall="clone", errno=1)
        assert "clone" in str(err)
        assert "errno=1" in str(err)
        assert err.syscall == "clone"
        assert err.errno == 1

    def test_cgroup_error(self):
        err = CgroupError("write failed", cgroup_path="/sys/fs/cgroup/test")
        assert "/sys/fs/cgroup/test" in str(err)
        assert err.cgroup_path == "/sys/fs/cgroup/test"

    def test_oom_killed(self):
        err = OOMKilledError("crate-abc123", memory_limit_bytes=67108864)
        assert "64MB" in str(err)
        assert err.code == "OOM_KILLED"
        assert isinstance(err, CgroupError)

    def test_rootfs_error(self):
        err = RootfsError("extraction failed")
        assert err.code == "ROOTFS_ERROR"
        assert isinstance(err, PyCrateError)

    def test_image_not_found(self):
        err = ImageNotFoundError("debian")
        assert "debian" in str(err)
        assert err.code == "IMAGE_NOT_FOUND"
        assert isinstance(err, RootfsError)

    def test_network_error(self):
        err = NetworkError("bridge creation failed")
        assert err.code == "NETWORK_ERROR"

    def test_all_catchable_by_base(self):
        """Every exception should be catchable by except PyCrateError."""
        exceptions = [
            ContainerError("test"),
            ContainerNotFoundError("id"),
            ContainerAlreadyRunningError("id"),
            ContainerAlreadyStoppedError("id"),
            ContainerLimitReachedError(4),
            NamespaceError("test"),
            CgroupError("test"),
            OOMKilledError("id", 1024),
            RootfsError("test"),
            ImageNotFoundError("alpine"),
            NetworkError("test"),
        ]
        for exc in exceptions:
            assert isinstance(exc, PyCrateError), f"{type(exc).__name__} is not a PyCrateError"
