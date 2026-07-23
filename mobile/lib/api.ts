import AsyncStorage from "@react-native-async-storage/async-storage";
import Constants from "expo-constants";
import { Platform } from "react-native";

const TOKEN_KEY = "cd_token";
const USER_KEY = "cd_user";

/*
 * WHERE TO SET THE API URL
 * Laptop / simulator / web: leave DEV_API_URL empty and do not set
 * EXPO_PUBLIC_API_URL — uses http://127.0.0.1:8000 automatically.
 * Physical phone (Expo Go): set mobile/.env to your Mac LAN IP, then npx expo start -c
 * API: uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
 */
const DEV_API_URL = ""; // leave empty for laptop; only override for physical-device testing

function isLocalDevHost(): boolean {
  // Web, iOS Simulator, Android Emulator — never need a LAN IP
  if (Platform.OS === "web") return true;
  // Constants.isDevice === false → simulator/emulator
  if (Constants.isDevice === false) return true;
  return false;
}

function defaultBaseUrl(): string {
  // 1) Hard override (physical device testing only)
  if (DEV_API_URL) return DEV_API_URL.replace(/\/$/, "");

  // 2) Explicit env (physical phone). Prefer leaving unset on laptop.
  const fromEnv = process.env.EXPO_PUBLIC_API_URL;
  if (fromEnv && !isLocalDevHost()) return fromEnv.replace(/\/$/, "");

  // 3) Laptop / simulator / web → localhost (no Wi‑Fi IP churn)
  if (isLocalDevHost()) {
    if (Platform.OS === "android") return "http://10.0.2.2:8000";
    return "http://127.0.0.1:8000";
  }

  // 4) Physical device: Expo Metro host IP on :8000
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
  signupOtpSend: (email: string) =>
    request<{ status: string; debug_code?: string; destination_hint?: string; expires_in?: number }>(
      "/auth/signup/otp/send",
      { method: "POST", body: JSON.stringify({ email }) },
    ),
  signupOtpVerify: (email: string, code: string) =>
    request<{
      status: string;
      email_verified_token?: string;
      email?: string;
      verified?: boolean;
    }>("/auth/signup/otp/verify", {
      method: "POST",
      body: JSON.stringify({ email, code }),
    }),
  publicStats: () =>
    request<{
      user_count: number;
      user_count_display: string;
      reviews: Array<{
        id: string;
        quote: string;
        name: string;
        role: string;
        placeholder?: boolean;
      }>;
      icp: { headline: string; body: string; segments: string[] };
    }>("/public/stats"),
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
  researchMeStart: (body: Record<string, unknown> = {}) =>
    request<{ status: string; job_id: string }>("/me/research/start", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  researchMeJob: (jobId: string) =>
    request<{
      status: string;
      stage?: string;
      progress?: number;
      message?: string;
      error?: string | null;
      result?: {
        status: string;
        needs_rating?: boolean;
        draft_id?: string | null;
        summary?: Record<string, any>;
        name?: string;
        company?: string | null;
        user?: UserPublic;
      } | null;
    }>(`/me/research/jobs/${encodeURIComponent(jobId)}`),
  candidates: (body: {
    name: string;
    company?: string | null;
    university?: string | null;
    linkedin_url?: string | null;
  } | string) =>
    request<{
      candidates: any[];
      exact?: any[];
      probable?: any[];
      match_mode?: "exact" | "probable_only" | "none";
      message?: string;
      status: string;
      warning?: string;
    }>("/candidates", {
      method: "POST",
      body: JSON.stringify(typeof body === "string" ? { name: body } : body),
    }),
  publicCandidates: (body: {
    name: string;
    company?: string | null;
    university?: string | null;
    linkedin_url?: string | null;
  }) =>
    request<{
      candidates: any[];
      exact?: any[];
      probable?: any[];
      match_mode?: "exact" | "probable_only" | "none";
      message?: string;
      status: string;
      warning?: string;
    }>("/public/candidates", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  publicCandidatesStart: (body: {
    name: string;
    company?: string | null;
    university?: string | null;
    linkedin_url?: string | null;
  }) =>
    request<{ status: string; job_id: string }>("/public/candidates/start", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  publicCandidatesJob: (jobId: string) =>
    request<{
      status: string;
      stage?: string;
      progress?: number;
      message?: string;
      error?: string | null;
      result?: {
        candidates?: any[];
        exact?: any[];
        probable?: any[];
        match_mode?: string;
        message?: string;
        status?: string;
        partial?: boolean;
        warning?: string;
      } | null;
    }>(`/public/candidates/jobs/${encodeURIComponent(jobId)}`),
  candidatesStart: (body: {
    name: string;
    company?: string | null;
    university?: string | null;
    linkedin_url?: string | null;
  }) =>
    request<{ status: string; job_id: string }>("/candidates/start", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  candidatesJob: (jobId: string) =>
    request<{
      status: string;
      stage?: string;
      progress?: number;
      message?: string;
      error?: string | null;
      result?: {
        candidates?: any[];
        exact?: any[];
        probable?: any[];
        match_mode?: string;
        message?: string;
        status?: string;
        partial?: boolean;
        warning?: string;
      } | null;
    }>(`/candidates/jobs/${encodeURIComponent(jobId)}`),
  publicResearchStart: (body: {
    name: string;
    company?: string | null;
    university?: string | null;
    place?: string | null;
    linkedin_url?: string | null;
    force_refresh?: boolean;
  }) =>
    request<{ status: string; job_id: string }>("/public/research/start", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  publicResearchJob: (jobId: string) =>
    request<{
      status: string;
      stage?: string;
      progress?: number;
      message?: string;
      error?: string | null;
      result?: {
        status: string;
        draft_id?: string | null;
        needs_rating?: boolean;
        summary?: Record<string, any>;
        name?: string;
        company?: string | null;
        university?: string | null;
        place?: string | null;
        linkedin_url?: string | null;
      } | null;
    }>(`/public/research/jobs/${encodeURIComponent(jobId)}`),
  research: (body: Record<string, unknown>) =>
    request<any>("/research", { method: "POST", body: JSON.stringify(body) }),
  researchDraft: (draftId: string) => request<any>(`/research/drafts/${encodeURIComponent(draftId)}`),
  researchFeedback: (body: {
    draft_id: string;
    rating: "good" | "bad";
    wrong_notes?: string;
    wrong_categories?: string[];
    auto_retry?: boolean;
  }) =>
    request<any>("/research/feedback", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  publicResearchFeedback: (body: {
    draft_id: string;
    rating?: "bad";
    wrong_notes?: string;
    wrong_categories?: string[];
    auto_retry?: boolean;
  }) =>
    request<{
      status: string;
      rating?: string;
      retried?: boolean;
      job_id?: string;
      message?: string;
    }>("/public/research/feedback", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  people: () => request<{ people: any[] }>("/people"),
  person: (name: string, company?: string | null) => {
    const q = company ? `?company=${encodeURIComponent(company)}` : "";
    return request<any>(`/people/${encodeURIComponent(name)}${q}`);
  },
  personChat: (
    name: string,
    body: {
      question: string;
      company?: string | null;
      draft_id?: string | null;
      history?: { role: string; content: string }[];
    },
  ) =>
    request<{ status: string; answer: string; name?: string; company?: string | null }>(
      `/people/${encodeURIComponent(name)}/chat`,
      { method: "POST", body: JSON.stringify(body) },
    ),
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
    request<{ status: string; url?: string; reason?: string }>(
      `/calendar/oauth-url?redirect_uri=${encodeURIComponent(redirectUri)}`,
    ),
  calendarOAuth: (body: { code: string; redirect_uri: string }) =>
    request<{ ok: boolean }>("/calendar/oauth", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  calendarSyncPrep: () => request<any>("/calendar/sync-prep", { method: "POST" }),
  calendarPrepQueue: () => request<{ queue: any[] }>("/calendar/prep-queue"),
  identityResolve: (body: {
    linkedin_url: string;
    github_username?: string;
    github_url?: string;
    name?: string;
    company?: string;
    location?: string;
    known_email?: string;
  }) =>
    request<{
      status: string;
      match: {
        linkedin_url?: string;
        candidate_url?: string;
        score: number;
        tier: "confirmed" | "possible" | "no_match";
        evidence: string[];
      };
    }>("/identity/resolve", { method: "POST", body: JSON.stringify(body) }),
  identityQueue: () => request<{ status: string; queue: any[] }>("/identity/queue"),
  identityQueueDecide: (id: string, decision: "confirm" | "reject") =>
    request<{ status: string; item: any }>(`/identity/queue/${encodeURIComponent(id)}`, {
      method: "POST",
      body: JSON.stringify({ decision }),
    }),
};
