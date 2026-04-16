"use client";

import { motion } from "framer-motion";

interface StatusBadgeProps {
  status: "created" | "running" | "stopped" | "error";
  size?: "sm" | "md";
}

export function StatusBadge({ status, size = "md" }: StatusBadgeProps) {
  const sizeClass = size === "sm" ? "text-[10px] px-2 py-0.5" : "text-xs px-2.5 py-1";

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full font-medium mono uppercase tracking-wider status-${status} ${sizeClass}`}
    >
      {status === "running" && (
        <span className="relative flex h-2 w-2">
          <motion.span
            className="absolute inline-flex h-full w-full rounded-full opacity-75"
            style={{ background: "var(--color-success)" }}
            animate={{ scale: [1, 1.8, 1], opacity: [0.75, 0, 0.75] }}
            transition={{ duration: 1.5, repeat: Infinity, ease: "easeInOut" }}
          />
          <span
            className="relative inline-flex rounded-full h-2 w-2"
            style={{ background: "var(--color-success)" }}
          />
        </span>
      )}
      {status}
    </span>
  );
}
