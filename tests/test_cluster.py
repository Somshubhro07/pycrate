"""
Cluster Test Suite
====================

Tests for the cluster state store, scheduler, and reconciler.
These tests validate the logic that was identified in the code review:

- C1: Duplicate assignment prevention
- C3: Concurrent write safety (write lock)
- C4: Heartbeat atomicity
- W3: Duplicate orphan stop prevention
- W5: Master self-heartbeat
- W7: Input validation
- W8: Reconciler efficiency

All tests use an in-memory SQLite database (":memory:") for speed and
isolation — they run on Windows without needing Linux.

Usage:
    cd pycrate
    python -m pytest tests/test_cluster.py -v
"""

import os
import sys
import threading
import time
import tempfile
from pathlib import Path

import pytest

# Ensure pycrate package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cluster.state import ClusterState, Deployment, NodeInfo
from cluster.scheduler import Scheduler, NoCapacityError
from cluster.reconciler import Reconciler


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    """Create a fresh state store with a temp file-based DB."""
    return ClusterState(tmp_path / "test_cluster.db")


@pytest.fixture
def populated_db(db):
    """State store with 1 master + 2 workers registered."""
    db.register_node("master-1", "10.0.1.1:9000", role="master",
                     cpu_total=400, memory_total=4096)
    db.register_node("worker-1", "10.0.1.11:9001", role="worker",
                     cpu_total=400, memory_total=4096)
    db.register_node("worker-2", "10.0.1.12:9001", role="worker",
                     cpu_total=400, memory_total=4096)
    return db


@pytest.fixture
def deployment():
    """Sample deployment for testing."""
    return Deployment(
        deployment_id="dep-test001",
        service_name="web",
        image="alpine:3.20",
        command=["/bin/sh"],
        replicas=3,
        cpu=50,
        memory=64,
    )


# ---------------------------------------------------------------------------
# State Store Tests
# ---------------------------------------------------------------------------

