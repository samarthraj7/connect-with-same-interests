import AsyncStorage from "@react-native-async-storage/async-storage";
import Constants from "expo-constants";
import { Platform } from "react-native";

const TOKEN_KEY = "cd_token";
const USER_KEY = "cd_user";

/**
 * WHERE TO UPDATE THE API URL (physical phone / Expo Go):
 * 1) This file — DEV_API_URL below (fastest; reload the app after changing)
 * 2) mobile/.env — EXPO_PUBLIC_API_URL=http://YOUR_MAC_LAN_IP:8000
 *    then restart Expo: cd mobile && npx expo start -c
 *
 * Find your Mac IP:  ipconfig getifaddr en0
 * API must bind all interfaces:  uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
 */
const DEV_API_URL = ""; // e.g. "http://192.168.1.10:8000" for a physical device

function defaultBaseUrl(): string {
  // 1) Explicit override for physical devices (edit DEV_API_URL above when Wi‑Fi IP changes)
  if (DEV_API_URL) return DEV_API_URL.replace(/\/$/, "");

  // 2) Expo env (requires restart of Metro after editing mobile/.env)
  const fromEnv = process.env.EXPO_PUBLIC_API_URL;
  if (fromEnv) return fromEnv.replace(/\/$/, "");

  // 3) Same host Expo Metro is using (often wrong / stale on campus Wi‑Fi)
  const hostUri = Constants.expoConfig?.hostUri;
  const host = hostUri?.split(":")[0];
  if (host && host !== "127.0.0.1" && host !== "localhost") {
    return `http://${host}:8000`;
  }
  if (Platform.OS === "android") return "http://10.0.2.2:8000";
  return "http://127.0.0.1:8000";
}

export const API_BASE = defaultBaseUrl();
console.log("[api] API_BASE =", API_BASE, "| env=", process.env.EXPO_PUBLIC_API_URL);

export type UserPublic = {
  id: string;
  email: string;
  tokens: number;
  profile: Record<string, any>;
  profile_source?: string;
  research_status?: string;
  profile_refinement?: { known_gaps?: string[]; last_from?: string };
  interaction_count?: number;
  settings?: Record<string, any>;
  connections_count?: number;
  pending_facts?: any[];
  handle_verification?: Record<string, any>;
};

async function authHeaders(): Promise<Record<string, string>> {
  const token = await AsyncStorage.getItem(TOKEN_KEY);
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { ...(await authHeaders()), ...(init?.headers || {}) },
  });
  const text = await res.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text };
  }
  if (!res.ok) {
    const detail = data?.detail || data?.error || res.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data as T;
}

export async function saveSession(token: string, user: UserPublic) {
  await AsyncStorage.setItem(TOKEN_KEY, token);
  await AsyncStorage.setItem(USER_KEY, JSON.stringify(user));
}

export async function clearSession() {
  await AsyncStorage.multiRemove([TOKEN_KEY, USER_KEY]);
}

export async function getCachedUser(): Promise<UserPublic | null> {
  const raw = await AsyncStorage.getItem(USER_KEY);
  return raw ? JSON.parse(raw) : null;
}

export async function getToken(): Promise<string | null> {
  return AsyncStorage.getItem(TOKEN_KEY);
}

export const api = {
  signup: (body: Record<string, unknown>) =>
    request<{ token: string; user: UserPublic; self_research?: any }>("/auth/signup", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  login: (email: string, password: string) =>
    request<{ token: string; user: UserPublic }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  me: () => request<UserPublic>("/me"),
  updateProfile: (body: Record<string, unknown>) =>
    request<UserPublic>("/me/profile", { method: "PATCH", body: JSON.stringify(body) }),
  researchMe: (body: Record<string, unknown> = {}) =>
    request<{
      status: string;
      user: UserPublic;
      needs_rating?: boolean;
      draft_id?: string | null;
      summary?: Record<string, any>;
      name?: string;
      company?: string | null;
    }>("/me/research", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  candidates: (name: string) =>
    request<{ candidates: any[]; status: string }>("/candidates", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  research: (body: Record<string, unknown>) =>
    request<any>("/research", { method: "POST", body: JSON.stringify(body) }),
  researchDraft: (draftId: string) => request<any>(`/research/drafts/${encodeURIComponent(draftId)}`),
  researchFeedback: (body: {
    draft_id: string;
    rating: "good" | "bad";
    wrong_notes?: string;
    wrong_categories?: string[];
  }) =>
    request<any>("/research/feedback", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  people: () => request<{ people: any[] }>("/people"),
  person: (name: string, company?: string | null) => {
    const q = company ? `?company=${encodeURIComponent(company)}` : "";
    return request<any>(`/people/${encodeURIComponent(name)}${q}`);
  },
  addNote: (name: string, note: string, company?: string | null) => {
    const q = company ? `?company=${encodeURIComponent(company)}` : "";
    return request(`/people/${encodeURIComponent(name)}/interactions${q}`, {
      method: "POST",
      body: JSON.stringify({ type: "note", note }),
    });
  },
  verifyHandles: (body: Record<string, unknown>) =>
    request<{ status: string; results: Record<string, any> }>("/verify/handles", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  uploadConnections: (body: { csv: string; filename?: string }) =>
    request<{ ok: boolean; imported: number }>("/me/connections", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  connections: () => request<{ count: number; sample: any[] }>("/me/connections"),
  updateSettings: (body: Record<string, unknown>) =>
    request<UserPublic>("/me/settings", { method: "PATCH", body: JSON.stringify(body) }),
  refreshPerson: (name: string, company?: string | null) => {
    const q = company ? `?company=${encodeURIComponent(company)}` : "";
    return request<any>(`/people/${encodeURIComponent(name)}/refresh${q}`, { method: "POST" });
  },
  addPendingFact: (body: Record<string, unknown>) =>
    request("/me/pending-facts", { method: "POST", body: JSON.stringify(body) }),
  updatePendingFact: (id: string, body: Record<string, unknown>) =>
    request(`/me/pending-facts/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  privateJournal: () => request<{ visibility: string; entries: any[] }>("/me/private/journal"),
  addPrivateJournal: (body: { body: string; entry_type?: string; tags?: string[] }) =>
    request("/me/private/journal", { method: "POST", body: JSON.stringify(body) }),
  deletePrivateJournal: (id: string) =>
    request(`/me/private/journal/${id}`, { method: "DELETE" }),
  publicCandidates: (body: Record<string, unknown>) =>
    fetch(`${API_BASE}/public/candidates`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(async (res) => {
      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail || data?.error || "Failed");
      return data as { candidates: any[]; status: string };
    }),
  sendOtp: (body: { channel: "email" | "phone"; destination?: string }) =>
    request<{ status: string; debug_code?: string; destination_hint?: string }>("/auth/otp/send", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  verifyOtp: (body: { channel: "email" | "phone"; code: string }) =>
    request<{ ok: boolean; user: UserPublic }>("/auth/otp/verify", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  calendarOAuthUrl: (redirectUri: string) =>
    request<{ status: string; url?: string }>(
      `/calendar/oauth-url?redirect_uri=${encodeURIComponent(redirectUri)}`
    ),
  calendarSyncPrep: () => request<any>("/calendar/sync-prep", { method: "POST" }),
  calendarPrepQueue: () => request<{ queue: any[] }>("/calendar/prep-queue"),
};
