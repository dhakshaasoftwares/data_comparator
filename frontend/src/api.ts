// Thin client for the Data Comparator REST API.

const TOKEN_KEY = "dc_token";

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: session lasts for this tab only */
  }
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T = any>(path: string, options: { method?: string; body?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(`/api${path}`, {
    method: options.method || (options.body !== undefined ? "POST" : "GET"),
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  });
  if (res.status === 401 && token) {
    setToken(null);
    window.location.assign("/login");
  }
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const detail = data?.detail;
    const message = Array.isArray(detail) ? detail.map((d: any) => d.msg).join("; ") : detail || res.statusText;
    throw new ApiError(res.status, message);
  }
  return data as T;
}

// ---- types ---------------------------------------------------------------

export type Status = "pass" | "warn" | "fail" | "error";

export interface ConfigField {
  name: string;
  label: string;
  type: "text" | "password" | "number" | "boolean" | "select" | "textarea";
  required: boolean;
  default: any;
  placeholder: string;
  help: string;
  options: string[];
  secret: boolean;
}

export interface ConnectorType {
  type: string;
  label: string;
  category: string;
  dialect: string;
  description: string;
  driver_hint: string;
  driver_installed: boolean;
  fields: ConfigField[];
}

export interface ConnectionT {
  id: number;
  name: string;
  type: string;
  config: Record<string, any>;
  secrets_set: string[];
  updated_at: string;
}

export interface QualityRule {
  type: "not_null" | "unique" | "range" | "regex" | "allowed_values" | "max_length" | "expression";
  column?: string | null;
  columns?: string[];
  min?: number | null;
  max?: number | null;
  pattern?: string | null;
  values?: string[];
  length?: number | null;
  expression?: string | null;
  side: "source" | "target" | "both";
  severity: "low" | "medium" | "high";
  max_violation_pct: number;
  name?: string | null;
}

export interface TablePair {
  source_table: string;
  target_table: string;
  key_columns: string[];
  column_mappings: { source: string; target: string }[];
  exclude_columns: string[];
  source_filter?: string | null;
  target_filter?: string | null;
  rules: QualityRule[];
}

export interface JobOptions {
  mode: "counts" | "profile" | "full";
  max_rows: number | null;
  sample_size: number;
  normalization: {
    trim_strings: boolean;
    case_insensitive: boolean;
    null_equals_empty: boolean;
    numeric_tolerance: number;
    timestamp_precision: "second" | "millisecond" | "microsecond";
  };
  weights: { completeness: number; accuracy: number; schema: number; quality: number };
  thresholds: { pass_score: number; warn_score: number };
}

export interface JobConfig {
  source_connection_id: number;
  target_connection_id: number;
  tables: TablePair[];
  options: JobOptions;
}

export interface RunT {
  id: number;
  job_id: number;
  job_name: string | null;
  status: "queued" | "running" | "completed" | "failed";
  progress: number;
  current_table: string;
  score: number | null;
  result_status: Status | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  summary: any;
  result?: any;
  config?: JobConfig;
}

export interface JobT {
  id: number;
  name: string;
  description: string;
  config: JobConfig;
  table_count: number;
  last_run: RunT | null;
  updated_at: string;
}

export const defaultOptions = (): JobOptions => ({
  mode: "full",
  max_rows: 1000000,
  sample_size: 20,
  normalization: {
    trim_strings: true,
    case_insensitive: false,
    null_equals_empty: true,
    numeric_tolerance: 0,
    timestamp_precision: "microsecond",
  },
  weights: { completeness: 30, accuracy: 35, schema: 20, quality: 15 },
  thresholds: { pass_score: 95, warn_score: 80 },
});

export const emptyPair = (source = "", target = ""): TablePair => ({
  source_table: source,
  target_table: target,
  key_columns: [],
  column_mappings: [],
  exclude_columns: [],
  source_filter: null,
  target_filter: null,
  rules: [],
});

export function fmtNum(n: number | null | undefined): string {
  if (n === null || n === undefined) return "–";
  return n.toLocaleString();
}

export function fmtDate(s: string | null | undefined): string {
  if (!s) return "–";
  return new Date(s).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function fmtScore(s: number | null | undefined): string {
  return s === null || s === undefined ? "–" : s.toFixed(1);
}
