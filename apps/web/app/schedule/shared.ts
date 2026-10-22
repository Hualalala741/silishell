export const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

export type ScheduleKind = "interval" | "daily" | "monthly";
export type IntervalUnit = "hours" | "days" | "weeks" | "months";

export type Schedule = {
  enabled: boolean;
  kind: ScheduleKind;
  interval_value: number | null;
  interval_unit: IntervalUnit | null;
  day_of_month: number | null;
  weekday: number | null;
  time: string | null;
  label: string;
  last_run_at: string | null;
  last_status: string | null;
  last_message: string | null;
};

export type Project = {
  topic_key: string;
  name: string;
  crawled_count: number;
  last_crawled_at: string | null;
  has_profile: boolean;
  schedule: Schedule | null;
};

export type ScheduledTask = {
  topic_key: string;
  name: string;
  schedule: Schedule;
};

export const INTERVAL_LIMITS: Record<IntervalUnit, [number, number]> = {
  hours: [1, 168],
  days: [1, 180],
  weeks: [1, 26],
  months: [1, 6],
};

export const UNIT_LABELS: Record<IntervalUnit, string> = {
  hours: "小时",
  days: "天",
  weeks: "周",
  months: "个月",
};

export const WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"];

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...options?.headers },
    cache: "no-store",
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = typeof body.detail === "string" ? body.detail : "请求失败，请稍后重试";
    throw new Error(detail);
  }
  return response.json();
}

export function formatWhen(value: string | null) {
  if (!value) return "尚未执行";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}
