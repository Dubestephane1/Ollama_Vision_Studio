import React, { useEffect, useState } from "react";
import {
  X,
  Settings,
  Cpu,
  Sliders,
  MessageSquare,
  Download,
  KeyRound,
  Eye,
  EyeOff,
  Check,
  Loader2,
} from "lucide-react";
import type { ModelOptions, ToolMode } from "../types";
import { api } from "../lib/api";
import { useToast } from "./Toaster";

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  conversationId: string | null;
  systemPrompt: string;
  options: ModelOptions;
  toolMode: ToolMode;
  onSave: (body: {
    system_prompt: string;
    options: ModelOptions;
    tool_mode: ToolMode;
  }) => Promise<void>;
  onExport: (format: "md" | "json") => void;
}

const PRESETS: Record<string, ModelOptions> = {
  Precise: { temperature: 0.1, top_p: 0.9 },
  Balanced: { temperature: 0.7, top_p: 0.9 },
  Creative: { temperature: 1.1, top_p: 0.95 },
  Deterministic: { temperature: 0.0, top_p: 1.0, seed: 42 },
};

const SYSTEM_PRESETS: Record<string, string> = {
  General:
    "You are a helpful, accurate assistant running locally on the user's machine.",
  Concise:
    "You are a concise assistant. Answer in as few words as possible. Skip preamble and summaries unless asked.",
  Coding:
    "You are a senior software engineer. Give working code, explain trade-offs briefly, and never invent APIs.",
  "Vision Analyst":
    "You are a precise visual analyst. Describe only what is actually visible in the image, and state clearly when you cannot determine something.",
  Translator:
    "You are a professional translator. Preserve the original meaning and tone. Reply with the translation only unless asked for explanation.",
};

const TABS = [
  { id: "prompt", label: "System prompt", icon: MessageSquare },
  { id: "params", label: "Parameters", icon: Sliders },
  { id: "connection", label: "Connection", icon: KeyRound },
  { id: "export", label: "Export", icon: Download },
] as const;

