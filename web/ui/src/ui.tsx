import type { ReactNode } from "react";
import type { DecisionStatus } from "./types";

export function ShadowMark({ size = 34 }: { size?: number }) {
  // Geometric "knowledge graph" mark: three source nodes linked into one hub —
  // deliberately distinct from any ghost/assistant silhouette.
  return (
    <svg width={size} height={size} viewBox="0 0 40 40" aria-hidden="true">
      <defs>
        <linearGradient id="shadowG" x1="0" y1="0" x2="40" y2="40">
          <stop offset="0" stopColor="#4F46E5" />
          <stop offset="1" stopColor="#F2683C" />
        </linearGradient>
      </defs>
      <g stroke="url(#shadowG)" strokeWidth="2" fill="none" strokeLinecap="round">
        <line x1="20" y1="20" x2="8" y2="9" />
        <line x1="20" y1="20" x2="33" y2="12" />
        <line x1="20" y1="20" x2="14" y2="33" />
      </g>
      <g fill="url(#shadowG)">
        <circle cx="20" cy="20" r="5.2" />
        <circle cx="8" cy="9" r="3" />
        <circle cx="33" cy="12" r="3" />
        <circle cx="14" cy="33" r="3" />
      </g>
    </svg>
  );
}

/**
 * The Shadow ghost mark — a rounded white ghost with a wavy hem and two
 * eye-slits, dark outline, wrapped in a spinning rainbow halo. Pure SVG so it
 * scales from a 20px favicon to a 56px launcher. The halo spins and the ghost
 * blinks via CSS classes (.gm-halo / .gm-eye) defined in app.css; both honour
 * prefers-reduced-motion. `glow` renders the halo (used in the launcher and the
 * header); omit it for a flat inline mark (e.g. a message avatar).
 */
export function GhostMark({ size = 34, glow = true, think = false }:
  { size?: number; glow?: boolean; think?: boolean }) {
  const body = (
    <svg width={size} height={size} viewBox="0 0 100 100" aria-hidden="true"
      className={"gm-ghost" + (think ? " think" : "")}>
      <path
        d="M22 80 Q31 72 40 80 T58 80 T78 80 L78 46 Q78 20 50 20 Q22 20 22 46 Z"
        fill="#fff" stroke="#0E1120" strokeWidth="5" strokeLinejoin="round" />
      <g fill="#0E1120">
        <rect className="gm-eye" x="39" y="43" width="6.5" height="15" rx="3.2" />
        <rect className="gm-eye" x="54" y="43" width="6.5" height="15" rx="3.2" />
      </g>
    </svg>
  );
  if (!glow) return body;
  return (
    <span className="gm-stage" style={{ width: size, height: size }}>
      <span className="gm-halo" />
      <span className="gm-wrap">{body}</span>
    </span>
  );
}

export function Icon({ d, sm }: { d: string; sm?: boolean }) {
  return (
    <svg className={"icon" + (sm ? " sm" : "")} viewBox="0 0 24 24" aria-hidden="true"
      dangerouslySetInnerHTML={{ __html: d }} />
  );
}

export const ICONS = {
  chat: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  layers: '<polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/>',
  meeting: '<rect x="2" y="4" width="20" height="14" rx="2"/><path d="M8 20h8"/>',
  slack: '<path d="M4 4h16v12H7l-3 3z"/>',
  issue: '<circle cx="12" cy="12" r="9"/><path d="M12 8v4l3 2"/>',
  pr: '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M6 9v6"/><path d="M18 9a6 6 0 0 1-6 6"/>',
  doc: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>',
  org: '<circle cx="12" cy="6" r="3"/><path d="M6 21v-2a4 4 0 0 1 4-4h4a4 4 0 0 1 4 4v2"/>',
  github: '<path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.9a3.4 3.4 0 0 0-1-2.6c3-.3 6-1.5 6-6.6a5 5 0 0 0-1.4-3.5 4.6 4.6 0 0 0-.1-3.5s-1.1-.3-3.5 1.3a12 12 0 0 0-6 0C6.1 1.6 5 1.9 5 1.9a4.6 4.6 0 0 0-.1 3.5A5 5 0 0 0 3.5 9c0 5 3 6.3 6 6.6a3.4 3.4 0 0 0-1 2.6V22"/>',
  lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
  plus: '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
  minimize: '<line x1="6" y1="12" x2="18" y2="12"/>',
  back: '<line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/>',
  chevron: '<polyline points="9 18 15 12 9 6"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/>',
  plug: '<path d="M9 2v6"/><path d="M15 2v6"/><path d="M6 8h12v3a6 6 0 0 1-12 0z"/><path d="M12 17v5"/>',
  send: '<line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/>',
  people: '<circle cx="9" cy="8" r="3"/><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6"/><circle cx="17" cy="8" r="2.5"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 3h8a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  ext: '<path d="M14 3h7v7"/><path d="M10 14 21 3"/><path d="M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5"/>',
};

export function srcIcon(t: string): string {
  return (ICONS as Record<string, string>)[t] || ICONS.doc;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export function fmtDate(d?: string): string {
  if (!d) return "";
  const [, m, day] = d.split("-");
  return `${MONTHS[+m - 1]} ${+day}`;
}

export function initials(name: string): string {
  return name.split(" ").map((x) => x[0]).join("");
}

export function Chip({ ds }: { ds: DecisionStatus }) {
  if (ds.status === "paused")
    return <span className="chip paused">Paused · {fmtDate(ds.decidedOn)}</span>;
  if (ds.status === "decided" && ds.confirmed === "pending")
    return <span className="chip pending">Decided · awaiting confirmation</span>;
  if (ds.status === "decided")
    return <span className="chip decided">Decided · {fmtDate(ds.decidedOn)}</span>;
  return <span className="chip paused">{ds.status}</span>;
}

// minimal markdown -> React nodes: **bold**, [n] citation chips, - bullets, paragraphs
export function renderMarkdown(md: string, onCite: (n: number) => void): ReactNode {
  const lines = md.split("\n");
  const blocks: ReactNode[] = [];
  let ul: ReactNode[] = [];
  const flush = () => {
    if (ul.length) { blocks.push(<ul key={"ul" + blocks.length}>{ul}</ul>); ul = []; }
  };
  lines.forEach((ln, i) => {
    if (/^\s*[-*]\s+/.test(ln)) {
      ul.push(<li key={i}>{inline(ln.replace(/^\s*[-*]\s+/, ""), onCite)}</li>);
    } else {
      flush();
      if (ln.trim()) blocks.push(<p key={i}>{inline(ln, onCite)}</p>);
    }
  });
  flush();
  return blocks;
}

function inline(text: string, onCite: (n: number) => void): ReactNode[] {
  // split on **bold** and [n]
  const out: ReactNode[] = [];
  const re = /(\*\*.+?\*\*|\[\d+\])/g;
  let last = 0; let m: RegExpExecArray | null; let k = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("**")) out.push(<b key={k++}>{tok.slice(2, -2)}</b>);
    else {
      const n = +tok.slice(1, -1);
      out.push(
        <span key={k++} className="cite" onClick={() => onCite(n)}>[{n}]</span>
      );
    }
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function relTime(ts: string): string {
  const s = Math.max(0, Date.now() / 1000 - parseFloat(ts));
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}
