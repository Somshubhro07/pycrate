"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import type { MetricsMessage, ContainerMetrics } from "@/lib/api";
import { getMetricsWsUrl, getLogsWsUrl } from "@/lib/api";

/**
 * Hook for streaming container metrics via WebSocket.
 *
 * Connects to /ws/metrics and provides the latest metrics snapshot
 * for all running containers. Auto-reconnects on disconnect with
 * exponential backoff.
 */
export function useMetrics() {
  const [metrics, setMetrics] = useState<ContainerMetrics[]>([]);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const retryRef = useRef(0);
  const maxRetries = 10;

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const ws = new WebSocket(getMetricsWsUrl());
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      retryRef.current = 0;
    };

    ws.onmessage = (event) => {
      try {
        const data: MetricsMessage = JSON.parse(event.data);
        if (data.type === "metrics") {
          setMetrics(data.containers);
        }
      } catch {
        // Skip malformed messages
      }
    };

    ws.onclose = () => {
      setConnected(false);
      wsRef.current = null;

      // Reconnect with exponential backoff
      if (retryRef.current < maxRetries) {
        const delay = Math.min(1000 * 2 ** retryRef.current, 30000);
        retryRef.current++;
        setTimeout(connect, delay);
      }
    };

    ws.onerror = () => {
      ws.close();
    };
  }, []);

  useEffect(() => {
    connect();
    return () => {
      wsRef.current?.close();
    };
  }, [connect]);

  return { metrics, connected };
}

/**
 * Hook for streaming container logs via WebSocket.
 *
 * Connects to /ws/logs/{id} and accumulates log lines.
 * Returns the log buffer and connection status.
 */
export function useLogs(containerId: string | null) {
  const [logs, setLogs] = useState<string[]>([]);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!containerId) return;

    setLogs([]);
    const ws = new WebSocket(getLogsWsUrl(containerId));
    wsRef.current = ws;

    ws.onopen = () => setConnected(true);

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === "log") {
          setLogs((prev) => [...prev.slice(-4999), data.line]);
        }
      } catch {
        // Skip
      }
    };

    ws.onclose = () => {
      setConnected(false);
      wsRef.current = null;
    };

    ws.onerror = () => ws.close();

    return () => {
      ws.close();
    };
  }, [containerId]);

  return { logs, connected };
}

/**
 * Hook for polling container data via REST API.
 *
 * Fetches at a regular interval and provides loading/error state.
 */
export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs = 5000
): { data: T | null; loading: boolean; error: string | null; refetch: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const result = await fetcher();
      setData(result);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }, [fetcher]);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, intervalMs);
    return () => clearInterval(interval);
  }, [fetchData, intervalMs]);

  return { data, loading, error, refetch: fetchData };
}
