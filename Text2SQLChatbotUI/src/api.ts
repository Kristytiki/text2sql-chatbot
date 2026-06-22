// Typed wrappers around the FastAPI backend.
const BASE = import.meta.env.VITE_API_BASE ?? "";

const KEY_STORAGE = "census_api_key";

export function getApiKey(): string {
  return localStorage.getItem(KEY_STORAGE) ?? "";
}

export function setApiKey(key: string): void {
  localStorage.setItem(KEY_STORAGE, key.trim());
}

export function clearApiKey(): void {
  localStorage.removeItem(KEY_STORAGE);
}

/** Thrown when the backend rejects the API key (HTTP 401). */
export class UnauthorizedError extends Error {}

export type SqlEvidence = {
  sql: string;
  columns: string[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
};

export type CreateSessionResp = {
  session_id: string;
  greeting: string;
};

export type SendMessageResp = {
  reply: string;
  blocked: boolean;
  block_reason: string | null;
  evidence: SqlEvidence[];
};

export type HistoryTurn = { role: "user" | "assistant"; text: string };

function authHeaders(extra?: HeadersInit): HeadersInit {
  const key = getApiKey();
  return {
    "Content-Type": "application/json",
    ...(key ? { "X-API-Key": key } : {}),
    ...(extra ?? {}),
  };
}

async function jsonFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, {
    ...init,
    headers: authHeaders(init?.headers),
  });
  if (r.status === 401) throw new UnauthorizedError("invalid or missing API key");
  if (!r.ok) throw new Error(`${r.status}: ${await r.text().catch(() => "")}`);
  return r.json() as Promise<T>;
}

export const api = {
  createSession: (user_name = "Analyst") =>
    jsonFetch<CreateSessionResp>(`${BASE}/chat/sessions`, {
      method: "POST",
      body: JSON.stringify({ user_name }),
    }),

  sendMessage: (session_id: string, message: string) =>
    jsonFetch<SendMessageResp>(`${BASE}/chat/sessions/${session_id}/messages`, {
      method: "POST",
      body: JSON.stringify({ message }),
    }),

  getHistory: (session_id: string) =>
    jsonFetch<{ session_id: string; turns: HistoryTurn[] }>(
      `${BASE}/chat/sessions/${session_id}/history`,
    ),

  deleteSession: (session_id: string) =>
    fetch(`${BASE}/chat/sessions/${session_id}`, {
      method: "DELETE",
      headers: authHeaders(),
    }),
};
