import { useEffect, useRef, useState } from "react";
import type { ActingAs, Answer } from "./types";
import { api } from "./api";
import { AnswerTurn } from "./AnswerView";
import { GhostMark, Icon, ICONS } from "./ui";

/* ───────────────────────── ghost pun copy ───────────────────────── */
const GREETING = "👻 I'm Shadow — here to un-haunt you.";
const GREETING_SUB =
  "I've been lurking in your team's meetings, chats, issues & docs. Ask me anything.";
const THINKING = [
  "Conjuring…",
  "Rattling some chains…",
  "Raising the archives…",
  "Peering beyond the veil…",
  "Floating through the docs…",
];
const SUGGESTIONS = [
  { emoji: "🔁", label: "Let's resurrect PAY-482", q: "What was the retry double-charge fix?" },
  { emoji: "👥", label: "Who's the spirit behind checkout?", q: "Who owns checkout, and who should I ask?" },
  { emoji: "🔒", label: "Dare to peek at the vendor crypt?", q: "What did we decide about the gateway vendor pricing?" },
];

const FOLLOWUPS: { match: RegExp; chips: string[] }[] = [
  { match: /pay-482|retry|retries|idempoten|double/i,
    chips: ["Who haunts this code?", "What's the current status?", "Show me the design doc"] },
  { match: /checkout|flash-sale|timeout/i, chips: ["Who owns checkout?", "Did it ship?"] },
  { match: /vendor|pricing|contract|gateway/i, chips: ["Who can let me in?", "What did we decide on retries?"] },
];
function followupsFor(q: string): string[] {
  return FOLLOWUPS.find((f) => f.match.test(q))?.chips ?? ["Dig deeper", "Who should I ask?"];
}

interface Turn { question: string; answer: Answer | null; streamed?: string }
interface Conversation { id: string; title: string; turns: Turn[] }
const trunc = (s: string, n = 40) => (s.length > n ? s.slice(0, n) + "…" : s);
let _cid = 0;

// the popup shows one of two "pages": the history list, or a single chat
type Page = "history" | "chat";

