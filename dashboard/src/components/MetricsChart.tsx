"use client";

import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Area,
  AreaChart,
} from "recharts";
import { useRef, useEffect, useState } from "react";
import type { ContainerMetrics } from "@/lib/api";

interface MetricsChartProps {
  metrics: ContainerMetrics[];
  containerId: string;
  type: "cpu" | "memory";
}

interface DataPoint {
  time: string;
  value: number;
}

const MAX_POINTS = 60;

export function MetricsChart({ metrics, containerId, type }: MetricsChartProps) {
  const [data, setData] = useState<DataPoint[]>([]);

  useEffect(() => {
    const containerMetrics = metrics.find((m) => m.container_id === containerId);
    if (!containerMetrics) return;

    const value = type === "cpu"
      ? containerMetrics.cpu.usage_percent
      : containerMetrics.memory.usage_percent;

    const now = new Date();
    const timeStr = `${now.getMinutes().toString().padStart(2, "0")}:${now.getSeconds().toString().padStart(2, "0")}`;

    setData((prev) => {
      const next = [...prev, { time: timeStr, value: Math.round(value * 100) / 100 }];
      return next.slice(-MAX_POINTS);
    });
  }, [metrics, containerId, type]);

  const color = type === "cpu" ? "#00b8d4" : "#8b5cf6";
  const label = type === "cpu" ? "CPU %" : "Memory %";

  return (
    <div className="card-static p-4">
      <div className="flex items-center justify-between mb-3">
        <h4 className="text-sm font-medium" style={{ color: "var(--color-text-heading)" }}>
          {label}
        </h4>
        {data.length > 0 && (
          <span className="mono text-sm font-bold" style={{ color }}>
            {data[data.length - 1].value.toFixed(1)}%
          </span>
        )}
      </div>
      <div style={{ height: 160 }}>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data}>
            <defs>
              <linearGradient id={`gradient-${type}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={color} stopOpacity={0.3} />
                <stop offset="100%" stopColor={color} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid
              strokeDasharray="3 3"
              stroke="var(--color-border-subtle)"
              vertical={false}
            />
            <XAxis
              dataKey="time"
              tick={{ fontSize: 10, fill: "var(--color-text-muted)", fontFamily: "var(--font-mono)" }}
              axisLine={false}
              tickLine={false}
              interval="preserveStartEnd"
            />
            <YAxis
              domain={[0, 100]}
              tick={{ fontSize: 10, fill: "var(--color-text-muted)", fontFamily: "var(--font-mono)" }}
              axisLine={false}
              tickLine={false}
              width={30}
            />
            <Tooltip
              contentStyle={{
                background: "var(--color-bg-card)",
                border: "1px solid var(--color-border)",
                borderRadius: "6px",
                fontFamily: "var(--font-mono)",
                fontSize: "12px",
              }}
              labelStyle={{ color: "var(--color-text-muted)" }}
              itemStyle={{ color }}
            />
            <Area
              type="monotone"
              dataKey="value"
              stroke={color}
              strokeWidth={2}
              fill={`url(#gradient-${type})`}
              dot={false}
              animationDuration={300}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
