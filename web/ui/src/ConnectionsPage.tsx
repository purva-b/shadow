import { useEffect, useState } from "react";
import type { ConnectorRow } from "./types";
import { api } from "./api";
import { GhostMark } from "./ui";

/**
 * Full-page /connections — where a signed-in user plugs sources into Shadow.
 * Each connector shows live status from /api/connections:
 *   • connected      → "Connected" + a Sync button
 *   • configured     → a "Connect" button that starts that connector's OAuth
 *   • not configured → setup steps (how to register the OAuth app)
 */
export function ConnectionsPage() {
  const [rows, setRows] = useState<ConnectorRow[] | null>(null);
  const [loggedIn, setLoggedIn] = useState(true);
  const [syncing, setSyncing] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [note, setNote] = useState<{ name: string; text: string } | null>(null);

  const load = () =>
    api.connections().then((r) => { setRows(r.connectors); setLoggedIn(r.logged_in); }).catch(() => {});
  useEffect(() => { load(); }, []);
  useEffect(() => { if (!loggedIn) window.location.href = "/login"; }, [loggedIn]);

  const connect = (row: ConnectorRow) => { if (row.connect_url) window.location.href = row.connect_url; };
  const sync = async (name: string) => {
    setSyncing(name); setNote(null);
    try {
      const r = await api.syncConnectorGet(name);
      if (!r.ok) {
        setNote({ name, text: r.error || "Sync failed — connect this source first." });
      } else {
        setNote({ name, text: `Synced — ${r.ingested ?? 0} new record${r.ingested === 1 ? "" : "s"}.` });
        await load();
      }
    } catch {
      setNote({ name, text: "Sync failed — please try again." });
    } finally {
      setSyncing(null);
    }
  };

  return (
    <div className="cp">
      <header className="cp-top">
        <a className="cp-back" href="/" aria-label="Back to Shadow">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="19" y1="12" x2="5" y2="12" /><polyline points="12 19 5 12 12 5" />
          </svg>
        </a>
        <GhostMark size={30} />
        <div className="cp-h"><div className="cp-t">Connect your sources</div></div>
      </header>

      <div className="cp-body">
        <p className="cp-intro">
          Shadow reads each source <b>with your permissions</b> — connect the ones your team uses,
          and every answer stays within what you're allowed to see.
        </p>

        {!rows ? (
          <div className="cp-loading">Loading connections…</div>
        ) : rows.map((r) => (
          <div className="cp-row" key={r.name}>
            <div className="cp-main">
              <span className="cp-logo">{r.emoji}</span>
              <div className="cp-info">
                <div className="cp-name">{r.label}</div>
                <div className="cp-meta">
                  {r.connected ? `Connected · ${r.indexed} indexed`
                    : r.configured ? "Ready to connect"
                    : r.indexed > 0 ? `${r.indexed} indexed · setup required`
                    : "Not configured"}
                </div>
              </div>
              <div className="cp-actions">
                {r.connected ? (
                  <>
                    <span className="cp-badge on">Connected</span>
                    <button className="cp-btn ghost" disabled={syncing === r.name}
                      onClick={() => sync(r.name)}>
                      {syncing === r.name ? "Syncing…" : "Sync"}
                    </button>
                  </>
                ) : r.name === "box" ? (
                  <span className="cp-badge on">Via sign-in</span>
                ) : r.configured ? (
                  <button className="cp-btn" onClick={() => connect(r)}>Connect</button>
                ) : (
                  <button className="cp-btn ghost"
                    onClick={() => setOpen(open === r.name ? null : r.name)}>
                    {open === r.name ? "Hide setup" : "Setup"}
                  </button>
                )}
              </div>
            </div>
            {open === r.name && r.setup_steps.length > 0 && (
              <ol className="cp-setup">
                {r.setup_steps.map((s, i) => <li key={i}>{s}</li>)}
              </ol>
            )}
            {note?.name === r.name && <div className="cp-note">{note.text}</div>}
          </div>
        ))}
      </div>
    </div>
  );
}