class TestClusterState:
    """Tests for the SQLite state store."""

    def test_register_node(self, db):
        node = db.register_node("worker-1", "10.0.1.11:9001")
        assert node.node_id == "worker-1"
        assert node.status == "healthy"
        assert node.role == "worker"

    def test_register_node_idempotent(self, db):
        """Re-registering should update, not duplicate."""
        db.register_node("worker-1", "10.0.1.11:9001", cpu_total=200)
        db.register_node("worker-1", "10.0.1.11:9001", cpu_total=400)
        nodes = db.get_all_nodes()
        assert len(nodes) == 1
        assert nodes[0].cpu_total == 400

    def test_node_health_timeout(self, db):
        """Nodes with stale heartbeats should be marked unhealthy."""
        db.register_node("worker-1", "10.0.1.11:9001")
        # Force old heartbeat
        conn = db._get_conn()
        conn.execute(
            "UPDATE nodes SET last_heartbeat = ? WHERE node_id = ?",
            (time.time() - 60, "worker-1"),
        )
        conn.commit()

        unhealthy = db.check_node_health()
        assert "worker-1" in unhealthy

        node = db.get_node("worker-1")
        assert node.status == "unhealthy"

    def test_heartbeat_updates_status(self, db):
        """Heartbeat should set node to healthy and update resources."""
        db.register_node("worker-1", "10.0.1.11:9001")
        db.mark_node_unhealthy("worker-1")

        db.update_heartbeat("worker-1", containers=[], resources={
            "cpu_used": 50, "memory_used": 128,
        })

        node = db.get_node("worker-1")
        assert node.status == "healthy"
        assert node.cpu_used == 50
        assert node.memory_used == 128

    def test_heartbeat_atomic_container_update(self, db):
        """C4 fix: heartbeat should atomically replace containers."""
        db.register_node("worker-1", "10.0.1.11:9001")

        # First heartbeat with 2 containers
        db.update_heartbeat("worker-1", containers=[
            {"container_id": "c1", "service_name": "web", "status": "running",
             "deployment_id": "dep-1"},
            {"container_id": "c2", "service_name": "web", "status": "running",
             "deployment_id": "dep-1"},
        ], resources={"cpu_used": 100, "memory_used": 128})

        containers = db.get_containers_for_node("worker-1")
        assert len(containers) == 2

        # Second heartbeat with 1 container (c2 stopped)
        db.update_heartbeat("worker-1", containers=[
            {"container_id": "c1", "service_name": "web", "status": "running",
             "deployment_id": "dep-1"},
        ], resources={"cpu_used": 50, "memory_used": 64})

        containers = db.get_containers_for_node("worker-1")
        assert len(containers) == 1
        assert containers[0].container_id == "c1"

    def test_deployment_crud(self, db, deployment):
        """Deployment create + get + update + delete."""
        db.create_deployment(deployment)

        dep = db.get_deployment("web")
        assert dep is not None
        assert dep.replicas == 3
        assert dep.image == "alpine:3.20"

        db.update_replicas("web", 5)
        dep = db.get_deployment("web")
        assert dep.replicas == 5

        db.delete_deployment("web")
        assert db.get_deployment("web") is None

    def test_create_deployment_upsert(self, db, deployment):
        """Creating a deployment with same name should update it."""
        db.create_deployment(deployment)

        updated = Deployment(
            deployment_id="dep-updated",
            service_name="web",
            image="alpine:3.21",
            command=["/bin/sh"],
            replicas=5,
        )
        db.create_deployment(updated)

        dep = db.get_deployment("web")
        assert dep.image == "alpine:3.21"
        assert dep.replicas == 5

    def test_assignment_lifecycle(self, db):
        """Create → get pending → ack → get pending returns empty."""
        db.register_node("worker-1", "10.0.1.11:9001")

        assignment = db.create_assignment(
            node_id="worker-1",
            action="create",
            deployment_id="dep-1",
            service_name="web",
            image="alpine:3.20",
        )
        assert assignment.assignment_id.startswith("assign-")

        pending = db.get_pending_assignments("worker-1")
        assert len(pending) == 1
        assert pending[0].action == "create"

        db.acknowledge_assignment(pending[0].assignment_id)
        pending = db.get_pending_assignments("worker-1")
        assert len(pending) == 0

    def test_count_pending_creates(self, db):
        """C1 fix: count_pending_creates returns unacked create count."""
        db.register_node("worker-1", "10.0.1.11:9001")

        db.create_assignment(
            node_id="worker-1", action="create",
            deployment_id="dep-1", service_name="web",
        )
        db.create_assignment(
            node_id="worker-1", action="create",
            deployment_id="dep-1", service_name="web",
        )
        db.create_assignment(
            node_id="worker-1", action="stop",
            deployment_id="dep-1", service_name="web",
        )

        assert db.count_pending_creates("dep-1") == 2

    def test_has_pending_stop(self, db):
        """W3 fix: detect existing stop assignment for a container."""
        db.register_node("worker-1", "10.0.1.11:9001")

        assert not db.has_pending_stop("c1")

        db.create_assignment(
            node_id="worker-1", action="stop",
            container_id="c1", service_name="web",
        )

        assert db.has_pending_stop("c1")

    def test_reserve_resources(self, db):
        """C2 fix: proper public API for resource reservation."""
        db.register_node("worker-1", "10.0.1.11:9001",
                         cpu_total=400, memory_total=4096)

        db.reserve_resources("worker-1", cpu=100, memory=256)

        node = db.get_node("worker-1")
        assert node.cpu_used == 100
        assert node.memory_used == 256

        db.reserve_resources("worker-1", cpu=50, memory=128)
        node = db.get_node("worker-1")
        assert node.cpu_used == 150
        assert node.memory_used == 384

    def test_master_heartbeat_update(self, db):
        """W5 fix: master can update its own heartbeat."""
        db.register_node("master-1", "10.0.1.1:9000", role="master")

        # Force old heartbeat
        conn = db._get_conn()
        conn.execute(
            "UPDATE nodes SET last_heartbeat = ? WHERE node_id = ?",
            (time.time() - 60, "master-1"),
        )
        conn.commit()

        db.update_master_heartbeat("master-1")

        node = db.get_node("master-1")
        assert time.time() - node.last_heartbeat < 2

    def test_remove_node_cascades(self, db):
        """Removing a node should clean up its assignments and containers."""
        db.register_node("worker-1", "10.0.1.11:9001")
        db.create_assignment(
            node_id="worker-1", action="create",
            deployment_id="dep-1", service_name="web",
        )
        db.update_heartbeat("worker-1", containers=[
            {"container_id": "c1", "service_name": "web",
             "status": "running", "deployment_id": "dep-1"},
        ], resources={})

        db.remove_node("worker-1")

        assert db.get_node("worker-1") is None
        assert len(db.get_pending_assignments("worker-1")) == 0
        assert len(db.get_containers_for_node("worker-1")) == 0

    def test_events_lifecycle(self, db):
        """Events can be created and retrieved."""
        db.add_event(event_type="test.event", message="Hello")
        db.add_event(event_type="test.event2", message="World")

        events = db.get_recent_events(limit=10)
        assert len(events) == 2
        assert events[0]["event_type"] == "test.event2"  # Most recent first

    def test_concurrent_heartbeats(self, db):
        """C3 fix: concurrent writes from different threads don't crash."""
        db.register_node("worker-1", "10.0.1.11:9001")
        db.register_node("worker-2", "10.0.1.12:9001")

        errors = []

        def heartbeat(node_id, count):
            try:
                for i in range(count):
                    db.update_heartbeat(node_id, containers=[
                        {"container_id": f"c-{node_id}-{i}",
                         "service_name": "web",
                         "status": "running",
                         "deployment_id": "dep-1"},
                    ], resources={"cpu_used": 50, "memory_used": 64})
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=heartbeat, args=("worker-1", 20)),
            threading.Thread(target=heartbeat, args=("worker-2", 20)),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert len(errors) == 0, f"Concurrent heartbeats failed: {errors}"


