"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { motion } from "framer-motion";
import { createContainer, startContainer } from "@/lib/api";

export default function CreateContainerPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [form, setForm] = useState({
    name: "",
    command: "/bin/sh",
    cpu_limit_percent: 50,
    memory_limit_mb: 64,
    hostname: "",
    autoStart: true,
  });

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);

    try {
      const container = await createContainer({
        name: form.name,
        command: form.command.split(" ").filter(Boolean),
        cpu_limit_percent: form.cpu_limit_percent,
        memory_limit_mb: form.memory_limit_mb,
        hostname: form.hostname || undefined,
      });

      if (form.autoStart) {
        await startContainer(container.container_id);
      }

      router.push(`/containers/${container.container_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create container");
    } finally {
      setLoading(false);
    }
  };

  const inputStyle = {
    background: "var(--color-bg-input)",
    border: "1px solid var(--color-border)",
    color: "var(--color-text-primary)",
  };

  return (
    <div className="max-w-2xl mx-auto">
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }}>
        <h1 className="text-2xl font-bold mb-2" style={{ color: "var(--color-text-heading)" }}>
          Create Container
        </h1>
        <p className="text-sm mb-8" style={{ color: "var(--color-text-muted)" }}>
          Configure and launch a new isolated process.
        </p>
      </motion.div>

      <motion.form
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.1 }}
        onSubmit={handleSubmit}
        className="card-static p-6 space-y-5"
      >
        {/* Name */}
        <div>
          <label className="block text-sm font-medium mb-1.5" style={{ color: "var(--color-text-secondary)" }}>
            Container Name
          </label>
          <input
            type="text"
            required
            pattern="^[a-zA-Z0-9][a-zA-Z0-9_.\-]*$"
            placeholder="my-container"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            className="w-full px-3 py-2.5 rounded-lg mono text-sm outline-none focus:ring-1 transition-shadow"
            style={{ ...inputStyle, "--tw-ring-color": "var(--color-accent-dim)" } as React.CSSProperties}
          />
          <p className="text-[11px] mt-1" style={{ color: "var(--color-text-muted)" }}>
            Alphanumeric characters, hyphens, underscores, and dots.
          </p>
        </div>

        {/* Command */}
        <div>
          <label className="block text-sm font-medium mb-1.5" style={{ color: "var(--color-text-secondary)" }}>
            Command
          </label>
          <input
            type="text"
            required
            placeholder="/bin/sh"
            value={form.command}
            onChange={(e) => setForm({ ...form, command: e.target.value })}
            className="w-full px-3 py-2.5 rounded-lg mono text-sm outline-none focus:ring-1 transition-shadow"
            style={inputStyle}
          />
        </div>

        {/* CPU + Memory side by side */}
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium mb-1.5" style={{ color: "var(--color-text-secondary)" }}>
              CPU Limit (%)
            </label>
            <input
              type="range"
              min={1}
              max={100}
              value={form.cpu_limit_percent}
              onChange={(e) => setForm({ ...form, cpu_limit_percent: parseInt(e.target.value) })}
              className="w-full accent-cyan-500"
            />
            <div className="flex justify-between mono text-xs mt-1" style={{ color: "var(--color-text-muted)" }}>
              <span>1%</span>
              <span className="font-bold" style={{ color: "var(--color-accent)" }}>
                {form.cpu_limit_percent}%
              </span>
              <span>100%</span>
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium mb-1.5" style={{ color: "var(--color-text-secondary)" }}>
              Memory Limit (MB)
            </label>
            <input
              type="range"
              min={4}
              max={512}
              step={4}
              value={form.memory_limit_mb}
              onChange={(e) => setForm({ ...form, memory_limit_mb: parseInt(e.target.value) })}
              className="w-full accent-cyan-500"
            />
            <div className="flex justify-between mono text-xs mt-1" style={{ color: "var(--color-text-muted)" }}>
              <span>4MB</span>
              <span className="font-bold" style={{ color: "var(--color-accent)" }}>
                {form.memory_limit_mb}MB
              </span>
              <span>512MB</span>
            </div>
          </div>
        </div>

        {/* Hostname */}
        <div>
          <label className="block text-sm font-medium mb-1.5" style={{ color: "var(--color-text-secondary)" }}>
            Hostname (optional)
          </label>
          <input
            type="text"
            placeholder="Defaults to container ID"
            value={form.hostname}
            onChange={(e) => setForm({ ...form, hostname: e.target.value })}
            className="w-full px-3 py-2.5 rounded-lg mono text-sm outline-none focus:ring-1 transition-shadow"
            style={inputStyle}
          />
        </div>

        {/* Auto-start toggle */}
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => setForm({ ...form, autoStart: !form.autoStart })}
            className="w-10 h-5 rounded-full transition-colors relative"
            style={{
              background: form.autoStart ? "var(--color-accent)" : "var(--color-bg-input)",
              border: `1px solid ${form.autoStart ? "var(--color-accent-dim)" : "var(--color-border)"}`,
            }}
          >
            <span
              className="absolute top-0.5 w-3.5 h-3.5 rounded-full bg-white transition-transform"
              style={{ left: form.autoStart ? "calc(100% - 18px)" : "2px" }}
            />
          </button>
          <span className="text-sm" style={{ color: "var(--color-text-secondary)" }}>
            Start immediately after creation
          </span>
        </div>

        {/* Error */}
        {error && (
          <div
            className="p-3 rounded-lg mono text-xs"
            style={{ background: "var(--color-danger-dim)", color: "var(--color-danger)" }}
          >
            {error}
          </div>
        )}

        {/* Submit */}
        <button
          type="submit"
          disabled={loading || !form.name}
          className="w-full py-3 rounded-lg font-medium text-sm transition-all disabled:opacity-50"
          style={{
            background: loading ? "var(--color-accent-dim)" : "var(--color-accent)",
            color: "#070b14",
          }}
        >
          {loading ? "Creating..." : "Create Container"}
        </button>
      </motion.form>
    </div>
  );
}
