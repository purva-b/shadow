import { useEffect, useMemo, useRef, useState } from "react";
import type { ActingAs, Answer, Conversation, Sources } from "./types";
import { api } from "./api";
import { Icon, ICONS, ShadowMark } from "./ui";
import { Sidebar } from "./Sidebar";
import { AnswerTurn, Thinking } from "./AnswerView";
import { MemoryView } from "./MemoryView";

const SUGGESTIONS = [
  { t: "Explain my assigned issue, PAY-482", bg: "var(--accent-tint)", fg: "var(--accent)", ic: '<circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16" y2="16"/>' },
  { t: "What did the team decide about retries?", bg: "var(--teal-tint)", fg: "var(--teal)", ic: '<rect x="3" y="3" width="18" height="18" rx="3"/><polyline points="8 12 11 15 16 9"/>' },
  { t: "How does the ledger webhook work and what did we fix?", bg: "var(--accent-tint)", fg: "var(--accent)", ic: ICONS.layers },
  { t: "Who is my onboarding buddy and where do I start?", bg: "var(--green-tint)", fg: "var(--green)", ic: ICONS.people },
  { t: "What's the plan for the checkout flash-sale timeouts?", bg: "var(--teal-tint)", fg: "var(--teal)", ic: '<rect x="2" y="5" width="15" height="14" rx="2"/><polygon points="17 9 22 6 22 18 17 15"/>' },
  { t: "What did we decide about the gateway vendor pricing contract?", bg: "var(--red-tint)", fg: "var(--red)", ic: ICONS.lock },
];

const FOLLOWUPS: { match: RegExp; chips: string[] }[] = [
  { match: /pay-482|retry|retries|idempoten/i,
    chips: ["Who should I ask about this?", "What's the current status?", "Show me the design doc"] },
  { match: /checkout|flash-sale|timeout/i,
    chips: ["Who owns checkout?", "Was this shipped?", "What about the budget?"] },
  { match: /ledger/i,
    chips: ["Who fixed the ledger webhook?", "What's PAY-501?", "Any open ledger decisions?"] },
  { match: /onboard|buddy|start/i,
    chips: ["How does this team review PRs?", "What are the standup times?"] },
  { match: /vendor|pricing|contract/i,
    chips: ["Who can brief me on it?", "What did we decide about retries?"] },
];
function followupsFor(q: string): string[] {
  const hit = FOLLOWUPS.find((f) => f.match.test(q));
  return hit ? hit.chips : ["What did the team decide about retries?", "Who should I ask about the ledger?"];
}

function tagFor(q: string): string {
  const l = q.toLowerCase();
  const m = q.match(/pay-\d+/i);
  if (m) return m[0].toUpperCase();
  if (l.includes("vendor") || l.includes("pricing") || l.includes("contract")) return "Restricted";
  if (l.includes("checkout") || l.includes("flash-sale")) return "Checkout";
  if (l.includes("ledger")) return "Ledger";
  if (l.includes("onboard") || l.includes("buddy") || l.includes("start")) return "Onboarding";
  if (l.includes("who") || l.includes("ask")) return "People";
  if (l.includes("decide") || l.includes("decision") || l.includes("retr")) return "Decision";
  if (l.includes("reorg")) return "Org";
  return "Question";
}
const trunc = (s: string) => (s.length > 48 ? s.slice(0, 48) + "…" : s);
let _cid = 0;

