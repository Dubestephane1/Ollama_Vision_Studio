export interface Conversation {
  id: string;
  title: string;
  model: string;
  created_at: string;
  updated_at: string;
  system_prompt?: string | null;
  options?: ModelOptions | null;
  tool_mode?: ToolMode;
  pinned?: number | 0;
  folder?: string | null;
}

export type ToolMode = "auto" | "on" | "off";

export interface ModelOptions {
  temperature?: number;
  top_p?: number;
  num_predict?: number;
  num_ctx?: number;
  seed?: number | null;
  [key: string]: any;
}

export interface ToolCall {
  id?: string;
  name: string;
  args?: Record<string, any>;
  ok: boolean;
  cached?: boolean;
  duration_ms?: number;
  result?: any;
  error?: string | null;
}

export interface MessageStats {
  tokens?: number;
  total_ms?: number;
  eval_ms?: number;
  prompt_ms?: number;
  done_reason?: string;
}

export interface Message {
  id: string;
  conversation_id?: string;
  role: "user" | "assistant" | "system";
  content: string;
  images?: string[];
  timestamp?: string;
  model?: string | null;
  stats?: MessageStats | null;
  tool_calls?: ToolCall[] | null;
  truncated?: number | 0;
}

export interface Skill {
  name: string;
  display_name: string;
  description: string;
  parameters: Record<string, any>;
  enabled_by_default?: boolean;
}

export interface UploadedImage {
  filename: string;
  url: string;
  original_name: string;
  size?: number;
  format?: string;
  width?: number;
  height?: number;
}

export interface OllamaModel {
  name: string;
  size?: number;
  size_formatted?: string;
  parameter_size?: string;
  quantization?: string;
  family?: string;
  modified_at?: string;
}

export interface RunStats {
  model: string;
  tokens?: number | null;
  tok_per_sec?: number | null;
  total_ms?: number;
  eval_ms?: number | null;
  prompt_ms?: number | null;
  done_reason?: string | null;
  used_tools?: number;
}

/** Events emitted by /api/chat/stream. */
export type ChatEvent =
  | { type: "status"; stage: string; text: string }
  | { type: "tool_start"; id: string; name: string; args: Record<string, any> }
  | {
      type: "tool_result";
      id: string;
      name: string;
      display_name?: string;
      ok: boolean;
      cached?: boolean;
      duration_ms: number;
      summary: string;
    }
  | { type: "chunk"; content: string }
  | { type: "stats" } & RunStats
  | {
      type: "complete";
      full_content: string;
      tool_calls: ToolCall[];
      stats: MessageStats;
      truncated: boolean;
    }
  | {
      type: "done";
      message_id: string | null;
      title: string | null;
      full_content?: string;
      stopped: boolean;
      truncated: boolean;
    }
  | { type: "error"; error: string; partial_saved: boolean; message_id?: string | null };