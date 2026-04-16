"use client";

import { useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import { motion } from "framer-motion";
import { StatusBadge } from "@/components/StatusBadge";
import { CircularGauge } from "@/components/ResourceGauge";
import { MetricsChart } from "@/components/MetricsChart";
import { LogViewer } from "@/components/LogViewer";
import { useMetrics, useLogs, usePolling } from "@/hooks/useWebSocket";
import {
  getContainer,
  startContainer,
  stopContainer,
  removeContainer,
} from "@/lib/api";
import type { Container } from "@/lib/api";

export default function ContainerDetailPage() {
  const params = useParams();
  const router = useRouter();
  const containerId = params.id as string;

  const fetchContainer = useCallback(() => getContainer(containerId), [containerId]);
  const { data: container, refetch } = usePolling<Container>(fetchContainer, 3000);
  const { metrics, connected: metricsConnected } = useMetrics();
  const { logs, connected: logsConnected } = useLogs(
    container?.status === "running" ? containerId : null
  );

  const containerMetrics = metrics.find((m) => m.container_id === containerId);

  if (!container) {
    return (
      <div className="flex items-center justify-center h-64">
        <p className="mono text-sm" style={{ color: "var(--color-text-muted)" }}>Loading...</p>
      </div>
    );
  }

  const handleStart = async () => {
    await startContainer(containerId);
    refetch();
  };

  const handleStop = async () => {
    await stopContainer(containerId);
    refetch();
  };

  const handleRemove = async () => {
    if (!confirm("Remove this container? All data will be deleted.")) return;
    await removeContainer(containerId);
    router.push("/containers");
  };

  return (
    <div className="max-w-6xl mx-auto">
      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        className="mb-8"
      >
        <div className="flex items-start justify-between">
          <div>
            <div className="flex items-center gap-3 mb-1">
              <h1 className="text-2xl font-bold" style={{ color: "var(--color-text-heading)" }}>
                {container.name}
              </h1>
              <StatusBadge status={container.status} />
            </div>
            <p className="mono text-sm" style={{ color: "var(--color-text-muted)" }}>
              {container.container_id}
            </p>
          </div>

          {/* Actions */}
          <div className="flex gap-2">
            {(container.status === "created" || container.status === "stopped") && (
              <button
                onClick={handleStart}
                className="px-4 py-2 rounded-lg text-sm font-medium transition-colors"
                style={{
                  background: "var(--color-success-dim)",
                  color: "var(--color-success)",
                  border: "1px solid rgba(16, 185, 129, 0.3)",
                }}
              >
                Start
              </button>
            )}
            {container.status === "running" && (
              <button
                onClick={handleStop}
                className="px-4 py-2 rounded-lg text-sm font-medium transition-colors"
                style={{
                  background: "var(--color-warning-dim)",
                  color: "var(--color-warning)",
                  border: "1px solid rgba(245, 158, 11, 0.3)",
                }}
              >
                Stop
              </button>
            )}
            <button
              onClick={handleRemove}
              className="px-4 py-2 rounded-lg text-sm font-medium transition-colors"
              style={{
                background: "var(--color-danger-dim)",
                color: "var(--color-danger)",
                border: "1px solid rgba(239, 68, 68, 0.3)",
              }}
            >
              Remove
            </button>
          </div>
        </div>
      </motion.div>

      {/* Info cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        {[
          { label: "Image", value: container.image },
          { label: "PID", value: container.pid ?? "--" },
          { label: "IP", value: container.network?.ip_address ?? "--" },
          { label: "Command", value: container.config.command.join(" ") },
        ].map((item) => (
          <div key={item.label} className="card-static p-3">
            <p className="text-[10px] font-medium mb-0.5" style={{ color: "var(--color-text-muted)" }}>
              {item.label}
            </p>
            <p className="mono text-sm truncate" style={{ color: "var(--color-text-heading)" }}>
              {item.value}
            </p>
          </div>
        ))}
      </div>

      {/* Gauges + Charts (only when running) */}
      {container.status === "running" && (
        <>
          {/* Circular gauges */}
          <div className="flex justify-center gap-12 mb-6">
            <motion.div initial={{ scale: 0.8, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={{ delay: 0.1 }}>
              <CircularGauge
                label="CPU"
                value={containerMetrics?.cpu.usage_percent ?? 0}
                max={100}
                unit="%"
              />
            </motion.div>
            <motion.div initial={{ scale: 0.8, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={{ delay: 0.2 }}>
              <CircularGauge
                label="Memory"
                value={containerMetrics?.memory.usage_mb ?? 0}
                max={container.config.memory_limit_mb}
                unit="MB"
              />
            </motion.div>
          </div>

          {/* Time-series charts */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
            <MetricsChart metrics={metrics} containerId={containerId} type="cpu" />
            <MetricsChart metrics={metrics} containerId={containerId} type="memory" />
          </div>
        </>
      )}

      {/* Error display */}
      {container.error && (
        <div
          className="card-static p-4 mb-6"
          style={{ borderColor: "rgba(239, 68, 68, 0.3)" }}
        >
          <p className="text-sm font-medium mb-1" style={{ color: "var(--color-danger)" }}>
            Error
          </p>
          <p className="mono text-xs" style={{ color: "var(--color-text-secondary)" }}>
            {container.error}
          </p>
        </div>
      )}

      {/* Logs */}
      <div className="mb-6">
        <h2 className="text-lg font-semibold mb-3" style={{ color: "var(--color-text-heading)" }}>
          Logs
        </h2>
        <LogViewer logs={logs} connected={logsConnected} />
      </div>

      {/* Timestamps */}
      <div className="card-static p-4">
        <h3 className="text-sm font-medium mb-3" style={{ color: "var(--color-text-heading)" }}>
          Timeline
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mono text-xs">
          <div>
            <span style={{ color: "var(--color-text-muted)" }}>Created: </span>
            <span style={{ color: "var(--color-text-secondary)" }}>
              {new Date(container.created_at).toLocaleString()}
            </span>
          </div>
          {container.started_at && (
            <div>
              <span style={{ color: "var(--color-text-muted)" }}>Started: </span>
              <span style={{ color: "var(--color-text-secondary)" }}>
                {new Date(container.started_at).toLocaleString()}
              </span>
            </div>
          )}
          {container.stopped_at && (
            <div>
              <span style={{ color: "var(--color-text-muted)" }}>Stopped: </span>
              <span style={{ color: "var(--color-text-secondary)" }}>
                {new Date(container.stopped_at).toLocaleString()}
              </span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
