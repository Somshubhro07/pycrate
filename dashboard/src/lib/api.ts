/**
 * API client and type definitions for the PyCrate dashboard.
 *
 * All API calls go through this module. The dashboard never constructs
 * URLs or headers directly.
 */

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const API_KEY = process.env.NEXT_PUBLIC_API_KEY || "";

// ---------------------------------------------------------------------------
// Types (mirroring the backend Pydantic schemas)
// ---------------------------------------------------------------------------

export interface ContainerConfig {
  container_id: string;
  name: string;
  command: string[];
  cpu_limit_percent: number;
  memory_limit_mb: number;
  env: Record<string, string>;
  hostname: string | null;
  image: string;
}

export interface NetworkInfo {
  ip_address: string | null;
  veth_host: string | null;
  veth_container: string | null;
}

export interface Container {
  container_id: string;
  name: string;
  status: "created" | "running" | "stopped" | "error";
  image: string;
  config: ContainerConfig;
  pid: number | null;
  exit_code: number | null;
  error: string | null;
  network: NetworkInfo | null;
  created_at: string;
  started_at: string | null;
  stopped_at: string | null;
}

export interface ContainerListResponse {
  containers: Container[];
  total: number;
}

export interface CreateContainerPayload {
  name: string;
  command?: string[];
  cpu_limit_percent?: number;
  memory_limit_mb?: number;
  env?: Record<string, string>;
  hostname?: string;
  image?: string;
}

export interface MemoryMetrics {
  usage_bytes: number;
  limit_bytes: number;
  usage_mb: number;
  limit_mb: number;
  usage_percent: number;
}

export interface CpuMetrics {
  usage_percent: number;
  total_usec: number;
  throttled_usec: number;
  nr_throttled: number;
}

export interface ContainerMetrics {
  container_id: string;
  timestamp: string;
  memory: MemoryMetrics;
  cpu: CpuMetrics;
  oom_killed: boolean;
}

export interface MetricsMessage {
  type: "metrics";
  timestamp: string;
  containers: ContainerMetrics[];
}

export interface SystemInfo {
  engine_version: string;
  hostname: string;
  kernel_version: string;
  total_memory_mb: number;
  available_memory_mb: number;
  cpu_count: number;
  uptime_seconds: number;
  total_containers: number;
  running_containers: number;
  stopped_containers: number;
  max_containers: number;
}

export interface HealthStatus {
  status: string;
  engine_initialized: boolean;
  database_connected: boolean;
  timestamp: string;
}

export interface ContainerEvent {
  container_id: string;
  event_type: string;
  message: string;
  timestamp: string;
  metadata: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// HTTP helpers
// ---------------------------------------------------------------------------

async function apiFetch<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const url = `${API_URL}${path}`;
  const res = await fetch(url, {
    ...options,
    credentials: "include", // Send cookies for admin auth
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": API_KEY,
      ...options.headers,
    },
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || body.error || `API error: ${res.status}`);
  }

  if (res.status === 204) return undefined as T;
  return res.json();
}

// ---------------------------------------------------------------------------
// Container endpoints
// ---------------------------------------------------------------------------

export async function listContainers(
  status?: string
): Promise<ContainerListResponse> {
  const query = status ? `?status=${status}` : "";
  return apiFetch(`/api/containers${query}`);
}

export async function getContainer(id: string): Promise<Container> {
  return apiFetch(`/api/containers/${id}`);
}

export async function createContainer(
  payload: CreateContainerPayload
): Promise<Container> {
  return apiFetch("/api/containers", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function startContainer(id: string): Promise<Container> {
  return apiFetch(`/api/containers/${id}/start`, { method: "POST" });
}

export async function stopContainer(
  id: string,
  timeout = 10
): Promise<Container> {
  return apiFetch(`/api/containers/${id}/stop`, {
    method: "POST",
    body: JSON.stringify({ timeout }),
  });
}

export async function removeContainer(id: string): Promise<void> {
  return apiFetch(`/api/containers/${id}`, { method: "DELETE" });
}

export async function getContainerLogs(
  id: string,
  tail?: number
): Promise<{ container_id: string; logs: string[]; total_lines: number }> {
  const query = tail ? `?tail=${tail}` : "";
  return apiFetch(`/api/containers/${id}/logs${query}`);
}

export async function getContainerEvents(
  id: string,
  limit = 50
): Promise<ContainerEvent[]> {
  return apiFetch(`/api/containers/${id}/events?limit=${limit}`);
}

// ---------------------------------------------------------------------------
// System endpoints
// ---------------------------------------------------------------------------

export async function getSystemInfo(): Promise<SystemInfo> {
  return apiFetch("/api/system/info");
}

export async function getHealth(): Promise<HealthStatus> {
  return apiFetch("/api/health");
}

// ---------------------------------------------------------------------------
// Suggestions endpoints (public)
// ---------------------------------------------------------------------------

export interface SuggestionPayload {
  name: string;
  email?: string;
  category: string;
  message: string;
  website: string; // Honeypot — must be empty
}

export async function submitSuggestion(
  payload: SuggestionPayload
): Promise<{ id: string; message: string }> {
  return apiFetch("/api/suggestions", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// ---------------------------------------------------------------------------
// Admin endpoints (cookie-authenticated)
// ---------------------------------------------------------------------------

export async function adminLogin(
  adminKey: string
): Promise<{ authenticated: boolean; message: string }> {
  return apiFetch("/api/admin/login", {
    method: "POST",
    body: JSON.stringify({ admin_key: adminKey }),
  });
}

export async function adminLogout(): Promise<void> {
  return apiFetch("/api/admin/logout", { method: "POST" });
}

export async function adminCheckAuth(): Promise<{ authenticated: boolean }> {
  return apiFetch("/api/admin/me");
}

export interface Suggestion {
  id: string;
  name: string;
  email: string | null;
  category: string;
  message: string;
  is_read: boolean;
  created_at: string;
}

export interface SuggestionListResponse_Admin {
  suggestions: Suggestion[];
  total: number;
  unread: number;
}

export async function adminGetSuggestions(
  isRead?: boolean
): Promise<SuggestionListResponse_Admin> {
  const query = isRead !== undefined ? `?is_read=${isRead}` : "";
  return apiFetch(`/api/admin/suggestions${query}`);
}

export async function adminMarkRead(id: string): Promise<void> {
  return apiFetch(`/api/admin/suggestions/${id}/read`, { method: "PATCH" });
}

export async function adminDeleteSuggestion(id: string): Promise<void> {
  return apiFetch(`/api/admin/suggestions/${id}`, { method: "DELETE" });
}

// ---------------------------------------------------------------------------
// WebSocket helpers
// ---------------------------------------------------------------------------

export function getMetricsWsUrl(): string {
  const wsBase = API_URL.replace(/^http/, "ws");
  return `${wsBase}/ws/metrics?api_key=${API_KEY}`;
}

export function getLogsWsUrl(containerId: string): string {
  const wsBase = API_URL.replace(/^http/, "ws");
  return `${wsBase}/ws/logs/${containerId}?api_key=${API_KEY}`;
}

