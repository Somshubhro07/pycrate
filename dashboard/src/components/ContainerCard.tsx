"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import type { Container, ContainerMetrics } from "@/lib/api";
import { StatusBadge } from "./StatusBadge";
import { ResourceGauge } from "./ResourceGauge";

interface ContainerCardProps {
  container: Container;
  metrics?: ContainerMetrics;
  index?: number;
  onStart?: (id: string) => void;
  onStop?: (id: string) => void;
}

export function ContainerCard({ container, metrics, index = 0, onStart, onStop }: ContainerCardProps) {
  const isRunning = container.status === "running";
  const cpuPercent = metrics?.cpu.usage_percent ?? 0;
  const memPercent = metrics?.memory.usage_percent ?? 0;
  const memMb = metrics?.memory.usage_mb ?? 0;

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, delay: index * 0.05 }}
    >
      <Link href={`/containers/${container.container_id}`} className="block">
        <div className="card p-5 cursor-pointer group">
          {/* Header */}
          <div className="flex items-start justify-between mb-4">
            <div className="min-w-0 flex-1">
              <h3
                className="text-sm font-semibold truncate"
                style={{ color: "var(--color-text-heading)" }}
              >
                {container.name}
              </h3>
              <p
                className="mono text-xs mt-0.5 truncate"
                style={{ color: "var(--color-text-muted)" }}
              >
                {container.container_id}
              </p>
            </div>
            <StatusBadge status={container.status} size="sm" />
          </div>

          {/* Metrics gauges (only when running) */}
          {isRunning && (
            <div className="flex gap-4 mb-4">
              <ResourceGauge label="CPU" value={cpuPercent} max={100} unit="%" size="sm" />
              <ResourceGauge label="MEM" value={memMb} max={container.config.memory_limit_mb} unit="MB" size="sm" />
            </div>
          )}

          {/* Footer info */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <span
                className="mono text-[11px] px-2 py-0.5 rounded"
                style={{
                  background: "var(--color-bg-input)",
                  color: "var(--color-text-muted)",
                }}
              >
                {container.image}
              </span>
              {container.network?.ip_address && (
                <span
                  className="mono text-[11px]"
                  style={{ color: "var(--color-text-muted)" }}
                >
                  {container.network.ip_address}
                </span>
              )}
            </div>

            {/* Action button */}
            <div onClick={(e) => e.preventDefault()}>
              {isRunning && onStop && (
                <button
                  onClick={() => onStop(container.container_id)}
                  className="px-3 py-1 rounded-md text-xs font-medium transition-colors"
                  style={{
                    background: "var(--color-warning-dim)",
                    color: "var(--color-warning)",
                    border: "1px solid rgba(245, 158, 11, 0.2)",
                  }}
                >
                  Stop
                </button>
              )}
              {(container.status === "created" || container.status === "stopped") && onStart && (
                <button
                  onClick={() => onStart(container.container_id)}
                  className="px-3 py-1 rounded-md text-xs font-medium transition-colors"
                  style={{
                    background: "var(--color-success-dim)",
                    color: "var(--color-success)",
                    border: "1px solid rgba(16, 185, 129, 0.2)",
                  }}
                >
                  Start
                </button>
              )}
            </div>
          </div>
        </div>
      </Link>
    </motion.div>
  );
}
