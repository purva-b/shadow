export interface Citation {
  file_id: string;
  name: string;
  source_type: string;
  date: string;
  box_url: string;
}

export interface DecisionStatus {
  decision: string;
  status: string;
  confirmed: string;
  decidedOn: string;
}

export interface WhoToAsk {
  name: string;
  role: string;
  reason: string;
  moved_note: string | null;
}

export interface Answer {
  answer_markdown: string;
  citations: Citation[];
  decision_status: DecisionStatus | null;
  who_to_ask: WhoToAsk | null;
  restricted_note: string | null;
}

export interface ActingAsOption {
  name: string;
  user_id: string;
  subtitle?: string;
  initials?: string;
}
export interface ActingAs {
  name: string;
  user_id: string;
  meta: { subtitle?: string; initials?: string };
  options: ActingAsOption[];
}

export interface Sources {
  meetings: number;
  slack: number;
  issues_prs: number;
  docs: number;
  live: boolean;
}

export interface ActivityRow {
  ts: string;
  agent: string;
  action: string;
  box_file_id: string;
  detail: string;
}

export interface DecisionRow {
  file_id: string;
  name: string;
  decision: string;
  status: string;
  owners: string;
  confirmed: string;
  box_url: string;
}

export interface QItem {
  text: string;
  answer: Answer | null;
}

export interface Turn {
  question: string;
  answer: Answer | null;   // null while loading
}
export interface Conversation {
  id: string;
  title: string;
  turns: Turn[];
}