export default function App() {
  const [view, setView] = useState<"ask" | "memory">("ask");
  const [actingAs, setActingAs] = useState<ActingAs | null>(null);
  const [sources, setSources] = useState<Sources | null>(null);
  const [convos, setConvos] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [draft, setDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => { api.actingAs().then(setActingAs); api.sources().then(setSources).catch(() => {}); }, []);

  const active = useMemo(() => convos.find((c) => c.id === activeId) || null, [convos, activeId]);

  // auto-scroll to the newest turn
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [active?.turns.length, loading]);

  const onSwitch = async (name: string) => {
    const r = await api.setActingAs(name);
    setActingAs(r);
    setConvos([]); setActiveId(null); setView("ask");
  };

  const newConvo = () => { setActiveId(null); setView("ask"); setDraft(""); };

  // ask(): if there's an active conversation, APPEND a follow-up turn; else start one
  const ask = async (question: string) => {
    const q = question.trim();
    if (!q || !actingAs || loading) return;
    setView("ask");
    setDraft("");

    let convId = activeId;
    let context: string[] = [];
    if (!convId || !active) {
      convId = "c" + (++_cid);
      const conv: Conversation = { id: convId, title: q, turns: [{ question: q, answer: null }] };
      setConvos((cs) => [conv, ...cs]);
      setActiveId(convId);
    } else {
      context = active.turns.map((t) => t.question);  // prior questions = topic context
      setConvos((cs) => cs.map((c) =>
        c.id === convId ? { ...c, turns: [...c.turns, { question: q, answer: null }] } : c));
    }

    setLoading(true);
    try {
      const ans: Answer = await api.ask(actingAs.user_id, q, context);
      setConvos((cs) => cs.map((c) => {
        if (c.id !== convId) return c;
        const turns = [...c.turns];
        turns[turns.length - 1] = { question: q, answer: ans };
        return { ...c, turns };
      }));
    } finally {
      setLoading(false);
    }
  };

  const filtered = convos.filter((c) =>
    c.title.toLowerCase().includes(search.toLowerCase()));
  const lastQ = active?.turns[active.turns.length - 1]?.question || "";

  return (
    <main className="app">
      <Sidebar view={view} setView={setView} sources={sources} actingAs={actingAs} onSwitch={onSwitch} />

      <section className="panel questions" aria-label="Your conversations">
        <div className="q-header">
          <h2>Conversations</h2>
          <button className="btn-primary" onClick={newConvo}>
            <Icon d={ICONS.plus} sm />New
          </button>
        </div>
        <div className="q-search">
          <input type="search" placeholder="Search…"
            value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
        <div className="q-list-wrap">
          <ul className="q-list">
            {filtered.map((c) => (
              <li key={c.id}
                className={"q-item" + (activeId === c.id ? " active" : "")}
                onClick={() => { setActiveId(c.id); setView("ask"); }}>
                <div className="q-tag">{tagFor(c.title)}{c.turns.length > 1 ? ` · ${c.turns.length}` : ""}</div>
                <div className="q-text">{trunc(c.title)}</div>
              </li>
            ))}
          </ul>
        </div>
        <div className="q-footer"><Icon d={ICONS.lock} sm />Only you can see these</div>
      </section>

      <section className="panel main" aria-label="Main">
        <div className="topbar">
          <div className="topbar-title">{view === "memory" ? "Team memory" : active ? trunc(active.title) : "Ask Shadow"}</div>
          <span className="pill"><span className="dot green" />Answers within your access</span>
        </div>

        {view === "memory" ? (
          <MemoryView />
        ) : (
          <div className="view view-ask">
            <div className="ask-scroll" ref={scrollRef}>
              {!active ? (
                <div className="empty-state">
                  <ShadowMark size={64} />
                  <h1 className="empty-headline">What are you stuck on?</h1>
                  <p className="empty-sub">I've read the team's meetings, chats, issues and docs. Ask anything.</p>
                  <div className="suggestions">
                    {SUGGESTIONS.map((s) => (
                      <button key={s.t} className="suggest-card" onClick={() => ask(s.t)}>
                        <span className="suggest-tile" style={{ background: s.bg, color: s.fg }}>
                          <Icon d={s.ic} />
                        </span>
                        <span className="suggest-text">{s.t}</span>
                      </button>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="thread">
                  {active.turns.map((t, i) => (
                    t.answer
                      ? <AnswerTurn key={i} question={t.question} answer={t.answer}
                          followups={i === active.turns.length - 1 ? followupsFor(t.question) : []}
                          onAsk={ask} />
                      : <Thinking key={i} question={t.question} />
                  ))}
                </div>
              )}
            </div>

            <Composer draft={draft} setDraft={setDraft} onSend={ask} busy={loading}
              placeholder={active ? `Ask a follow-up about ${trunc(lastQ)}…` : "Ask about an issue, a decision, a doc, or who to talk to…"} />
          </div>
        )}
      </section>
    </main>
  );
}

function Composer({ draft, setDraft, onSend, busy, placeholder }: {
  draft: string; setDraft: (s: string) => void; onSend: (q: string) => void;
  busy: boolean; placeholder: string;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  return (
    <form className="composer" onSubmit={(e) => { e.preventDefault(); onSend(draft); }}>
      <div className="composer-box">
        <textarea ref={ref} rows={1} value={draft} placeholder={placeholder}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onSend(draft); }
          }} />
        <button type="submit" className="send-btn" aria-label="Send" disabled={!draft.trim() || busy}>
          <Icon d={ICONS.send} />
        </button>
      </div>
    </form>
  );
}
