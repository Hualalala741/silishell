"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

const STORAGE_KEY = "craw1-nav-collapsed";

const COLLECT_NAV = [
  { href: "/", label: "新建采集任务" },
  { href: "/schedule", label: "定时采集管理" },
  { href: "/schedule/new", label: "新建定时采集" },
] as const;

function isCollectPath(pathname: string) {
  return COLLECT_NAV.some((item) => item.href === pathname);
}

function NavIcon({ name }: { name: "collect" | "graph" | "land" }) {
  const paths = {
    collect: <><circle cx="11" cy="11" r="6.5"/><path d="m16 16 4 4"/><path d="M11 8v6M8 11h6"/></>,
    graph: <><circle cx="6" cy="7" r="2.2"/><circle cx="17.5" cy="6.5" r="2.2"/><circle cx="12" cy="17" r="2.2"/><path d="M8 8.2 10.4 15M15.6 8.2 13.4 15M8.1 7h7.2"/></>,
    land: <><path d="m3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3Z"/><path d="M9 3v15M15 6v15"/><path d="m4.5 14 3-2 3 1.5 3.5-3 5.5 1.5"/></>,
  };
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    setCollapsed(window.localStorage.getItem(STORAGE_KEY) === "1");
  }, []);

  function toggle() {
    setCollapsed((current) => {
      const next = !current;
      window.localStorage.setItem(STORAGE_KEY, next ? "1" : "0");
      return next;
    });
  }

  return <div className={`app-shell ${collapsed ? "collapsed" : ""}`}>
    <aside className="nav-sidebar">
      <div className="nav-brand">
        <div className="brand-mark"><span>采</span></div>
        <div className="brand-copy">
          <strong>政策采集工作台</strong>
          <span>Policy Research</span>
        </div>
      </div>
      <p className="nav-kicker">页面</p>
      <nav className="nav-links" aria-label="工作台导航">
        <div className="nav-group">
          {collapsed ? <Link className={isCollectPath(pathname) ? "active" : ""} href="/" title="政策采集" aria-current={isCollectPath(pathname) ? "page" : undefined}>
            <NavIcon name="collect"/>
          </Link> : <>
            <div className={`nav-group-label ${isCollectPath(pathname) ? "active" : ""}`}>
              <NavIcon name="collect"/>
              <span>政策采集</span>
            </div>
            <div className="nav-sublinks">
              {COLLECT_NAV.map((item) => {
                const active = pathname === item.href;
                return <Link className={active ? "active" : ""} href={item.href} key={item.href} aria-current={active ? "page" : undefined}>{item.label}</Link>;
              })}
            </div>
          </>}
        </div>
        <Link className={pathname === "/policyNet" ? "active" : ""} href="/policyNet" title="政策图谱" aria-current={pathname === "/policyNet" ? "page" : undefined}>
          <NavIcon name="graph"/>
          <span>政策图谱</span>
        </Link>
        <Link className={pathname === "/siteSelection" ? "active" : ""} href="/siteSelection" title="资源选址" aria-current={pathname === "/siteSelection" ? "page" : undefined}>
          <NavIcon name="land"/>
          <span>资源选址</span>
        </Link>
      </nav>
      <button className="nav-toggle" type="button" onClick={toggle} aria-expanded={!collapsed} aria-label={collapsed ? "展开侧边栏" : "收起侧边栏"}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          {collapsed ? <path d="m9 6 6 6-6 6"/> : <path d="m15 6-6 6 6 6"/>}
        </svg>
        <span>{collapsed ? "展开" : "收起"}</span>
      </button>
    </aside>
    <button className="nav-backdrop" type="button" aria-label="收起侧边栏" onClick={toggle}/>
    <div className="app-main">{children}</div>
  </div>;
}
