"use client";

import { useCallback } from "react";
import { motion } from "framer-motion";
import { usePolling } from "@/hooks/useWebSocket";
import { getSystemInfo, getHealth } from "@/lib/api";
import type { SystemInfo, HealthStatus } from "@/lib/api";

function formatUptime(seconds: number): string {
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h ${mins}m`;
  if (hours > 0) return `${hours}h ${mins}m`;
  return `${mins}m`;
}

export default function SystemPage() {
  const fetchSystem = useCallback(() => getSystemInfo(), []);
  const fetchHealth = useCallback(() => getHealth(), []);

  const { data: system } = usePolling<SystemInfo>(fetchSystem, 10000);
  const { data: health } = usePolling<HealthStatus>(fetchHealth, 5000);

  if (!system) {
    return (
      <div className="flex items-center justify-center h-64">
        <p className="mono text-sm" style={{ color: "var(--color-text-muted)" }}>
          Loading system info...
        </p>
      </div>
    );
  }

  const sections = [
    {
      title: "Host",
      items: [
        { label: "Hostname", value: system.hostname },
        { label: "Kernel", value: system.kernel_version },
        { label: "CPU Cores", value: system.cpu_count.toString() },
        { label: "Uptime", value: formatUptime(system.uptime_seconds) },
      ],
    },
    {
      title: "Memory",
      items: [
        { label: "Total", value: `${system.total_memory_mb.toFixed(0)} MB` },
        { label: "Available", value: `${system.available_memory_mb.toFixed(0)} MB` },
        {
          label: "Used",
          value: `${(system.total_memory_mb - system.available_memory_mb).toFixed(0)} MB`,
        },
        {
          label: "Usage",
          value: `${(((system.total_memory_mb - system.available_memory_mb) / system.total_memory_mb) * 100).toFixed(1)}%`,
        },
      ],
    },
    {
      title: "Engine",
      items: [
        { label: "Version", value: `v${system.engine_version}` },
        { label: "Total Containers", value: system.total_containers.toString() },
        { label: "Running", value: system.running_containers.toString() },
        { label: "Stopped", value: system.stopped_containers.toString() },
        { label: "Max Capacity", value: system.max_containers.toString() },
      ],
    },
  ];

  return (
    <div className="max-w-4xl mx-auto">
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }}>
        <h1 className="text-2xl font-bold mb-2" style={{ color: "var(--color-text-heading)" }}>
          System
        </h1>
        <p className="text-sm mb-8" style={{ color: "var(--color-text-muted)" }}>
          Host and engine status
        </p>
      </motion.div>

      {/* Health status */}
      {health && (
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          className="card-static p-5 mb-6"
        >
          <h2 className="text-sm font-semibold mb-3" style={{ color: "var(--color-text-heading)" }}>
            Health
          </h2>
          <div className="grid grid-cols-3 gap-4">
            <div className="flex items-center gap-2">
              <span
                className="w-2.5 h-2.5 rounded-full"
                style={{
                  background: health.status === "ok" ? "var(--color-success)" : "var(--color-warning)",
                }}
              />
              <span className="mono text-sm" style={{ color: "var(--color-text-secondary)" }}>
                Status: {health.status}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <span
                className="w-2.5 h-2.5 rounded-full"
                style={{
                  background: health.engine_initialized ? "var(--color-success)" : "var(--color-danger)",
                }}
              />
              <span className="mono text-sm" style={{ color: "var(--color-text-secondary)" }}>
                Engine: {health.engine_initialized ? "initialized" : "down"}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <span
                className="w-2.5 h-2.5 rounded-full"
                style={{
                  background: health.database_connected ? "var(--color-success)" : "var(--color-danger)",
                }}
              />
              <span className="mono text-sm" style={{ color: "var(--color-text-secondary)" }}>
                Database: {health.database_connected ? "connected" : "down"}
              </span>
            </div>
          </div>
        </motion.div>
      )}

      {/* Info sections */}
      <div className="space-y-4">
        {sections.map((section, si) => (
          <motion.div
            key={section.title}
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: si * 0.05 }}
            className="card-static p-5"
          >
            <h2 className="text-sm font-semibold mb-3" style={{ color: "var(--color-text-heading)" }}>
              {section.title}
            </h2>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-y-3 gap-x-6">
              {section.items.map((item) => (
                <div key={item.label}>
                  <p className="text-[10px] font-medium mb-0.5" style={{ color: "var(--color-text-muted)" }}>
                    {item.label}
                  </p>
                  <p className="mono text-sm" style={{ color: "var(--color-text-heading)" }}>
                    {item.value}
                  </p>
                </div>
              ))}
            </div>
          </motion.div>
        ))}
      </div>
    </div>
  );
}
