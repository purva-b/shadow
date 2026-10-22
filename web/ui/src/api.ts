import type {
  ActingAs, Answer, Sources, ActivityRow, DecisionRow,
} from "./types";

async function get<T>(path: string): Promise<T> {
  const r = await fetch(path, { credentials: "same-origin" });
  if (!r.ok) throw new Error(`${path} ${r.status}`);
  return r.json() as Promise<T>;
}
async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(body ?? {}),
  });
  if (!r.ok) throw new Error(`${path} ${r.status}`);
  return r.json() as Promise<T>;
}

export const api = {
  me: () => get<ActingAs>("/api/me"),
  login: (user: string) => post<ActingAs>("/api/login", { user }),
  logout: () => post<{ logged_in: boolean }>("/api/logout", {}),
  sources: () => get<Sources>("/api/sources"),
  ask: (question: string, context: string[] = []) =>
    post<Answer>("/api/ask", { question, context }),
  ingest: () => post<{ ok: boolean; steps?: unknown; error?: string }>("/api/ingest", {}),
  syncConnector: (name: string) =>
    post<{ ok: boolean; ingested?: number; store_total?: number; error?: string }>(
      "/api/sync/" + encodeURIComponent(name), {}),
  confirmTask: () =>
    post<{ ok: boolean; closed?: number; error?: string }>("/api/simulate/confirm-task", {}),
  activity: () => get<{ rows: ActivityRow[] }>("/api/activity"),
  decisions: () => get<{ rows: DecisionRow[] }>("/api/decisions"),
  embed: (fileId: string) =>
    get<{ url: string | null }>("/api/embed?file_id=" + encodeURIComponent(fileId)),
};
