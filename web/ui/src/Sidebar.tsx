import { useState } from "react";
import type { ActingAs, Sources } from "./types";
import { api } from "./api";
import { Icon, ICONS, ShadowMark, initials } from "./ui";

export function Sidebar({
  view, setView, sources, actingAs, onSwitch, onSynced,
}: {
  view: string;
  setView: (v: "ask" | "memory") => void;
  sources: Sources | null;
  actingAs: ActingAs | null;
  onSwitch: (name: string) => void;
  onSynced?: () => void;
}) {
  const [syncingName, setSyncingName] = useState<string>("");
  const [syncMsg, setSyncMsg] = useState<string>("");

  async function syncConnector(name: string) {
    setSyncingName(name);
    setSyncMsg("");
    try {
      const r = await api.syncConnector(name);
      setSyncMsg(r.ok ? `${name}: +${r.ingested ?? 0} synced` : `${name}: ${r.error || "failed"}`);
      onSynced?.();
    } catch (e) {
      setSyncMsg(`${name}: sync failed`);
    } finally {
      setSyncingName("");
    }
  }

  const connectorRow = (
    name: string, icon: string, label: string, hint: string,
  ) => (
    <li className="connector-row">
      <Icon d={icon} sm />
      <span>{label}</span>
      <em>{sources?.connectors?.[name] ?? 0}</em>
      <button
        className="sync-btn"
        onClick={() => syncConnector(name)}
        disabled={!!syncingName}
        title={hint}
      >
        {syncingName === name ? "Syncing…" : "Sync"}
      </button>
    </li>
  );

  const navItem = (id: "ask" | "memory", icon: string, label: string) => (
    <button
      className={"nav-item" + (view === id ? " active" : "")}
      aria-current={view === id ? "page" : "false"}
      onClick={() => setView(id)}
    >
      <Icon d={icon} />
      <span>{label}</span>
    </button>
  );

  const row = (icon: string, label: string, count: number | string) => (
    <li>
      <Icon d={icon} sm />
      <span>{label}</span>
      <em>{count}</em>
    </li>
  );

  return (
    <aside className="panel sidebar" aria-label="Navigation">
      <div className="logo-row">
        <ShadowMark size={34} />
        <span className="wordmark">SHADOW</span>
      </div>
      <div className="divider-soft" />

      <nav className="nav" aria-label="Primary">
        {navItem("ask", ICONS.chat, "Ask Shadow")}
        {navItem("memory", ICONS.layers, "Team memory")}
      </nav>

      <div className="shadowing">
        <div className="section-label">
          Shadowing
          {sources?.live && (
            <span className="live-dot"><span className="dot" />Live</span>
          )}
        </div>
        <ul className="source-list">
          {row(ICONS.meeting, "Meetings", sources?.meetings ?? "—")}
          {row(ICONS.slack, "Slack channels", sources?.slack ?? "—")}
          {row(ICONS.issue, "Issues and PRs", sources?.issues_prs ?? "—")}
          {row(ICONS.doc, "Docs in Box", sources?.docs ?? "—")}
          {connectorRow("github", ICONS.github, "GitHub (PRs/issues)",
            "Pull PRs/issues from the configured repo (needs GITHUB_TOKEN + GITHUB_REPO)")}
          {connectorRow("slack", ICONS.slack, "Slack (live)",
            "Pull channel threads from Slack (needs SLACK_BOT_TOKEN)")}
          {connectorRow("confluence", ICONS.doc, "Confluence (wiki)",
            "Pull pages from Confluence (needs CONFLUENCE_BASE_URL + API token)")}
          {connectorRow("calendar", ICONS.meeting, "Calendar (events)",
            "Pull events from Google Calendar (needs GCAL_ACCESS_TOKEN)")}
          {syncMsg && <li className="sync-msg">{syncMsg}</li>}
        </ul>
      </div>

      <div className="sidebar-bottom">
        <div className="divider-soft" />
        <label className="acting-label" htmlFor="acting-as">Acting as</label>
        <select
          id="acting-as"
          className="acting-select"
          value={actingAs?.name ?? ""}
          onChange={(e) => onSwitch(e.target.value)}
        >
          {actingAs?.options.map((o) => (
            <option key={o.name} value={o.name}>{o.name}</option>
          ))}
        </select>
        <div className="user-chip">
          <span className="avatar">
            {actingAs?.meta.initials ?? (actingAs ? initials(actingAs.name) : "")}
          </span>
          <div>
            <div className="user-name">{actingAs?.name}</div>
            <div className="user-sub">{actingAs?.meta.subtitle}</div>
          </div>
        </div>
      </div>
    </aside>
  );
}
