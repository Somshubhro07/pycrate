"use client";

import { useEffect, useRef } from "react";

interface LogViewerProps {
  logs: string[];
  connected: boolean;
}

export function LogViewer({ logs, connected }: LogViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const autoScrollRef = useRef(true);

  // Auto-scroll to bottom when new logs arrive
  useEffect(() => {
    if (autoScrollRef.current && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [logs]);

  // Detect manual scroll to disable auto-scroll
  const handleScroll = () => {
    if (!containerRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = containerRef.current;
    autoScrollRef.current = scrollHeight - scrollTop - clientHeight < 40;
  };

  return (
    <div className="card-static overflow-hidden flex flex-col" style={{ height: 400 }}>
      {/* Terminal header bar */}
      <div
        className="flex items-center justify-between px-4 py-2.5 border-b"
        style={{ borderColor: "var(--color-border)", background: "var(--color-bg-secondary)" }}
      >
        <div className="flex items-center gap-2">
          <div className="flex gap-1.5">
            <span className="w-3 h-3 rounded-full" style={{ background: "#ef4444" }} />
            <span className="w-3 h-3 rounded-full" style={{ background: "#f59e0b" }} />
            <span className="w-3 h-3 rounded-full" style={{ background: "#10b981" }} />
          </div>
          <span className="mono text-xs ml-2" style={{ color: "var(--color-text-muted)" }}>
            logs
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span
            className={`inline-flex items-center gap-1.5 mono text-[10px] px-2 py-0.5 rounded-full ${
              connected ? "status-running" : "status-stopped"
            }`}
          >
            <span className={`w-1.5 h-1.5 rounded-full ${connected ? "bg-emerald-400" : "bg-amber-400"}`} />
            {connected ? "streaming" : "disconnected"}
          </span>
          <span className="mono text-[10px]" style={{ color: "var(--color-text-muted)" }}>
            {logs.length} lines
          </span>
        </div>
      </div>

      {/* Log content */}
      <div
        ref={containerRef}
        onScroll={handleScroll}
        className="terminal flex-1 overflow-y-auto border-0 rounded-none"
        style={{ background: "var(--color-bg-terminal)" }}
      >
        {logs.length === 0 ? (
          <div className="flex items-center justify-center h-full">
            <p className="mono text-sm" style={{ color: "var(--color-text-muted)" }}>
              {connected ? "Waiting for output..." : "Connect to view logs"}
            </p>
          </div>
        ) : (
          <div className="py-2">
            {logs.map((line, i) => (
              <div key={i} className="terminal-line py-0.5 flex">
                <span
                  className="w-12 shrink-0 text-right pr-3 select-none"
                  style={{ color: "var(--color-text-muted)", opacity: 0.5 }}
                >
                  {i + 1}
                </span>
                <span className="flex-1 break-all">{line}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