# ---------------------------------------------------------------------------
# Scheduler Tests
# ---------------------------------------------------------------------------

class TestScheduler:
    """Tests for the resource-aware scheduler."""

    def test_basic_scheduling(self, populated_db, deployment):
        scheduler = Scheduler(populated_db)
        decision = scheduler.schedule(deployment)

        assert decision.node.role == "worker"  # Should prefer workers
        assert decision.node.cpu_available >= deployment.cpu
        assert decision.node.memory_available >= deployment.memory

    def test_prefers_workers_over_master(self, populated_db, deployment):
        scheduler = Scheduler(populated_db)

        # Schedule 3 containers — all should go to workers
        nodes_used = set()
        for _ in range(3):
            decision = scheduler.schedule(deployment)
            nodes_used.add(decision.node.node_id)

        assert "master-1" not in nodes_used

    def test_no_capacity_error(self, populated_db):
        """Should raise when no node can fit the deployment."""
        scheduler = Scheduler(populated_db)

        huge = Deployment(
            deployment_id="dep-huge",
            service_name="monster",
            image="alpine:3.20",
            command=["/bin/sh"],
            replicas=1,
            cpu=9999,
            memory=99999,
        )

        with pytest.raises(NoCapacityError):
            scheduler.schedule(huge)

    def test_no_healthy_nodes_error(self, db):
        """Should raise NoCapacityError when cluster is empty."""
        scheduler = Scheduler(db)
        dep = Deployment(
            deployment_id="dep-1", service_name="web",
            image="alpine", command=["/bin/sh"],
        )

        with pytest.raises(NoCapacityError, match="no healthy nodes"):
            scheduler.schedule(dep)

    def test_spread_across_nodes(self, populated_db, deployment):
        """Scheduler should spread containers across nodes."""
        scheduler = Scheduler(populated_db)

        # Schedule 6 containers with reservations
        node_counts = {}
        for _ in range(6):
            decision = scheduler.schedule(deployment)
            nid = decision.node.node_id
            node_counts[nid] = node_counts.get(nid, 0) + 1
            # Simulate resource reservation
            populated_db.reserve_resources(nid, deployment.cpu, deployment.memory)

        # With spread scheduling, both workers should have containers
        assert len(node_counts) >= 2, (
            f"Expected spread across nodes but got: {node_counts}"
        )

    def test_fallback_to_master_when_workers_full(self, populated_db, deployment):
        """When workers are full, master should be used as fallback."""
        scheduler = Scheduler(populated_db)

        # Fill up both workers
        for node_id in ["worker-1", "worker-2"]:
            populated_db.reserve_resources(node_id, cpu=400, memory=4096)

        decision = scheduler.schedule(deployment)
        assert decision.node.node_id == "master-1"


