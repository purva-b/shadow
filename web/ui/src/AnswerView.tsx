import { useEffect, useMemo, useRef, useState } from "react";
import type { Answer, Citation } from "./types";
import { api } from "./api";
import { Chip, Icon, ICONS, ShadowMark, initials, renderMarkdown, srcIcon } from "./ui";

const THINK_PHRASES = [
  "Shadow is casting around for answers…",
  "Shadow is lurking in the archives…",
  "Shadow is following the trail…",
  "Shadow is digging through the shade…",
  "Shadow is shedding some light…",
  "Shadow is stepping out of the dark…",
  "Shadow is peeking behind the scenes…",
  "Shadow is tracing the outline…",
  "Shadow is never far behind…",
  "Shadow is sneaking a peek at the notes…",
  "Shadow is tiptoeing through the team's history…",
  "Shadow is lengthening as it learns…",
];

function shuffled<T>(a: T[]): T[] {
  const r = [...a];
  for (let i = r.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [r[i], r[j]] = [r[j], r[i]];
  }
  return r;
}

export function Thinking({ question }: { question: string }) {
  const phrases = useMemo(() => shuffled(THINK_PHRASES), [question]);
  const [i, setI] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setI((n) => (n + 1) % phrases.length), 1600);
    return () => clearInterval(t);
  }, [phrases]);
  return (
    <div className="answer-layout"><div className="conversation">
      <div className="q-bubble">{question}</div>
      <div className="thinking-live">
        <span className="think-orb"><ShadowMark size={26} /></span>
        <div className="think-text">
          <div className="think-step" key={i}>{phrases[i]}</div>
        </div>
      </div>
    </div></div>
  );
}

export function AnswerView({ question, answer, followups = [], onAsk }:
  { question: string; answer: Answer; followups?: string[]; onAsk?: (q: string) => void }) {
  const cites = answer.citations ?? [];
  const [sel, setSel] = useState(0);
  const selectCite = (n: number) => setSel(Math.max(0, Math.min(cites.length - 1, n - 1)));

  return (
    <div className="answer-layout">
      <div className="conversation">
        <div className="q-bubble">{question}</div>
        <div className="answer-block">
          <div className="answer-by"><ShadowMark size={24} /> Shadow</div>
          <div className="answer-md">{renderMarkdown(answer.answer_markdown, selectCite)}</div>

          {answer.decision_status && (
            <div className="decision-row">
              <span className="lbl">Decision</span>
              <span className="txt">{answer.decision_status.decision}</span>
              <Chip ds={answer.decision_status} />
            </div>
          )}

          {cites.length > 0 && (
            <div className="sources">
              <h4>Where this was discussed</h4>
              {cites.map((c, i) => (
                <div key={c.file_id} className="source-row" onClick={() => setSel(i)}>
                  <span className="num">{i + 1}</span>
                  <Icon d={srcIcon(c.source_type)} sm />
                  <a className="nm" href={c.box_url} target="_blank" rel="noopener">{c.name}</a>
                  <span className="meta">{c.source_type} · {c.date}</span>
                </div>
              ))}
            </div>
          )}

          {answer.who_to_ask && (
            <div className="who-card">
              <span className="wavatar">{initials(answer.who_to_ask.name)}</span>
              <div>
                <div className="wname">{answer.who_to_ask.name}</div>
                <div className="wrole">{answer.who_to_ask.role}</div>
                <div className="wreason">{answer.who_to_ask.reason}</div>
                {answer.who_to_ask.moved_note && (
                  <div className="wmoved">{answer.who_to_ask.moved_note}</div>
                )}
              </div>
            </div>
          )}

          {answer.restricted_note && answer.citations.length > 0 && (
            <div className="restricted">
              <Icon d={ICONS.lock} sm />
              <div>
                <div className="rtitle">One source was withheld</div>
                <div className="rbody">{answer.restricted_note}</div>
              </div>
            </div>
          )}

          {followups.length > 0 && onAsk && (
            <div className="followups">
              <span className="followups-label">Ask a follow-up</span>
              <div className="followup-chips">
                {followups.map((f) => (
                  <button key={f} className="followup-chip" onClick={() => onAsk(f)}>
                    {f}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {cites.length > 0 && <Preview cite={cites[sel]} />}
    </div>
  );
}

function Preview({ cite }: { cite: Citation }) {
  const [url, setUrl] = useState<string | null | undefined>(undefined);
  const reqId = useRef(0);
  useEffect(() => {
    const id = ++reqId.current;
    setUrl(undefined);
    api.embed(cite.file_id)
      .then((r) => { if (id === reqId.current) setUrl(r.url); })
      .catch(() => { if (id === reqId.current) setUrl(null); });
  }, [cite.file_id]);

  return (
    <div className="preview">
      <div className="preview-card">
        <div className="preview-head">
          <Icon d={srcIcon(cite.source_type)} sm />
          <span className="pnm">{cite.name}</span>
          <span className="pdate">{cite.date}</span>
          <a className="open-box" href={cite.box_url} target="_blank" rel="noopener">
            Open in Box <Icon d={ICONS.ext} sm />
          </a>
        </div>
        {url === undefined && <div className="ph">Loading preview…</div>}
        {url === null && <div className="ph">Preview unavailable — open in Box.</div>}
        {url && <iframe src={url} title={cite.name} />}
      </div>
    </div>
  );
}
