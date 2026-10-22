import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Sidebar } from "./components/Sidebar";
import { ChatHeader } from "./components/ChatHeader";
import { MessageList } from "./components/MessageList";
import { ChatInput } from "./components/ChatInput";
import { SkillsModal } from "./components/SkillsModal";
import { ModelSelectorModal } from "./components/ModelSelectorModal";
import { SettingsModal } from "./components/SettingsModal";
import { useToast } from "./components/Toaster";
import { api } from "./lib/api";
import { readSSE } from "./lib/sse";
import type {
  ChatEvent,
  Conversation,
  Message,
  ModelOptions,
  OllamaModel,
  Skill,
  ToolCall,
  ToolMode,
  UploadedImage,
} from "./types";

const MODEL_STORAGE_KEY = "ollama_selected_model";
const LEGACY_SKILLS_STORAGE_KEY = "ollama_enabled_skills";
const SKILL_PREFS_STORAGE_KEY = "ollama_skill_prefs";

/**
 * Core tools that are switched on out of the box: read a web page, look an
 * article up, search the live web. A fresh install - or one whose saved
 * state predates these tools - starts with them enabled. They can still be
 * turned off in the Tools modal; that choice is remembered.
 */
const DEFAULT_ON_SKILLS = ["fetch_url", "web_search", "grokipedia"];

/**
 * The skills that used to be on by default. A saved list from that era
 * contains them whether or not the user ever picked them, so they are not
 * carried over as "explicitly on" when the old state is migrated.
 */
const LEGACY_DEFAULT_SKILLS = [
  "calculator",
  "fetch_url",
  "grokipedia",
  "image_metadata",
  "web_search",
];

interface SkillPrefs {
  /** Skills the user explicitly turned on, on top of the defaults. */
  enabled: string[];
  /** Skills the user explicitly turned off. */
  disabled: string[];
}

/**
 * Read the saved skill preferences, migrating the legacy plain-array format.
 *
 * Legacy state was a flat list of every enabled skill name, including the
 * tools that were on by default back then. Those carry no intent, so only
 * the genuinely user-added tools are kept; the core tools are restored and
 * everything else that used to be a default stays off.
 */
function loadSkillPrefs(): SkillPrefs {
  try {
    const raw = localStorage.getItem(SKILL_PREFS_STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed?.enabled) && Array.isArray(parsed?.disabled)) {
        return { enabled: parsed.enabled, disabled: parsed.disabled };
      }
    }
  } catch (e) {
    console.warn("Could not read saved skill preferences:", e);
  }

  try {
    const raw = localStorage.getItem(LEGACY_SKILLS_STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        return {
          enabled: parsed.filter((name) => !LEGACY_DEFAULT_SKILLS.includes(name)),
          disabled: LEGACY_DEFAULT_SKILLS.filter(
            (name) => !parsed.includes(name) && !DEFAULT_ON_SKILLS.includes(name)
          ),
        };
      }
    }
  } catch (e) {
    console.warn("Could not read saved skills:", e);
  }

  return { enabled: [], disabled: [] };
}

