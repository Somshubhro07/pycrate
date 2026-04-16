"use client";

import { useCallback } from "react";
import { motion } from "framer-motion";
import { ContainerCard } from "@/components/ContainerCard";
import { useMetrics } from "@/hooks/useWebSocket";
import { usePolling } from "@/hooks/useWebSocket";
import { listContainers, startContainer, stopContainer, getSystemInfo } from "@/lib/api";
import type { ContainerListResponse, SystemInfo } from "@/lib/api";

export default function DashboardPage() {
  const fetchContainers = useCallback(() => listContainers(), []);
  const fetchSystem = useCallback(() => getSystemInfo(), []);

  const { data: containerData, refetch } = usePolling<ContainerListResponse>(fetchContainers, 3000);
  const { data: systemData } = usePolling<SystemInfo>(fetchSystem, 10000);
  const { metrics, connected } = useMetrics();

  const containers = containerData?.containers ?? [];
  const running = containers.filter((c) => c.status === "running").length;
  const stopped = containers.filter((c) => c.status === "stopped").length;
  const total = containers.length;

  const handleStart = async (id: string) => {
    try {
      await startContainer(id);
      refetch();
    } catch (err) {
      console.error("Failed to start container:", err);
    }
  };

  const handleStop = async (id: string) => {
    try {
      await stopContainer(id);
      refetch();
    } catch (err) {
      console.error("Failed to stop container:", err);
    }
  };

  return (
    <div className="max-w-7xl mx-auto">
      {/* Page header */}
      <motion.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        className="mb-8"
      >
        <h1 className="text-2xl font-bold" style={{ color: "var(--color-text-heading)" }}>
          Dashboard
        </h1>
        <div className="flex items-center gap-3 mt-2">
          <span
            className={`inline-flex items-center gap-1.5 mono text-xs px-2 py-0.5 rounded-full ${
              connected ? "status-running" : "status-error"
            }`}
          >
            <span className={`w-1.5 h-1.5 rounded-full ${connected ? "bg-emerald-400" : "bg-red-400"}`} />
            {connected ? "Live" : "Disconnected"}
          </span>
          {systemData && (
            <span className="mono text-xs" style={{ color: "var(--color-text-muted)" }}>
              {systemData.hostname} / {systemData.kernel_version}
            </span>
          )}
        </div>
      </motion.div>

      {/* Stats row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
        {[
          { label: "Total", value: total, color: "var(--color-accent)" },
          { label: "Running", value: running, color: "var(--color-success)" },
          { label: "Stopped", value: stopped, color: "var(--color-warning)" },
          {
            label: "Capacity",
            value: `${total}/${systemData?.max_containers ?? 4}`,
            color: "var(--color-text-secondary)",
          },
        ].map((stat, i) => (
          <motion.div
            key={stat.label}
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * 0.05 }}
            className="card-static p-4"
          >
            <p className="text-xs font-medium mb-1" style={{ color: "var(--color-text-muted)" }}>
              {stat.label}
            </p>
            <p className="mono text-2xl font-bold" style={{ color: stat.color }}>
              {stat.value}
            </p>
          </motion.div>
        ))}
      </div>

      {/* System resources */}
      {systemData && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
          <div className="card-static p-4">
            <p className="text-xs font-medium mb-1" style={{ color: "var(--color-text-muted)" }}>
              Host Memory
            </p>
            <div className="flex items-baseline gap-2">
              <span className="mono text-lg font-bold" style={{ color: "var(--color-text-heading)" }}>
                {(systemData.total_memory_mb - systemData.available_memory_mb).toFixed(0)}
              </span>
              <span className="mono text-xs" style={{ color: "var(--color-text-muted)" }}>
                / {systemData.total_memory_mb.toFixed(0)} MB used
              </span>
            </div>
          </div>
          <div className="card-static p-4">
            <p className="text-xs font-medium mb-1" style={{ color: "var(--color-text-muted)" }}>
              CPU Cores
            </p>
            <span className="mono text-lg font-bold" style={{ color: "var(--color-text-heading)" }}>
              {systemData.cpu_count}
            </span>
          </div>
          <div className="card-static p-4">
            <p className="text-xs font-medium mb-1" style={{ color: "var(--color-text-muted)" }}>
              Engine
            </p>
            <span className="mono text-lg font-bold" style={{ color: "var(--color-accent-bright)" }}>
              v{systemData.engine_version}
            </span>
          </div>
        </div>
      )}

      {/* Container grid */}
      {containers.length === 0 ? (
        <div
          className="card-static p-12 text-center"
        >
          <p className="text-lg mb-2" style={{ color: "var(--color-text-secondary)" }}>
            No containers
          </p>
          <p className="text-sm" style={{ color: "var(--color-text-muted)" }}>
            Create your first container to get started.
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