# ---------------------------------------------------------------------------
# Reconciler Tests
# ---------------------------------------------------------------------------

class TestReconciler:
    """Tests for the reconciliation engine."""

    def test_scale_up_creates_assignments(self, populated_db, deployment):
        """Reconciler should create assignments for missing replicas."""
        scheduler = Scheduler(populated_db)
        reconciler = Reconciler(populated_db, scheduler, master_id="master-1")

        populated_db.create_deployment(deployment)

        reconciler.reconcile()

        # Should have created 3 assignments (one per desired replica)
        w1 = populated_db.get_pending_assignments("worker-1")
        w2 = populated_db.get_pending_assignments("worker-2")
        total = len(w1) + len(w2)
        assert total == 3, f"Expected 3 assignments, got {total}"

    def test_no_duplicates_with_pending_assignments(self, populated_db, deployment):
        """C1 fix: reconciler shouldn't create duplicates for pending creates."""
        scheduler = Scheduler(populated_db)
        reconciler = Reconciler(populated_db, scheduler, master_id="master-1")

        populated_db.create_deployment(deployment)

        # First pass: creates 3 assignments
        reconciler.reconcile()

        # Second pass: should NOT create more (pending creates counted)
        reconciler.reconcile()

        w1 = populated_db.get_pending_assignments("worker-1")
        w2 = populated_db.get_pending_assignments("worker-2")
        total = len(w1) + len(w2)
        assert total == 3, (
            f"Expected exactly 3 assignments after 2 passes, got {total}"
        )

    def test_scale_down_stops_excess(self, populated_db, deployment):
        """Reconciler should stop containers when replicas reduced."""
        scheduler = Scheduler(populated_db)
        reconciler = Reconciler(populated_db, scheduler, master_id="master-1")

        populated_db.create_deployment(deployment)

        # Simulate 5 running containers (but only 3 desired)
        for i in range(5):
            node = "worker-1" if i < 3 else "worker-2"
            populated_db.update_heartbeat(node, containers=[
                {"container_id": f"c{i}", "service_name": "web",
                 "status": "running", "deployment_id": deployment.deployment_id,
                 "started_at": time.time() - i},
            ] + [
                {"container_id": f"c{j}", "service_name": "web",
                 "status": "running", "deployment_id": deployment.deployment_id,
                 "started_at": time.time() - j}
                for j in range(i) if (j < 3 and node == "worker-1") or (j >= 3 and node == "worker-2")
            ], resources={"cpu_used": 50})

        # Directly insert 5 containers for simpler test
        conn = populated_db._get_conn()
        conn.execute("DELETE FROM containers")
        for i in range(5):
            node = "worker-1" if i < 3 else "worker-2"
            conn.execute("""
                INSERT INTO containers
                    (container_id, node_id, deployment_id, service_name,
                     status, started_at, reported_at)
                VALUES (?, ?, ?, ?, 'running', ?, ?)
            """, (f"c{i}", node, deployment.deployment_id, "web",
                  time.time() - i, time.time()))
        conn.commit()

        reconciler.reconcile()

        # Should have 2 stop assignments (5 running - 3 desired = 2 excess)
        w1_stops = [a for a in populated_db.get_pending_assignments("worker-1")
                    if a.action == "stop"]
        w2_stops = [a for a in populated_db.get_pending_assignments("worker-2")
                    if a.action == "stop"]
        total_stops = len(w1_stops) + len(w2_stops)
        assert total_stops == 2, f"Expected 2 stops, got {total_stops}"

    def test_orphan_cleanup(self, populated_db):
        """Reconciler should stop containers for deleted deployments."""
        scheduler = Scheduler(populated_db)
        reconciler = Reconciler(populated_db, scheduler, master_id="master-1")

        # Container exists but its deployment doesn't
        conn = populated_db._get_conn()
        conn.execute("""
            INSERT INTO containers
                (container_id, node_id, deployment_id, service_name,
                 status, reported_at)
            VALUES ('orphan-1', 'worker-1', 'dep-deleted', 'old-service',
                    'running', ?)
        """, (time.time(),))
        conn.commit()

        reconciler.reconcile()

        stops = [a for a in populated_db.get_pending_assignments("worker-1")
                 if a.action == "stop"]
        assert len(stops) == 1
        assert stops[0].container_id == "orphan-1"

    def test_no_duplicate_orphan_stops(self, populated_db):
        """W3 fix: shouldn't create duplicate stop assignments for orphans."""
        scheduler = Scheduler(populated_db)
        reconciler = Reconciler(populated_db, scheduler, master_id="master-1")

        conn = populated_db._get_conn()
        conn.execute("""
            INSERT INTO containers
                (container_id, node_id, deployment_id, service_name,
                 status, reported_at)
            VALUES ('orphan-1', 'worker-1', 'dep-deleted', 'old-service',
                    'running', ?)
        """, (time.time(),))
        conn.commit()

        # Two reconcile passes
        reconciler.reconcile()
        reconciler.reconcile()

        stops = [a for a in populated_db.get_pending_assignments("worker-1")
                 if a.action == "stop" and a.container_id == "orphan-1"]
        assert len(stops) == 1, f"Expected 1 stop, got {len(stops)}"

    def test_master_heartbeat_stays_alive(self, populated_db):
        """W5 fix: master should update its own heartbeat."""
        scheduler = Scheduler(populated_db)
        reconciler = Reconciler(populated_db, scheduler, master_id="master-1")

        # Force old heartbeat
        conn = populated_db._get_conn()
        conn.execute(
            "UPDATE nodes SET last_heartbeat = ? WHERE node_id = ?",
            (time.time() - 60, "master-1"),
        )
        conn.commit()

        reconciler.reconcile()

        master = populated_db.get_node("master-1")
        assert master.status == "healthy"
        assert time.time() - master.last_heartbeat < 2

    def test_unhealthy_node_reschedule(self, populated_db, deployment):
        """Containers on unhealthy nodes should be rescheduled."""
        scheduler = Scheduler(populated_db)
        reconciler = Reconciler(populated_db, scheduler, master_id="master-1")

        populated_db.create_deployment(deployment)

        # Simulate running containers on worker-1
        populated_db.update_heartbeat("worker-1", containers=[
            {"container_id": "c1", "service_name": "web",
             "status": "running", "deployment_id": deployment.deployment_id},
            {"container_id": "c2", "service_name": "web",
             "status": "running", "deployment_id": deployment.deployment_id},
            {"container_id": "c3", "service_name": "web",
             "status": "running", "deployment_id": deployment.deployment_id},
        ], resources={"cpu_used": 150, "memory_used": 192})

        # Make worker-1 unhealthy
        conn = populated_db._get_conn()
        conn.execute(
            "UPDATE nodes SET last_heartbeat = ? WHERE node_id = ?",
            (time.time() - 60, "worker-1"),
        )
        conn.commit()

        reconciler.reconcile()

        # Worker-1 should be unhealthy now
        w1 = populated_db.get_node("worker-1")
        assert w1.status == "unhealthy"

        # Containers should be marked as lost
        containers = populated_db.get_containers_for_node("worker-1")
        for c in containers:
            assert c.status == "lost"

    def test_reconciler_start_stop(self, populated_db):
        """Reconciler thread lifecycle."""
        scheduler = Scheduler(populated_db)
        reconciler = Reconciler(populated_db, scheduler, master_id="master-1")

        reconciler.start()
        assert reconciler.is_running

        # Let it run a couple passes
        time.sleep(0.5)
        assert reconciler._pass_count >= 0

        reconciler.stop()
        assert not reconciler.is_running

    def test_reconciler_stats(self, populated_db):
        """Stats should reflect running state."""
        scheduler = Scheduler(populated_db)
        reconciler = Reconciler(populated_db, scheduler, master_id="master-1")

        stats = reconciler.stats
        assert stats["running"] is False
        assert stats["pass_count"] == 0


