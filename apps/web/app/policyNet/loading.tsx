import styles from "./policy-net.module.css";

export default function Loading() {
  return <main className={styles.loading} aria-live="polite" aria-busy="true">
    <span className={styles.loader} aria-hidden="true"/>
    <span>正在从数据库载入政策图谱…</span>
  </main>;
}