export function ChatWidget() {
  const [open, setOpen] = useState(false);
  const [me, setMe] = useState<ActingAs | null>(null);
  const [convos, setConvos] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [page, setPage] = useState<Page>("history");
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [think, setThink] = useState(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // identity from the login session (Box OAuth in prod; dev login as fallback)
  useEffect(() => { api.me().then(setMe).catch(() => {}); }, []);

  // login/logout are full PAGE redirects now (not in-popup)
  const signIn = () => { window.location.href = "/login"; };
  const signOut = async () => {
    await api.logout().catch(() => {});
    window.location.href = "/login";
  };

  useEffect(() => {
    if (!busy) return;
    const t = setInterval(() => setThink((n) => (n + 1) % THINKING.length), 1400);
    return () => clearInterval(t);
  }, [busy]);

  const active = convos.find((c) => c.id === activeId) || null;

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [active?.turns.length, busy, page]);
  useEffect(() => { if (open && page === "chat") setTimeout(() => inputRef.current?.focus(), 250); }, [open, page]);

  // start a brand-new chat (opens the chat page on an empty thread)
  const newChat = () => { setActiveId(null); setPage("chat"); setDraft(""); };
  const openChat = (id: string) => { setActiveId(id); setPage("chat"); };
  const goHistory = () => setPage("history");

  const streamIn = (full: string, convId: string) =>
    new Promise<void>((resolve) => {
      const words = full.split(/(\s+)/);
      let i = 0;
      const tick = () => {
        i += 2;
        const partial = words.slice(0, i).join("");
        setConvos((cs) => cs.map((c) => {
          if (c.id !== convId) return c;
          const turns = [...c.turns];
          const last = turns[turns.length - 1];
          if (last) turns[turns.length - 1] = { ...last, streamed: partial };
          return { ...c, turns };
        }));
        scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
        if (i < words.length) setTimeout(tick, 22); else resolve();
      };
      tick();
    });

  const ask = async (question: string) => {
    const q = question.trim();
    if (!q || busy || !me?.logged_in) return;
    setDraft("");

    let convId = activeId;
    let context: string[] = [];
    if (!convId || !active) {
      convId = "c" + (++_cid);
      setConvos((cs) => [{ id: convId!, title: q, turns: [{ question: q, answer: null }] }, ...cs]);
      setActiveId(convId);
      setPage("chat");
    } else {
      context = active.turns.map((t) => t.question);
      setConvos((cs) => cs.map((c) =>
        c.id === convId ? { ...c, turns: [...c.turns, { question: q, answer: null }] } : c));
    }

    setBusy(true);
    try {
      const ans = await api.ask(q, context);
      await streamIn(ans.answer_markdown, convId);
      setConvos((cs) => cs.map((c) => {
        if (c.id !== convId) return c;
        const turns = [...c.turns];
        turns[turns.length - 1] = { question: turns[turns.length - 1].question, answer: ans };
        return { ...c, turns };
      }));
    } catch {
      setConvos((cs) => cs.map((c) => {
        if (c.id !== convId) return c;
        const turns = [...c.turns];
        turns[turns.length - 1] = {
          question: turns[turns.length - 1].question,
          answer: { answer_markdown: "👻 That trail's gone cold — I couldn't reach the archives. Try again in a moment.",
            citations: [], decision_status: null, who_to_ask: null, restricted_note: null },
        };
        return { ...c, turns };
      }));
    } finally {
      setBusy(false);
    }
  };

  const onHistory = page === "history";

  return (
    <div className="sw-root">
      <button className={"sw-launcher" + (open ? " hidden" : "")} aria-label="Open Shadow" onClick={() => setOpen(true)}>
        <GhostMark size={38} />
        <span className="sw-ping" />
      </button>

      {open && (
        <section className="sw-pop" role="dialog" aria-label="Shadow chat">
          <header className="sw-head">
            <span className="sw-head-glow" />
            {me?.logged_in && !onHistory ? (
              <button className="sw-back" aria-label="Back to your chats" onClick={goHistory}>
                <Icon d={ICONS.back} sm />
              </button>
            ) : (
              <GhostMark size={44} think={busy} />
            )}
            <div className="sw-id">
              <div className="sw-name">
                {!me?.logged_in ? "Shadow" : onHistory ? "Shadow" : trunc(active?.title || "New chat", 26)}
              </div>
              <div className="sw-sub">
                <span className="sw-dot" />
                {!me?.logged_in ? "Please sign in" : busy ? THINKING[think] : "Answers within your access"}
              </div>
            </div>
            {me?.logged_in && onHistory ? (
              <>
                <a className="sw-icon-btn" aria-label="Connections" title="Connect sources" href="/connections">
                  <Icon d={ICONS.plug} sm />
                </a>
                <button className="sw-icon-btn" aria-label="Sign out" title="Sign out" onClick={signOut}>
                  <Icon d={ICONS.logout} sm />
                </button>
              </>
            ) : null}
            <button className="sw-x" aria-label="Minimize" onClick={() => setOpen(false)}>
              <Icon d={ICONS.minimize} sm />
            </button>
          </header>

          {!me?.logged_in ? (
            /* ── LOGIN PAGE ── */
            <div className="sw-body sw-login">
              <GhostMark size={66} />
              <div className="sw-greet">{GREETING}</div>
              <div className="sw-greet-sub">
                Sign in to ask across your team's memory — you'll only ever see what you're allowed to.
              </div>
              <button className="sw-signin" onClick={signIn}>
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                  strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <rect x="3" y="7" width="18" height="12" rx="2" /><path d="M3 10h18" />
                </svg>
                {me?.auth_mode === "oauth" ? "Sign in with Box" : "Sign in (dev)"}
              </button>
              <div className="sw-login-foot">
                🔒 Your identity comes from Box — Shadow never sees your password.
              </div>
            </div>
          ) : (
          <div className="sw-body" ref={scrollRef}>
            {onHistory ? (
              /* ── HISTORY PAGE ── */
              <div className="sw-page-history">
                <div className="sw-welcome">
                  <GhostMark size={54} />
                  <div className="sw-greet">{GREETING}</div>
                  <div className="sw-greet-sub">{GREETING_SUB}</div>
                </div>
                {convos.length === 0 ? (
                  <>
                    <div className="sw-cards">
                      {SUGGESTIONS.map((s) => (
                        <button key={s.q} className="sw-card" onClick={() => ask(s.q)}>
                          <span className="sw-card-tile">{s.emoji}</span>
                          <span className="sw-card-label">{s.label}</span>
                        </button>
                      ))}
                    </div>
                    <div className="sw-empty-hint">Your chats will show up here.</div>
                  </>
                ) : (
                  <div className="sw-hlist">
                    <div className="sw-hlist-h">Your chats</div>
                    {convos.map((c) => (
                      <button key={c.id} className="sw-hrow" onClick={() => openChat(c.id)}>
                        <span className="sw-hrow-ic"><Icon d={ICONS.chat} sm /></span>
                        <span className="sw-hrow-body">
                          <span className="sw-hrow-title">{trunc(c.title)}</span>
                          <span className="sw-hrow-meta">
                            {c.turns.length} message{c.turns.length > 1 ? "s" : ""}
                          </span>
                        </span>
                        <span className="sw-hrow-go"><Icon d={ICONS.chevron} sm /></span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            ) : (
              /* ── CHAT PAGE ── */
              active ? (
                <div className="sw-thread">
                  {active.turns.map((t, i) => {
                    if (t.answer) {
                      return (
                        <AnswerTurn key={i} question={t.question} answer={t.answer}
                          followups={i === active.turns.length - 1 ? followupsFor(t.question) : []}
                          onAsk={ask} />
                      );
                    }
                    return (
                      <div className="sw-turn" key={i}>
                        <div className="sw-msg sw-me">{t.question}</div>
                        {t.streamed ? (
                          <div className="sw-msg sw-them sw-streaming">{t.streamed}<span className="sw-caretblink" /></div>
                        ) : (
                          <div className="sw-typing"><i /><i /><i /></div>
                        )}
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="sw-newchat">
                  <GhostMark size={48} />
                  <div className="sw-newchat-t">Ask me anything</div>
                  <div className="sw-cards">
                    {SUGGESTIONS.map((s) => (
                      <button key={s.q} className="sw-card" onClick={() => ask(s.q)}>
                        <span className="sw-card-tile">{s.emoji}</span>
                        <span className="sw-card-label">{s.label}</span>
                      </button>
                    ))}
                  </div>
                </div>
              )
            )}
          </div>
          )}

          {/* composer only on the chat page, when logged in */}
          {me?.logged_in && !onHistory && (
            <form className="sw-foot" onSubmit={(e) => { e.preventDefault(); ask(draft); }}>
              <input ref={inputRef} value={draft} placeholder="Ask about an issue, a decision, a doc…"
                onChange={(e) => setDraft(e.target.value)} />
              <button type="submit" className="sw-send" aria-label="Summon" disabled={!draft.trim() || busy}>
                <Icon d={ICONS.send} />
              </button>
            </form>
          )}

          {/* bottom-right new-chat FAB — shown on the history page when logged in */}
          {me?.logged_in && onHistory && (
            <button className="sw-fab" aria-label="New chat" title="New chat" onClick={newChat}>
              <Icon d={ICONS.plus} />
            </button>
          )}
        </section>
      )}
    </div>
  );
}