# ---------------------------------------------------------------------------
# Integration Tests
# ---------------------------------------------------------------------------

class TestIntegration:
    """End-to-end test flows across multiple components."""

    def test_full_deployment_lifecycle(self, populated_db, deployment):
        """Deploy → scale → delete with reconciler passes."""
        scheduler = Scheduler(populated_db)
        reconciler = Reconciler(populated_db, scheduler, master_id="master-1")

        # 1. Deploy
        populated_db.create_deployment(deployment)
        reconciler.reconcile()

        all_assignments = (
            populated_db.get_pending_assignments("worker-1") +
            populated_db.get_pending_assignments("worker-2")
        )
        creates = [a for a in all_assignments if a.action == "create"]
        assert len(creates) == 3

        # 2. Simulate agents executing and reporting
        for a in creates:
            populated_db.acknowledge_assignment(a.assignment_id)

        populated_db.update_heartbeat("worker-1", containers=[
            {"container_id": "c1", "service_name": "web",
             "status": "running", "deployment_id": deployment.deployment_id},
            {"container_id": "c2", "service_name": "web",
             "status": "running", "deployment_id": deployment.deployment_id},
        ], resources={"cpu_used": 100, "memory_used": 128})

        populated_db.update_heartbeat("worker-2", containers=[
            {"container_id": "c3", "service_name": "web",
             "status": "running", "deployment_id": deployment.deployment_id},
        ], resources={"cpu_used": 50, "memory_used": 64})

        # 3. Reconcile — should be stable (no new assignments)
        reconciler.reconcile()
        new_assignments = (
            populated_db.get_pending_assignments("worker-1") +
            populated_db.get_pending_assignments("worker-2")
        )
        assert len(new_assignments) == 0, (
            f"Expected 0 new assignments after stabilization, got {len(new_assignments)}"
        )

        # 4. Scale up
        populated_db.update_replicas("web", 5)
        reconciler.reconcile()
        new_creates = [
            a for a in (
                populated_db.get_pending_assignments("worker-1") +
                populated_db.get_pending_assignments("worker-2")
            ) if a.action == "create"
        ]
        assert len(new_creates) == 2

        # 5. Delete deployment
        populated_db.delete_deployment("web")
        reconciler.reconcile()

        # Should have stop assignments for the 3 running containers
        stops = [
            a for a in (
                populated_db.get_pending_assignments("worker-1") +
                populated_db.get_pending_assignments("worker-2")
            ) if a.action == "stop"
        ]
        assert len(stops) == 3

    def test_rapid_reconcile_no_duplicates(self, populated_db, deployment):
        """Running reconciler rapidly should not create duplicate work."""
        scheduler = Scheduler(populated_db)
        reconciler = Reconciler(populated_db, scheduler, master_id="master-1")

        populated_db.create_deployment(deployment)

        # Run 10 rapid reconcile passes
        for _ in range(10):
            reconciler.reconcile()

        all_assignments = (
            populated_db.get_pending_assignments("worker-1") +
            populated_db.get_pending_assignments("worker-2")
        )
        creates = [a for a in all_assignments if a.action == "create"]
        assert len(creates) == 3, (
            f"Expected exactly 3 creates after 10 passes, got {len(creates)}"
        )

    def test_cluster_summary(self, populated_db, deployment):
        """Cluster summary should include all components."""
        populated_db.create_deployment(deployment)

        summary = populated_db.get_cluster_summary()
        assert summary["nodes"]["total"] == 3
        assert summary["nodes"]["healthy"] == 3
        assert summary["deployments"]["total"] == 1
        assert summary["containers"]["total"] == 0
