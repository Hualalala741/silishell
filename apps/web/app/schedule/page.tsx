"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, formatWhen, type ScheduledTask } from "./shared";

export default function ScheduleTasksPage() {
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [loading, setLoading] = useState(true);
  const [pendingKey, setPendingKey] = useState("");
  const [deletingKey, setDeletingKey] = useState("");
  const [error, setError] = useState("");

  async function load() {
    const items = await api<ScheduledTask[]>("/schedules");
    setTasks(items);
    setPendingKey("");
  }

  useEffect(() => {
    load()
      .catch((cause) => {
        setError(cause instanceof Error ? cause.message : "无法连接后端");
      })
      .finally(() => setLoading(false));
  }, []);

  async function remove(topicKey: string) {
    setDeletingKey(topicKey);
    setError("");
    try {
      await api(`/schedules/${encodeURIComponent(topicKey)}`, { method: "DELETE" });
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "删除失败");
    } finally {
      setDeletingKey("");
    }
  }

  return <main className="shell">
    <section className="hero">
      <div>
        <p className="eyebrow">YONGCHUN · SCHEDULED CRAWL</p>
        <h1>采集项目<em>管理</em></h1>
        <p className="hero-copy">这里只显示已经设定的定时任务。删除后，这个项目不会再自动爬取。</p>
      </div>
      <Link className="primary schedule-create" href="/schedule/new">新建定时采集</Link>
    </section>

    {error && <div className="alert"><strong>操作未完成</strong><span>{error}</span><button onClick={() => setError("")} aria-label="关闭">×</button></div>}

    <section className="card schedule-panel task-panel">
      {loading ? <div className="schedule-empty">正在读取任务…</div> : tasks.length === 0 ? <div className="schedule-empty">还没有定时任务。可以去新建定时采集，为已经抓取过的项目设定频率。</div> : <div className="task-list">
        {tasks.map((task) => <article className="task-row" key={task.topic_key}>
          <div>
            <strong>{task.name}</strong>
            <span>{task.schedule.label}</span>
          </div>
          <p className={task.schedule.last_status === "failed" ? "failed" : ""}>
            上次执行：{formatWhen(task.schedule.last_run_at)}
            {task.schedule.last_message ? `。${task.schedule.last_message}` : ""}
          </p>
          {pendingKey === task.topic_key ? <div className="schedule-actions">
            <button className="ghost-button danger" onClick={() => remove(task.topic_key)} disabled={deletingKey === task.topic_key}>{deletingKey === task.topic_key ? "正在删除…" : "确认删除"}</button>
            <button className="ghost-button" onClick={() => setPendingKey("")} disabled={deletingKey === task.topic_key}>取消</button>
          </div> : <button className="ghost-button" onClick={() => setPendingKey(task.topic_key)}>删除</button>}
        </article>)}
      </div>}
    </section>
  </main>;
}
