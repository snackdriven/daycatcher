/**
 * Typed wrappers around the FastAPI backend. Mirrors meat_suit/api.py.
 *
 * All routes are same-origin. The signed session cookie is httpOnly and
 * rides via `credentials: "same-origin"` (set in queryClient.ts).
 */

import { apiRequest } from "./queryClient";

// ---------- shared types ----------

export type PersonaKey =
  | "smug_construct"
  | "feral_bff"
  | "stoic_mentor"
  | "quiet_guardian";

export const PERSONA_ORDER: PersonaKey[] = [
  "smug_construct",
  "feral_bff",
  "stoic_mentor",
  "quiet_guardian",
];

/** Server-provided persona row. Display text comes from the Python catalog. */
export interface PersonaRow {
  key: PersonaKey;
  display: string;
}

/**
 * Frontend-only persona presentation: letter mark for the segmented control
 * and the CSS variable that holds the persona's tint. These never leave the
 * UI — the server doesn't care about them.
 */
export interface PersonaPresentation {
  letter: string;
  tint_var: string;
  blurb: string;
}

export const PERSONA_PRESENTATION: Record<PersonaKey, PersonaPresentation> = {
  smug_construct: {
    letter: "C",
    tint_var: "--persona-ceo",
    blurb: "calm, clipped, faintly amused. directives over hand-holding.",
  },
  feral_bff: {
    letter: "F",
    tint_var: "--persona-feral",
    blurb: "loud, no caps, will roast you with love. minimal punctuation.",
  },
  stoic_mentor: {
    letter: "A",
    tint_var: "--persona-almanac",
    blurb: "spare, observational, steady cadence. very few questions.",
  },
  quiet_guardian: {
    letter: "I",
    tint_var: "--persona-innkeeper",
    blurb: "soft, attentive, low-volume kindness. hospitality cues.",
  },
};

export async function fetchPersonas(): Promise<{ personas: PersonaRow[]; default: PersonaKey }> {
  const res = await apiRequest("GET", "/api/personas");
  return res.json();
}

// ---------- /api/state ----------

export interface AppState {
  vibe: PersonaKey;
  personas: PersonaRow[];
  unread_journal: number;
  last_message_id: number;
  first_run: boolean;
}

export async function fetchState(): Promise<AppState> {
  const res = await apiRequest("GET", "/api/state");
  return res.json();
}

// ---------- /api/messages ----------

export type MessageRole = "user" | "assistant" | "system_nudge";

export interface ChatMessage {
  id: number;
  ts_utc: string;
  role: MessageRole;
  vibe: PersonaKey | null;
  content: string;
}

export async function fetchMessages(
  opts: { since_id?: number; limit?: number } = {},
): Promise<ChatMessage[]> {
  const qs = new URLSearchParams();
  if (opts.since_id !== undefined) qs.set("since_id", String(opts.since_id));
  if (opts.limit !== undefined) qs.set("limit", String(opts.limit));
  const url = `/api/messages${qs.toString() ? `?${qs}` : ""}`;
  const res = await apiRequest("GET", url);
  const data = await res.json();
  return data.messages as ChatMessage[];
}

export async function postMessage(
  content: string,
): Promise<{ user: ChatMessage; assistant: ChatMessage }> {
  const res = await apiRequest("POST", "/api/messages", { content });
  return res.json();
}

export async function resetChat(): Promise<void> {
  await apiRequest("DELETE", "/api/messages");
}

// ---------- /api/personas ----------

export async function markPersonasSeen(): Promise<void> {
  await apiRequest("POST", "/api/personas/seen");
}

// ---------- /api/vibe ----------

export async function setVibe(vibe: PersonaKey): Promise<{ vibe: PersonaKey }> {
  const res = await apiRequest("POST", "/api/vibe", { vibe });
  return res.json();
}

// ---------- /api/journal ----------

export interface JournalEntry {
  local_date: string;
  ts_utc: string;
  narrative: string;
  seen: boolean;
  kind: "daily" | "weekly";
}

export async function fetchJournal(opts: {
  kind?: "all" | "daily" | "weekly";
  limit?: number;
  before?: string;
} = {}): Promise<{ entries: JournalEntry[]; unread: number }> {
  const qs = new URLSearchParams();
  if (opts.kind) qs.set("kind", opts.kind);
  if (opts.limit !== undefined) qs.set("limit", String(opts.limit));
  if (opts.before) qs.set("before", opts.before);
  const url = `/api/journal${qs.toString() ? `?${qs}` : ""}`;
  const res = await apiRequest("GET", url);
  return res.json();
}

