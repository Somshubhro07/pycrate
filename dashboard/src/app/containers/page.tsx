"use client";

import { useCallback, useState } from "react";
import { motion } from "framer-motion";
import { ContainerCard } from "@/components/ContainerCard";
import { useMetrics, usePolling } from "@/hooks/useWebSocket";
import {
  listContainers,
  startContainer,
  stopContainer,
  removeContainer,
} from "@/lib/api";
import type { ContainerListResponse } from "@/lib/api";

export default function ContainersPage() {
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const fetchContainers = useCallback(() => listContainers(statusFilter), [statusFilter]);
  const { data, refetch } = usePolling<ContainerListResponse>(fetchContainers, 3000);
  const { metrics } = useMetrics();

  const containers = data?.containers ?? [];

  const handleStart = async (id: string) => {
    await startContainer(id);
    refetch();
  };

  const handleStop = async (id: string) => {
    await stopContainer(id);
    refetch();
  };

  const handleRemove = async (id: string) => {
    if (!confirm(`Remove container ${id}? This deletes all data.`)) return;
    await removeContainer(id);
    refetch();
  };

  const filters = [
    { label: "All", value: undefined },
    { label: "Running", value: "running" },
    { label: "Stopped", value: "stopped" },
    { label: "Created", value: "created" },
  ];

  return (
    <div className="max-w-7xl mx-auto">
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }}>
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-2xl font-bold" style={{ color: "var(--color-text-heading)" }}>
            Containers
          </h1>
          <span className="mono text-sm" style={{ color: "var(--color-text-muted)" }}>
            {containers.length} total
          </span>
        </div>

        {/* Filters */}
        <div className="flex gap-2 mb-6">
          {filters.map((f) => (
            <button
              key={f.label}
              onClick={() => setStatusFilter(f.value)}
              className="px-3 py-1.5 rounded-lg mono text-xs font-medium transition-colors"
              style={{
                background: statusFilter === f.value ? "var(--color-accent-glow)" : "var(--color-bg-card)",
                color: statusFilter === f.value ? "var(--color-accent-bright)" : "var(--color-text-secondary)",
                border: `1px solid ${statusFilter === f.value ? "var(--color-accent-dim)" : "var(--color-border)"}`,
              }}
            >
              {f.label}
            </button>
          ))}
        </div>
      </motion.div>

      {containers.length === 0 ? (
        <div className="card-static p-12 text-center">
          <p className="text-sm" style={{ color: "var(--color-text-muted)" }}>
            {statusFilter ? `No ${statusFilter} containers` : "No containers found"}
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {containers.map((container, i) => (
            <ContainerCard
              key={container.container_id}
              container={container}
              metrics={metrics.find((m) => m.container_id === container.container_id)}
              index={i}
              onStart={handleStart}
              onStop={handleStop}
            />
          ))}
        </div>
      )}
    </div>
  );
}