export const App: React.FC = () => {
  const toast = useToast();

  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConvId, setActiveConvId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [activeConv, setActiveConv] = useState<Conversation | null>(null);

  const [isStreaming, setIsStreaming] = useState(false);
  const [streamingText, setStreamingText] = useState("");
  const [statusText, setStatusText] = useState<string | null>(null);
  const [streamingTools, setStreamingTools] = useState<ToolCall[]>([]);
  const [liveTools, setLiveTools] = useState<
    Record<string, { ok: boolean; summary: string; duration_ms: number }>
  >({});
  const [liveToolNames, setLiveToolNames] = useState<Record<string, string>>({});

  const [skills, setSkills] = useState<Skill[]>([]);
  const [activeSkills, setActiveSkills] = useState<string[]>([]);
  const [skillPrefs, setSkillPrefs] = useState<SkillPrefs>({ enabled: [], disabled: [] });
  const [isSkillsModalOpen, setIsSkillsModalOpen] = useState(false);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);

  const [ollamaOnline, setOllamaOnline] = useState(false);
  const [ollamaHost, setOllamaHost] = useState("http://localhost:11434");
  const [availableModels, setAvailableModels] = useState<OllamaModel[]>([]);
  const [currentModel, setCurrentModel] = useState<string>(
    () => localStorage.getItem(MODEL_STORAGE_KEY) || "openbmb/minicpm-v4.7:1b"
  );
  const [isModelModalOpen, setIsModelModalOpen] = useState(false);

  const abortRef = useRef<AbortController | null>(null);
  const convIdRef = useRef<string | null>(null);
  const skillsRef = useRef<string[]>([]);
  skillsRef.current = activeSkills;

  // ------------------------------------------------------------- loading --
  const fetchHealth = useCallback(async () => {
    try {
      const res = await fetch("/api/health");
      if (res.ok) {
        const data = await res.json();
        setOllamaOnline(data.ollama_online);
        if (data.ollama_host) setOllamaHost(data.ollama_host);
        if (!localStorage.getItem(MODEL_STORAGE_KEY) && data.default_model) {
          setCurrentModel(data.default_model);
        }
      }
    } catch {
      setOllamaOnline(false);
    }
  }, []);

  const fetchModels = useCallback(async () => {
    try {
      const data = await api.models();
      setOllamaOnline(data.ollama_online);
      if (data.ollama_host) setOllamaHost(data.ollama_host);
      if (Array.isArray(data.models)) setAvailableModels(data.models);
    } catch (e) {
      console.error("Failed to load models:", e);
    }
  }, []);

  const fetchConversations = useCallback(async () => {
    try {
      const data = await api.listConversations();
      setConversations(data.conversations || []);
      return data.conversations || [];
    } catch (e) {
      console.error("Failed to load conversations:", e);
      return [];
    }
  }, []);

  const fetchSkills = useCallback(async () => {
    try {
      const data = await api.skills();
      const discovered = data.skills || [];
      setSkills(discovered);
      // Saved state can name a skill that no longer exists - drop those.
      const known = new Set(discovered.map((s) => s.name));
      const defaults = data.default_enabled || [];
      const prefs = loadSkillPrefs();
      setSkillPrefs(prefs);
      setActiveSkills(
        Array.from(new Set([...defaults, ...prefs.enabled, ...DEFAULT_ON_SKILLS])).filter(
          (name) => known.has(name) && !prefs.disabled.includes(name)
        )
      );
    } catch (e) {
      console.error("Failed to load skills:", e);
    }
  }, []);

  const loadConversation = useCallback(async (id: string) => {
    try {
      const data = await api.getConversation(id);
      setMessages(data.messages || []);
      setActiveConv(data.conversation);
      if (data.conversation?.model) setCurrentModel(data.conversation.model);
    } catch (e: any) {
      console.error("Failed to load messages:", e);
      toast.push({ kind: "error", title: "Could not open chat", detail: e.message });
    }
  }, [toast]);

  useEffect(() => {
    fetchHealth();
    fetchModels();
    fetchSkills();
    (async () => {
      const convs = await fetchConversations();
      if (convs.length > 0) setActiveConvId(convs[0].id);
    })();
  }, [fetchHealth, fetchModels, fetchSkills, fetchConversations]);

  // Poll health so a restarted Ollama is noticed instead of showing stale "online".
  useEffect(() => {
    const id = setInterval(() => {
      if (!document.hidden) fetchHealth();
    }, 10000);
    return () => clearInterval(id);
  }, [fetchHealth]);

  useEffect(() => {
    convIdRef.current = activeConvId;
    if (activeConvId) {
      loadConversation(activeConvId);
    } else {
      setMessages([]);
      setActiveConv(null);
    }
  }, [activeConvId, loadConversation]);

  // Persist the user's skill choices so they survive a reload.
  useEffect(() => {
    localStorage.setItem(SKILL_PREFS_STORAGE_KEY, JSON.stringify(skillPrefs));
  }, [skillPrefs]);

  // Turn a tool on or off, remembering the choice as an explicit preference
  // so the defaults (fetch_url, web_search, grokipedia) stay on until the
  // user says otherwise.
  const handleToggleSkill = useCallback(
    (name: string) => {
      const turningOff = activeSkills.includes(name);
      setActiveSkills((prev) =>
        turningOff ? prev.filter((s) => s !== name) : [...prev, name]
      );
      setSkillPrefs((prefs) => {
        const enabled = new Set(prefs.enabled);
        const disabled = new Set(prefs.disabled);
        if (turningOff) {
          disabled.add(name);
          enabled.delete(name);
        } else {
          disabled.delete(name);
          enabled.add(name);
        }
        return { enabled: [...enabled], disabled: [...disabled] };
      });
    },
    [activeSkills]
  );

  // ------------------------------------------------------------- actions --
  const handleSelectModel = useCallback(
    async (modelName: string) => {
      setCurrentModel(modelName);
      localStorage.setItem(MODEL_STORAGE_KEY, modelName);
      if (convIdRef.current) {
        try {
          const res = await api.updateConversation(convIdRef.current, { model: modelName });
          setActiveConv(res.conversation);
          setConversations((prev) =>
            prev.map((c) => (c.id === convIdRef.current ? res.conversation : c))
          );
        } catch (e: any) {
          toast.push({ kind: "error", title: "Could not switch model", detail: e.message });
        }
      }
    },
    [toast]
  );

  const handleNewChat = useCallback(async () => {
    try {
      const created = await api.createConversation({ title: "New Chat", model: currentModel });
      await fetchConversations();
      setActiveConvId(created.id);
      setMessages([]);
    } catch (e: any) {
      toast.push({ kind: "error", title: "Could not create chat", detail: e.message });
    }
  }, [currentModel, fetchConversations, toast]);

  const handleUpdateTitle = useCallback(
    async (newTitle: string) => {
      const id = convIdRef.current;
      if (!id || !newTitle.trim()) return;
      try {
        await api.updateConversation(id, { title: newTitle.trim() });
        setConversations((prev) =>
          prev.map((c) => (c.id === id ? { ...c, title: newTitle.trim() } : c))
        );
      } catch (e: any) {
        toast.push({ kind: "error", title: "Rename failed", detail: e.message });
      }
    },
    [toast]
  );

  const handleDeleteConversation = useCallback(
    async (convId: string) => {
      if (!window.confirm("Delete this conversation and all its messages?")) return;
      try {
        await api.deleteConversation(convId);
        const remaining = conversations.filter((c) => c.id !== convId);
        setConversations(remaining);
        if (convIdRef.current === convId) {
          setActiveConvId(remaining.length > 0 ? remaining[0].id : null);
        }
        toast.push({ kind: "success", title: "Conversation deleted" });
      } catch (e: any) {
        toast.push({ kind: "error", title: "Delete failed", detail: e.message });
      }
    },
    [conversations, toast]
  );

  const handleTogglePin = useCallback(
    async (convId: string) => {
      const conv = conversations.find((c) => c.id === convId);
      if (!conv) return;
      const pinned = !conv.pinned;
      try {
        const res = await api.updateConversation(convId, { pinned });
        setConversations((prev) =>
          prev
            .map((c) => (c.id === convId ? res.conversation : c))
            .sort((a, b) => Number(b.pinned || 0) - Number(a.pinned || 0))
        );
      } catch (e: any) {
        toast.push({ kind: "error", title: "Could not pin", detail: e.message });
      }
    },
    [conversations, toast]
  );

  const handleUploadImage = useCallback(
    async (file: File | Blob, filename = "image.png"): Promise<UploadedImage | null> => {
      try {
        return await api.uploadImage(file, filename);
      } catch (e: any) {
        toast.push({ kind: "error", title: "Upload failed", detail: e.message });
        return null;
      }
    },
    [toast]
  );

  const handleStop = useCallback(async () => {
    const id = convIdRef.current;
    abortRef.current?.abort();
    if (id) {
      try {
        await api.stopGeneration(id);
      } catch {
        /* best effort - the abort already closed the connection */
      }
    }
  }, []);

  // ----------------------------------------------------------- streaming --
  const runStream = useCallback(
    async (convId: string, prompt: string, imageUrls: string[]) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      setIsStreaming(true);
      setStreamingText("");
      setStatusText(null);
      setStreamingTools([]);
      setLiveTools({});
      setLiveToolNames({});

      let accumulated = "";

      try {
        const res = await fetch("/api/chat/stream", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            conversation_id: convId,
            prompt,
            image_paths: imageUrls,
            model: currentModel,
            enabled_skills: skillsRef.current,
          }),
          signal: controller.signal,
        });

        if (!res.ok || !res.body) {
          let detail = `Server returned ${res.status}`;
          try {
            const body = await res.json();
            if (body?.detail) detail = body.detail;
          } catch {
            /* ignore */
          }
          throw new Error(detail);
        }

        await readSSE(
          res,
          (ev: ChatEvent) => {
            switch (ev.type) {
              case "status":
                setStatusText(ev.text);
                break;

              case "tool_start":
                setStatusText(`Running ${ev.name}…`);
                setLiveToolNames((n) => ({ ...n, [ev.id]: ev.name }));
                break;

              case "tool_result":
                setLiveTools((t) => ({
                  ...t,
                  [ev.id]: {
                    ok: ev.ok,
                    summary: ev.summary,
                    duration_ms: ev.duration_ms,
                  },
                }));
                setStreamingTools((t) => [
                  ...t,
                  {
                    id: ev.id,
                    name: ev.name,
                    ok: ev.ok,
                    duration_ms: ev.duration_ms,
                    args: {},
                  },
                ]);
                setStatusText(null);
                break;

              case "chunk":
                // A chunk with `replace` overwrites the buffer instead of
                // appending - used to retract raw tool-call markup.
                if (ev.replace) {
                  accumulated = ev.content;
                } else {
                  accumulated += ev.content;
                }
                setStreamingText(accumulated);
                break;

              case "stats":
                setStatusText(null);
                break;

              case "done": {
                if (!ev.message_id) {
                  setStatusText(null);
                  break;
                }
                const assistant: Message = {
                  id: ev.message_id,
                  conversation_id: convId,
                  role: "assistant",
                  content: ev.full_content || accumulated,
                  timestamp: new Date().toISOString(),
                  model: currentModel,
                  truncated: ev.truncated ? 1 : 0,
                };
                setMessages((prev) => [...prev, assistant]);
                setStreamingText("");
                setStatusText(null);
                if (ev.stopped) {
                  toast.push({
                    kind: "info",
                    title: "Generation stopped",
                    detail: "The partial reply was saved.",
                  });
                }
                if (ev.truncated && !ev.stopped) {
                  toast.push({
                    kind: "warning",
                    title: "Reply was cut short",
                    detail: "The model stopped before finishing. Try regenerating.",
                  });
                }
                fetchConversations();
                break;
              }

              case "error":
                toast.push({
                  kind: "error",
                  title: "Model error",
                  detail: ev.partial_saved
                    ? `${ev.error} (partial reply saved)`
                    : ev.error,
                });
                setStatusText(null);
                break;

              default:
                break;
            }
          },
          controller.signal
        );
      } catch (err: any) {
        if (err.name === "AbortError") {
          // Stop pressed: the server has already persisted what streamed in.
          setStatusText(null);
        } else {
          toast.push({
            kind: "error",
            title: "Chat stream error",
            detail: err.message,
          });
        }
      } finally {
        setIsStreaming(false);
        setStreamingText("");
        setStatusText(null);
        abortRef.current = null;
        // Reconcile with the server so ids, stats and tool calls are accurate.
        if (convIdRef.current) loadConversation(convIdRef.current);
      }
    },
    [currentModel, fetchConversations, loadConversation, toast]
  );

  const handleSendMessage = useCallback(
    async (text: string, images: string[]) => {
      let convId = convIdRef.current;

      if (!convId) {
        try {
          const created = await api.createConversation({ title: "New Chat", model: currentModel });
          convId = created.id;
          setActiveConvId(created.id);
        } catch (e: any) {
          toast.push({ kind: "error", title: "Could not start chat", detail: e.message });
          return;
        }
      }

      const tempMsg: Message = {
        id: `temp-${Date.now()}`,
        conversation_id: convId,
        role: "user",
        content: text,
        images,
        timestamp: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, tempMsg]);

      await runStream(convId, text, images);
    },
    [currentModel, runStream, toast]
  );

  const handleRegenerate = useCallback(async () => {
    const convId = convIdRef.current;
    if (!convId || isStreaming) return;

    // Find the last user message and re-send it after truncating the tail.
    let idx = -1;
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === "user") {
        idx = i;
        break;
      }
    }
    if (idx === -1) return;

    const lastUser = messages[idx];
    const tail = messages.slice(idx + 1);
    for (const m of tail) {
      if (m.id.startsWith("temp-")) continue;
      try {
        await api.deleteMessage(m.id);
      } catch {
        /* best effort */
      }
    }
    setMessages(messages.slice(0, idx + 1));

    await runStream(convId, lastUser.content, lastUser.images || []);
  }, [messages, isStreaming, runStream]);

  const handleEditMessage = useCallback(
    async (id: string, content: string) => {
      const convId = convIdRef.current;
      if (!convId) return;

      const idx = messages.findIndex((m) => m.id === id);
      if (idx === -1) return;

      const tail = messages.slice(idx + 1);
      for (const m of tail) {
        if (m.id.startsWith("temp-")) continue;
        try {
          await api.deleteMessage(m.id);
        } catch {
          /* best effort */
        }
      }

      try {
        await api.editMessage(id, content, messages[idx].images);
      } catch (e: any) {
        toast.push({ kind: "error", title: "Edit failed", detail: e.message });
        return;
      }

      const edited = messages.slice(0, idx + 1);
      setMessages([
        ...edited.slice(0, idx),
        { ...messages[idx], content },
      ]);
      await runStream(convId, content, messages[idx].images || []);
    },
    [messages, runStream, toast]
  );

  const handleDeleteMessage = useCallback(
    async (id: string) => {
      if (!window.confirm("Delete this message?")) return;
      try {
        await api.deleteMessage(id);
        setMessages((prev) => prev.filter((m) => m.id !== id));
      } catch (e: any) {
        toast.push({ kind: "error", title: "Delete failed", detail: e.message });
      }
    },
    [toast]
  );

  const handleBranch = useCallback(
    async (messageId: string) => {
      const convId = convIdRef.current;
      if (!convId) return;
      try {
        const res = await api.branchConversation(convId, messageId);
        await fetchConversations();
        setActiveConvId(res.id);
        toast.push({ kind: "success", title: "Branched into a new chat" });
      } catch (e: any) {
        toast.push({ kind: "error", title: "Branch failed", detail: e.message });
      }
    },
    [fetchConversations, toast]
  );

  const handleSaveSettings = useCallback(
    async (body: { system_prompt: string; options: ModelOptions; tool_mode: ToolMode }) => {
      const convId = convIdRef.current;
      if (!convId) return;
      try {
        const res = await api.updateConversation(convId, body);
        setActiveConv(res.conversation);
        setConversations((prev) => prev.map((c) => (c.id === convId ? res.conversation : c)));
        toast.push({ kind: "success", title: "Settings saved" });
      } catch (e: any) {
        toast.push({ kind: "error", title: "Save failed", detail: e.message });
      }
    },
    [toast]
  );

  const handleExport = useCallback((format: "md" | "json") => {
    const convId = convIdRef.current;
    if (!convId) return;
    window.open(api.exportUrl(convId, format), "_blank");
  }, []);

  const skillNames = useMemo(
    () => Object.fromEntries(skills.map((s) => [s.name, s.display_name])),
    [skills]
  );

  return (
    <div className="app-container">
      <Sidebar
        conversations={conversations}
        activeId={activeConvId}
        onSelect={setActiveConvId}
        onNewChat={handleNewChat}
        onDelete={handleDeleteConversation}
        onTogglePin={handleTogglePin}
        ollamaOnline={ollamaOnline}
        currentModel={currentModel}
        modelsCount={availableModels.length}
        onOpenModelModal={() => setIsModelModalOpen(true)}
      />

      <main className="chat-main">
        <ChatHeader
          conversation={activeConv}
          onUpdateTitle={handleUpdateTitle}
          onOpenSkills={() => setIsSkillsModalOpen(true)}
          onOpenSettings={() => setIsSettingsOpen(true)}
          activeSkillsCount={activeSkills.length}
          model={currentModel}
          models={availableModels}
          onSelectModel={handleSelectModel}
          onOpenModelModal={() => setIsModelModalOpen(true)}
          ollamaOnline={ollamaOnline}
        />

        <MessageList
          messages={messages}
          isStreaming={isStreaming}
          streamingText={streamingText}
          streamingToolCalls={streamingTools}
          liveTools={liveTools}
          liveToolNames={liveToolNames}
          statusText={statusText}
          onSelectPrompt={(p) => handleSendMessage(p, [])}
          onRegenerate={handleRegenerate}
          onEdit={handleEditMessage}
          onDelete={handleDeleteMessage}
          onBranch={handleBranch}
          onStop={handleStop}
        />

        <ChatInput
          onSendMessage={handleSendMessage}
          isStreaming={isStreaming}
          onUploadImage={handleUploadImage}
          activeSkills={activeSkills}
          currentModel={currentModel}
          onOpenModelModal={() => setIsModelModalOpen(true)}
          onStop={handleStop}
          skillNames={skillNames}
        />
      </main>

      <SkillsModal
        isOpen={isSkillsModalOpen}
        onClose={() => setIsSkillsModalOpen(false)}
        skills={skills}
        activeSkills={activeSkills}
        onToggleSkill={handleToggleSkill}
      />

      <ModelSelectorModal
        isOpen={isModelModalOpen}
        onClose={() => setIsModelModalOpen(false)}
        models={availableModels}
        currentModel={currentModel}
        onSelectModel={handleSelectModel}
        onRefreshModels={fetchModels}
        ollamaOnline={ollamaOnline}
        ollamaHost={ollamaHost}
      />

      <SettingsModal
        isOpen={isSettingsOpen}
        onClose={() => setIsSettingsOpen(false)}
        conversationId={activeConvId}
        systemPrompt={activeConv?.system_prompt || ""}
        options={activeConv?.options || {}}
        toolMode={(activeConv?.tool_mode || "auto") as ToolMode}
        onSave={handleSaveSettings}
        onExport={handleExport}
      />
    </div>
  );
};