export async function markJournalSeen(
  local_date: string,
  kind: "daily" | "weekly" = "daily",
): Promise<{ unread: number }> {
  const qs = kind === "weekly" ? "?kind=weekly" : "";
  const res = await apiRequest(
    "POST",
    `/api/journal/${encodeURIComponent(local_date)}/seen${qs}`,
  );
  return res.json();
}

// ---------- /api/data/summary ----------

export type DataRange = "7d" | "30d" | "90d" | "all";

export type SleepStatus = "within" | "late" | "very_late" | "unknown";

export interface VibeDistRow {
  key: PersonaKey;
  display: string;
  count: number;
  pct: number;
}

export interface SleepAdherenceRow {
  date: string;
  last_msg_local: string;
  status: SleepStatus;
}

export interface HourlyDensityRow {
  hour: number;
  count: number;
}

export interface WeeklyCompareSide {
  messages: number;
  late_nights: number;
  top_vibe: PersonaKey | null;
}

export interface DataSummary {
  range: DataRange;
  kpis: {
    messages_total: number;
    messages_sparkline: number[];
    streak_days: number;
    after_11pm_pct: number;
  };
  vibe_distribution: VibeDistRow[];
  sleep_adherence: SleepAdherenceRow[];
  hourly_density: HourlyDensityRow[];
  weekly_compare: {
    this_week: WeeklyCompareSide;
    last_week: WeeklyCompareSide;
  };
}

export async function fetchDataSummary(range: DataRange): Promise<DataSummary> {
  const res = await apiRequest("GET", `/api/data/summary?range=${range}`);
  return res.json();
}

// ---------- /api/settings ----------

export interface SettingsRhythm {
  sleep_weeknight_hour: number;
  sleep_weekend_hour: number;
  sleep_nudges_enabled: boolean;
  nudges_enabled: boolean;
  timezone: string;
}

export interface Settings {
  vibe: PersonaKey;
  personas: PersonaRow[];
  rhythm: SettingsRhythm;
}

export async function fetchSettings(): Promise<Settings> {
  const res = await apiRequest("GET", "/api/settings");
  return res.json();
}

export interface SettingsPatch {
  vibe?: PersonaKey;
  sleep_weeknight_hour?: number;
  sleep_weekend_hour?: number;
  nudges_enabled?: boolean;
  sleep_nudges_enabled?: boolean;
}

export async function patchSettings(
  patch: SettingsPatch,
): Promise<Settings & { restart_required: string[] }> {
  const res = await apiRequest("PATCH", "/api/settings", patch);
  return res.json();
}

export async function snoozeSleep(): Promise<void> {
  await apiRequest("POST", "/api/sleep/snooze");
}

export async function sleepOffForever(): Promise<void> {
  await apiRequest("POST", "/api/sleep/off-forever");
}

export async function checkIn(): Promise<{ id: number; vibe: PersonaKey; content: string }> {
  const res = await apiRequest("POST", "/api/check");
  return res.json();
}

// ---------- auth ----------

export interface AuthStatus {
  has_passcode: boolean;
  authenticated: boolean;
  locked: boolean;
  locked_until: string | null;
}

/**
 * Public endpoint, no cookie required. The gate component hits this on boot
 * to decide which screen to show.
 */
export async function fetchAuthStatus(): Promise<AuthStatus> {
  const res = await fetch("/api/auth/status", { credentials: "same-origin" });
  if (!res.ok) throw new Error(`auth status ${res.status}`);
  return res.json();
}

/** Returns the parsed JSON. Throws ApiError on non-2xx with the error text. */
async function authPost(path: string, passcode: string): Promise<{ ok: true }> {
  const res = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ passcode }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ error: res.statusText }));
    const err = new Error(body.error || `${path} ${res.status}`) as Error & {
      status?: number;
    };
    err.status = res.status;
    throw err;
  }
  return res.json();
}

export async function authSetup(passcode: string): Promise<void> {
  await authPost("/api/auth/setup", passcode);
}

export async function authLogin(passcode: string): Promise<void> {
  await authPost("/api/auth/login", passcode);
}

export async function authLogout(): Promise<void> {
  await fetch("/api/auth/logout", {
    method: "POST",
    credentials: "same-origin",
  });
}
