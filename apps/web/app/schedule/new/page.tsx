"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  INTERVAL_LIMITS, UNIT_LABELS, WEEKDAYS, api,
  type IntervalUnit, type Project, type ScheduleKind,
} from "../shared";

const UNITS = Object.keys(INTERVAL_LIMITS) as IntervalUnit[];

function describe(kind: ScheduleKind, value: number, unit: IntervalUnit, day: number, weekday: number, time: string) {
  if (kind === "daily") return `每天 ${time}`;
  if (kind === "monthly") return `每月 ${day} 日 ${time}`;
  if (unit === "hours") return `每 ${value} 小时`;
  if (unit === "days") return `每 ${value} 天 ${time}`;
  if (unit === "weeks") return `每 ${value} 周的周${WEEKDAYS[weekday]} ${time}`;
  return `约每 ${value} 个月 ${time}`;
}

export default function NewSchedulePage() {
  const router = useRouter();
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedKey, setSelectedKey] = useState("");
  const [kind, setKind] = useState<ScheduleKind>("daily");
  const [intervalValue, setIntervalValue] = useState(6);
  const [intervalUnit, setIntervalUnit] = useState<IntervalUnit>("hours");
  const [dayOfMonth, setDayOfMonth] = useState(1);
  const [weekday, setWeekday] = useState(0);
  const [time, setTime] = useState("08:00");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const selected = projects.find((item) => item.topic_key === selectedKey) || null;
  const [low, high] = INTERVAL_LIMITS[intervalUnit];
  const needsClock = kind !== "interval" || intervalUnit !== "hours";
  const summary = describe(kind, intervalValue, intervalUnit, dayOfMonth, weekday, time.slice(0, 5));

  function applyProject(project: Project) {
    setSelectedKey(project.topic_key);
    const schedule = project.schedule;
    if (!schedule) {
      setKind("daily");
      setTime("08:00");
      return;
    }
    setKind(schedule.kind);
    setTime(schedule.time || "08:00");
    setIntervalValue(schedule.interval_value || 6);
    setIntervalUnit(schedule.interval_unit || "hours");
    setDayOfMonth(schedule.day_of_month || 1);
    setWeekday(schedule.weekday ?? 0);
  }

  useEffect(() => {
    api<Project[]>("/projects")
      .then((items) => {
        setProjects(items);
        if (items[0]) applyProject(items[0]);
      })
      .catch((cause) => {
        setError(cause instanceof Error ? cause.message : "无法连接后端");
      })
      .finally(() => setLoading(false));
  }, []);

  function changeUnit(unit: IntervalUnit) {
    const [nextLow, nextHigh] = INTERVAL_LIMITS[unit];
    setIntervalUnit(unit);
    setIntervalValue((current) => Math.min(nextHigh, Math.max(nextLow, current)));
  }

  async function save() {
    if (!selected) return;
    setSaving(true);
    setError("");
    try {
      const clock = time.slice(0, 5);
      await api("/schedules", {
        method: "PUT",
        body: JSON.stringify({
          topic_key: selected.topic_key,
          kind,
          interval_value: kind === "interval" ? intervalValue : null,
          interval_unit: kind === "interval" ? intervalUnit : null,
          day_of_month: kind === "monthly" ? dayOfMonth : null,
          weekday: kind === "interval" && intervalUnit === "weeks" ? weekday : null,
          time: kind === "interval" && intervalUnit === "hours" ? null : clock,
        }),
      });
      router.push("/schedule");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  return <main className="shell">
    <section className="hero">
      <div>
        <p className="eyebrow">YONGCHUN · NEW SCHEDULE</p>
        <h1>选择已采集项目，<br/><em>设定自动爬取的频率。</em></h1>
        <p className="hero-copy">可以按固定间隔执行，也可以每天同一时刻，或每月固定一天。时间都按北京时间。</p>
      </div>
    </section>

    {error && <div className="alert"><strong>操作未完成</strong><span>{error}</span><button onClick={() => setError("")} aria-label="关闭">×</button></div>}

    <div className="schedule-page">
      <aside className="card project-picker">
        <p className="eyebrow">PROJECT</p>
        <h2>选择项目</h2>
        <p>列表来自已经成功抓取过正文的项目。</p>
        {loading ? <div className="schedule-empty">正在读取项目…</div> : projects.length === 0 ? <div className="schedule-empty">还没有可选择的项目。请先在政策采集页完成一次抓取。</div> : <div className="project-options">
          {projects.map((project) => <button className={project.topic_key === selectedKey ? "active" : ""} key={project.topic_key} onClick={() => applyProject(project)}>
            <span>{project.name.slice(0, 1)}</span>
            <div>
              <strong>{project.name}</strong>
              <small>{project.crawled_count} 篇已入库{project.schedule ? ` · ${project.schedule.label}` : ""}</small>
            </div>
          </button>)}
        </div>}
      </aside>

      <section className="card schedule-panel">
        <p className="eyebrow">FREQUENCY</p>
        <h2>执行频率</h2>
        {selected ? <>
          <p className="schedule-copy">为「{selected.name}」设置自动爬取。保存后，后端保持运行时会按这个频率执行。</p>
          <div className="frequency-switch" role="tablist" aria-label="执行频率">
            {([["interval", "固定间隔"], ["daily", "每天"], ["monthly", "每月"]] as const).map(([value, label]) =>
              <button className={kind === value ? "active" : ""} key={value} type="button" onClick={() => setKind(value)}>{label}</button>)}
          </div>

          {kind === "interval" && <div className="frequency-fields">
            <label className="schedule-time">
              <span>每隔</span>
              <input type="number" min={low} max={high} value={intervalValue} onChange={(event) => setIntervalValue(Number(event.target.value))} />
            </label>
            <label className="schedule-time">
              <span>单位</span>
              <select value={intervalUnit} onChange={(event) => changeUnit(event.target.value as IntervalUnit)}>
                {UNITS.map((unit) => <option value={unit} key={unit}>{UNIT_LABELS[unit]}</option>)}
              </select>
            </label>
          </div>}
          {kind === "interval" && <p className="schedule-hint">
            {intervalUnit === "hours" && "范围 1–168 小时。从保存后开始计时，不固定钟点。"}
            {intervalUnit === "days" && "范围 1–180 天，并在每天的指定时刻执行。"}
            {intervalUnit === "weeks" && "范围 1–26 周，并固定到某一天的指定时刻。"}
            {intervalUnit === "months" && "范围 1–6 个月，按 30 天估算。若要每月固定日期，请改用「每月」。"}
          </p>}

          {kind === "interval" && intervalUnit === "weeks" && <div className="weekday-switch" aria-label="星期">
            {WEEKDAYS.map((name, index) => <button className={weekday === index ? "active" : ""} type="button" key={name} onClick={() => setWeekday(index)}>周{name}</button>)}
          </div>}

          {kind === "monthly" && <label className="schedule-time">
            <span>每月日期</span>
            <select value={dayOfMonth} onChange={(event) => setDayOfMonth(Number(event.target.value))}>
              {Array.from({ length: 28 }, (_, index) => index + 1).map((day) => <option value={day} key={day}>{day} 日</option>)}
            </select>
          </label>}
          {kind === "monthly" && <p className="schedule-hint">只提供 1–28 日，2 月和短月也会执行。</p>}

          {needsClock && <label className="schedule-time">
            <span>执行时刻</span>
            <input type="time" value={time} onChange={(event) => setTime(event.target.value)} />
          </label>}

          {!selected.has_profile && <div className="alert"><strong>还不能定时</strong><span>这个项目没有保存检索条件。请回到政策采集页，用同一需求再抓取一次。</span></div>}
          <div className="schedule-status">
            <strong>{summary}</strong>
            <p>{selected.schedule ? `当前已有定时：${selected.schedule.label}。保存后会覆盖。` : "保存后会出现在采集项目管理里。"}</p>
          </div>
          <div className="schedule-actions">
            <button className="primary" onClick={save} disabled={saving || !selected.has_profile || (needsClock && !time)}>{saving ? "正在保存…" : "保存定时"}</button>
          </div>
        </> : <div className="schedule-empty">请先在左侧选择一个项目。</div>}
      </section>
    </div>
  </main>;
}