export const SettingsModal: React.FC<SettingsModalProps> = ({
  isOpen,
  onClose,
  conversationId,
  systemPrompt,
  options,
  toolMode,
  onSave,
  onExport,
}) => {
  const [tab, setTab] = useState<typeof TABS[number]["id"]>("prompt");
  const [prompt, setPrompt] = useState(systemPrompt);
  const [opts, setOpts] = useState<ModelOptions>(options);
  const [mode, setMode] = useState<ToolMode>(toolMode);
  const [saving, setSaving] = useState(false);

  // Connection state
  const [apiKey, setApiKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [hostOverride, setHostOverride] = useState("");
  const [hasKey, setHasKey] = useState(false);
  const [keyHint, setKeyHint] = useState("");
  const [cloudModels, setCloudModels] = useState<string[]>([]);
  const [connBusy, setConnBusy] = useState(false);
  const [connMsg, setConnMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const toast = useToast();

  useEffect(() => {
    if (isOpen) {
      setPrompt(systemPrompt);
      setOpts(options);
      setMode(toolMode);
      setTab("prompt");
    }
  }, [isOpen, systemPrompt, options, toolMode]);

  const loadCredentials = async () => {
    try {
      const d = await api.credentials();
      setHasKey(!!d.has_api_key);
      setKeyHint(d.key_hint || "");
      setHostOverride(d.host_override || "");
    } catch {
      /* non-fatal */
    }
  };

  useEffect(() => {
    if (isOpen && tab === "connection") loadCredentials();
  }, [isOpen, tab]);

  const testKey = async () => {
    setConnBusy(true);
    setConnMsg(null);
    try {
      const r = await api.verifyKey(apiKey.trim() || undefined);
      if (r.ok) {
        const cloud =
          r.cloud_models?.length
            ? r.cloud_auth_ok === true
              ? `, ${r.cloud_models.length} cloud model(s) authenticated`
              : r.cloud_auth_ok === false
                ? `, but cloud auth failed: ${r.cloud_error || "unknown"}`
                : ""
            : "";
        setConnMsg({
          ok: r.cloud_auth_ok !== false,
          text:
            `Reachable — ${r.model_count} models visible${cloud}.` +
            (r.note ? ` ${r.note}` : ""),
        });
      } else {
        setConnMsg({ ok: false, text: r.error || "Connection failed." });
      }
    } catch (e: any) {
      setConnMsg({ ok: false, text: e.message });
    } finally {
      setConnBusy(false);
    }
  };

  const saveCredentials = async () => {
    setConnBusy(true);
    setConnMsg(null);
    try {
      const body: Record<string, string> = {};
      if (apiKey.trim()) body.api_key = apiKey.trim();
      body.host = hostOverride.trim();
      const d = await api.saveCredentials(body);
      setHasKey(!!d.has_api_key);
      setKeyHint(d.key_hint || "");
      setApiKey("");
      setCloudModels(d.cloud_models || []);
      setConnMsg({
        ok: !!d.ollama_online,
        text: d.ollama_online
          ? `Saved. Connected to ${d.ollama_host}.`
          : `Saved, but Ollama is not reachable: ${d.error || "unknown error"}`,
      });
      toast.push({
        kind: d.ollama_online ? "success" : "warning",
        title: d.ollama_online ? "Connection updated" : "Saved but unreachable",
        detail: d.ollama_online ? d.ollama_host : d.error,
      });
    } catch (e: any) {
      setConnMsg({ ok: false, text: e.message });
    } finally {
      setConnBusy(false);
    }
  };

  const clearKey = async () => {
    setConnBusy(true);
    try {
      await api.clearCredentials();
      setHasKey(false);
      setKeyHint("");
      setApiKey("");
      setConnMsg({ ok: true, text: "API key cleared." });
    } catch (e: any) {
      setConnMsg({ ok: false, text: e.message });
    } finally {
      setConnBusy(false);
    }
  };

  if (!isOpen) return null;

  const save = async () => {
    setSaving(true);
    try {
      await onSave({ system_prompt: prompt, options: opts, tool_mode: mode });
      onClose();
    } finally {
      setSaving(false);
    }
  };

  const num = (key: keyof ModelOptions, value: string) => {
    const n = Number(value);
    setOpts((o) => ({ ...o, [key]: value === "" ? undefined : isNaN(n) ? value : n }));
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content settings-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div style={{ display: "flex", alignItems: "center", gap: "0.6rem" }}>
            <div className="modal-header-icon">
              <Settings size={20} />
            </div>
            <div>
              <h3 style={{ fontSize: "1.1rem", fontWeight: 700, margin: 0 }}>
                Conversation Settings
              </h3>
              <p style={{ fontSize: "0.75rem", color: "var(--text-muted)", margin: 0 }}>
                Applies to this conversation only
              </p>
            </div>
          </div>
          <button className="action-btn" onClick={onClose} title="Close">
            <X size={18} />
          </button>
        </div>

        <div className="settings-tabs">
          {TABS.map((t) => (
            <button
              key={t.id}
              className={`settings-tab ${tab === t.id ? "active" : ""}`}
              onClick={() => setTab(t.id)}
            >
              <t.icon size={13} />
              <span>{t.label}</span>
            </button>
          ))}
        </div>

        {tab === "prompt" && (
          <div className="settings-body">
            <label className="settings-label">Tool calling</label>
            <div className="tool-mode-row">
              {(["auto", "on", "off"] as ToolMode[]).map((m) => (
                <button
                  key={m}
                  className={`tool-mode-btn ${mode === m ? "active" : ""}`}
                  onClick={() => setMode(m)}
                >
                  {m === "auto" ? "Auto" : m === "on" ? "Always" : "Never"}
                </button>
              ))}
            </div>
            <p className="settings-help">
              {mode === "auto" &&
                "Probes the model once, then caches the result. Falls back to a plain chat if the model cannot call tools."}
              {mode === "on" &&
                "Always offers tools to the model. Use this if you know the model supports tool calling."}
              {mode === "off" &&
                "Never offers tools. The model answers directly without calling any skill."}
            </p>

            <label className="settings-label">System prompt</label>
            <div className="preset-row">
              {Object.entries(SYSTEM_PRESETS).map(([name, value]) => (
                <button key={name} className="preset-chip" onClick={() => setPrompt(value)}>
                  {name}
                </button>
              ))}
            </div>
            <textarea
              className="settings-textarea"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={10}
              placeholder="Instructions given to the model at the start of every turn…"
            />
          </div>
        )}

        {tab === "params" && (
          <div className="settings-body">
            <label className="settings-label">Presets</label>
            <div className="preset-row">
              {Object.entries(PRESETS).map(([name, value]) => (
                <button
                  key={name}
                  className="preset-chip"
                  onClick={() => setOpts((o) => ({ ...o, ...value }))}
                >
                  {name}
                </button>
              ))}
            </div>

            <div className="params-grid">
              <div className="param">
                <label>
                  temperature <span className="param-val">{opts.temperature ?? 0.7}</span>
                </label>
                <input
                  type="range"
                  min={0}
                  max={2}
                  step={0.05}
                  value={opts.temperature ?? 0.7}
                  onChange={(e) => num("temperature", e.target.value)}
                />
                <small>Lower is more focused and repeatable.</small>
              </div>

              <div className="param">
                <label>
                  top_p <span className="param-val">{opts.top_p ?? 0.9}</span>
                </label>
                <input
                  type="range"
                  min={0}
                  max={1}
                  step={0.01}
                  value={opts.top_p ?? 0.9}
                  onChange={(e) => num("top_p", e.target.value)}
                />
                <small>Nucleus sampling cutoff.</small>
              </div>

              <div className="param">
                <label>
                  context window <span className="param-val">{opts.num_ctx ?? 4096}</span>
                </label>
                <input
                  type="number"
                  min={512}
                  max={131072}
                  step={512}
                  value={opts.num_ctx ?? 4096}
                  onChange={(e) => num("num_ctx", e.target.value)}
                />
                <small>Raise for long chats. Uses more VRAM.</small>
              </div>

              <div className="param">
                <label>
                  max reply tokens <span className="param-val">{opts.num_predict ?? -1}</span>
                </label>
                <input
                  type="number"
                  min={-1}
                  max={16384}
                  step={128}
                  value={opts.num_predict ?? -1}
                  onChange={(e) => num("num_predict", e.target.value)}
                />
                <small>-1 means unlimited.</small>
              </div>

              <div className="param">
                <label>seed</label>
                <input
                  type="number"
                  placeholder="random"
                  value={opts.seed ?? ""}
                  onChange={(e) => num("seed", e.target.value)}
                />
                <small>Fix for reproducible output.</small>
              </div>
            </div>
            <p className="settings-help">
              <Cpu size={11} style={{ verticalAlign: -1 }} /> These map to Ollama's
              generation options and are sent with every request.
            </p>
          </div>
        )}

        {tab === "connection" && (
          <div className="settings-body">
            <p className="settings-help" style={{ marginTop: 0 }}>
              Ollama Cloud models (<code>:cloud</code>) authenticate with the account you
              signed in to — run <code>ollama signin</code> once in a terminal and they
              work with no key here. Add an API key only if you use a remote Ollama
              host or a different account.
            </p>

            <label className="settings-label">Ollama host</label>
            <input
              className="settings-input"
              type="text"
              placeholder="http://localhost:11434"
              value={hostOverride}
              onChange={(e) => setHostOverride(e.target.value)}
            />
            <p className="settings-help">Leave blank to use the default local host.</p>

            <label className="settings-label">API key</label>
            <div className="apikey-row">
              <div className="apikey-input-wrap">
                <input
                  className="settings-input"
                  type={showKey ? "text" : "password"}
                  placeholder={hasKey ? `Key saved (${keyHint})` : "Paste your Ollama API key"}
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  autoComplete="off"
                  spellCheck={false}
                />
                <button
                  className="apikey-toggle"
                  onClick={() => setShowKey((s) => !s)}
                  title={showKey ? "Hide" : "Show"}
                >
                  {showKey ? <EyeOff size={14} /> : <Eye size={14} />}
                </button>
              </div>
              <button className="btn-ghost" onClick={testKey} disabled={connBusy}>
                {connBusy ? <Loader2 size={13} className="animate-spin" /> : null}
                Test
              </button>
            </div>

            {hasKey && (
              <button className="link-danger" onClick={clearKey} disabled={connBusy}>
                Remove saved key
              </button>
            )}

            {connMsg && (
              <div className={`conn-msg ${connMsg.ok ? "ok" : "bad"}`}>
                {connMsg.ok && <Check size={13} />}
                <span>{connMsg.text}</span>
              </div>
            )}

            <p className="settings-help">
              The key is stored in <code>backend/data/settings.json</code> on this
              machine only. It is never returned to the browser and never sent anywhere
              except your configured Ollama host.
            </p>
          </div>
        )}

        {tab === "export" && (
          <div className="settings-body">
            <p className="settings-help" style={{ marginTop: 0 }}>
              Download this conversation including model, tool calls and statistics.
            </p>
            <div className="export-row">
              <button className="btn-primary" disabled={!conversationId}
                onClick={() => onExport("md")}>
                <Download size={14} /> Markdown
              </button>
              <button className="btn-ghost" disabled={!conversationId}
                onClick={() => onExport("json")}>
                <Download size={14} /> JSON
              </button>
            </div>
          </div>
        )}

        <div className="modal-footer">
          <button className="btn-ghost" onClick={onClose}>
            {tab === "connection" ? "Close" : "Cancel"}
          </button>
          {tab === "connection" && (
            <button className="btn-primary" onClick={saveCredentials} disabled={connBusy}>
              {connBusy ? "Saving…" : "Save connection"}
            </button>
          )}
          {tab !== "export" && tab !== "connection" && (
            <button className="btn-primary" onClick={save} disabled={saving}>
              {saving ? "Saving…" : "Save settings"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
};