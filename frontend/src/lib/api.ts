/** Thin typed wrappers over the JSON API. */

import type {
  Conversation,
  Message,
  ModelOptions,
  OllamaModel,
  Skill,
  ToolMode,
} from "../types";

const BASE = "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });

  if (!res.ok) {
    let detail = `Server returned ${res.status}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      /* keep the status-code message */
    }
    throw new Error(detail);
  }

  return res.json() as Promise<T>;
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  body: JSON.stringify(body),
});

const patch = (body: unknown): RequestInit => ({
  method: "PATCH",
  body: JSON.stringify(body),
});

export interface HealthInfo {
  status: string;
  default_model: string;
  default_system_prompt: string;
  default_options: ModelOptions;
  ollama_online: boolean;
  ollama_host: string;
  available_models: OllamaModel[];
}

export interface AppConfig {
  max_images_per_message: number;
  tool_modes: ToolMode[];
  default_model: string;
  auth_required: boolean;
}

export interface OllamaCredentials {
  has_api_key: boolean;
  key_hint: string;
  host_override: string;
  ollama_host?: string;
  ollama_online?: boolean;
  cloud_models?: string[];
  error?: string | null;
}

export const api = {
  health: () => request<HealthInfo>("/api/health"),
  config: () => request<AppConfig>("/api/config"),

  credentials: () => request<OllamaCredentials>("/api/ollama/credentials"),

  saveCredentials: (body: { api_key?: string; host?: string }) =>
    request<OllamaCredentials>("/api/ollama/credentials", {
      method: "PUT",
      body: JSON.stringify(body),
    }),

  clearCredentials: () =>
    request<{ status: string }>("/api/ollama/credentials", { method: "DELETE" }),

  verifyKey: (apiKey?: string) => {
    const fd = new FormData();
    if (apiKey) fd.append("api_key", apiKey);
    return fetch("/api/ollama/verify", { method: "POST", body: fd }).then((r) =>
      r.json()
    ) as Promise<{
      ok: boolean;
      host: string;
      model_count?: number;
      cloud_models?: string[];
      cloud_auth_ok?: boolean | null;
      cloud_error?: string;
      note?: string;
      error?: string;
    }>;
  },

  models: () =>
    request<{
      ollama_online: boolean;
      ollama_host: string;
      models: OllamaModel[];
      default_model: string;
      error: string | null;
    }>("/api/models"),

  skills: () =>
    request<{ skills: Skill[]; default_enabled: string[] }>("/api/skills"),

  listConversations: () =>
    request<{ conversations: Conversation[] }>("/api/conversations"),

  createConversation: (body: { title?: string; model?: string }) =>
    request<{ id: string }>("/api/conversations", json(body)),

  getConversation: (id: string) =>
    request<{ conversation: Conversation; messages: Message[] }>(
      `/api/conversations/${id}`
    ),

  updateConversation: (
    id: string,
    body: Partial<{
      title: string;
      model: string;
      system_prompt: string;
      options: ModelOptions;
      tool_mode: ToolMode;
      pinned: boolean;
    }>
  ) => request<{ conversation: Conversation }>(`/api/conversations/${id}`, patch(body)),

  deleteConversation: (id: string) =>
    request<{ status: string }>(`/api/conversations/${id}`, { method: "DELETE" }),

  branchConversation: (convId: string, messageId: string) =>
    request<{ id: string }>(
      `/api/conversations/${convId}/branch?up_to_message_id=${encodeURIComponent(messageId)}`,
      { method: "POST" }
    ),

  editMessage: (id: string, content: string, images?: string[]) =>
    request<{ message: Message }>(`/api/messages/${id}`, patch({ content, images })),

  deleteMessage: (id: string) =>
    request<{ status: string }>(`/api/messages/${id}`, { method: "DELETE" }),

  generateTitle: (convId: string) =>
    request<{ title: string }>(`/api/conversations/${convId}/title`, { method: "POST" }),

  stopGeneration: (convId: string) => {
    const fd = new FormData();
    fd.append("conversation_id", convId);
    return fetch("/api/chat/stop", { method: "POST", body: fd }).then((r) =>
      r.json()
    );
  },

  exportUrl: (convId: string, format: "md" | "json" = "md") =>
    `/api/conversations/${convId}/export?format=${format}`,

  async uploadImage(file: File | Blob, filename = "image.png") {
    const fd = new FormData();
    fd.append("file", file, filename);
    const res = await fetch("/api/upload", { method: "POST", body: fd });
    if (!res.ok) {
      let detail = "Upload failed";
      try {
        const body = await res.json();
        if (body?.detail) detail = body.detail;
      } catch {
        /* ignore */
      }
      throw new Error(detail);
    }
    return res.json();
  },
};