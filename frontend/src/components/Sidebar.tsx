import React, { useMemo, useState } from "react";
import {
  MessageSquarePlus,
  MessageSquare,
  Trash2,
  Sparkles,
  Cpu,
  ChevronRight,
  Search,
  Pin,
  X,
} from "lucide-react";
import type { Conversation } from "../types";

interface SidebarProps {
  conversations: Conversation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNewChat: () => void;
  onDelete: (id: string) => void;
  onTogglePin: (id: string) => void;
  ollamaOnline: boolean;
  currentModel: string;
  modelsCount: number;
  onOpenModelModal: () => void;
}

/** Bucket conversations into Today / Yesterday / This week / Older. */
function groupByDate(conv: Conversation[]): [string, Conversation[]][] {
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const day = 86400000;

  const buckets: Record<string, Conversation[]> = {
    Pinned: [],
    Today: [],
    Yesterday: [],
    "Previous 7 days": [],
    Older: [],
  };

  for (const c of conv) {
    if (c.pinned) {
      buckets.Pinned.push(c);
      continue;
    }
    const t = new Date(c.updated_at || c.created_at).getTime();
    if (Number.isNaN(t)) {
      buckets.Older.push(c);
    } else if (t >= startOfToday) {
      buckets.Today.push(c);
    } else if (t >= startOfToday - day) {
      buckets.Yesterday.push(c);
    } else if (t >= startOfToday - 7 * day) {
      buckets["Previous 7 days"].push(c);
    } else {
      buckets.Older.push(c);
    }
  }

  return Object.entries(buckets).filter(([, items]) => items.length > 0);
}

export const Sidebar: React.FC<SidebarProps> = ({
  conversations,
  activeId,
  onSelect,
  onNewChat,
  onDelete,
  onTogglePin,
  ollamaOnline,
  currentModel,
  modelsCount,
  onOpenModelModal,
}) => {
  const [query, setQuery] = useState("");
  const displayModelName =
    currentModel.length > 20 ? currentModel.split("/").pop() || currentModel : currentModel;

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return conversations;
    return conversations.filter(
      (c) =>
        c.title?.toLowerCase().includes(q) || c.model?.toLowerCase().includes(q)
    );
  }, [conversations, query]);

  const groups = useMemo(() => groupByDate(filtered), [filtered]);

  const renderConv = (conv: Conversation) => {
    const isActive = conv.id === activeId;
    const shortModel = conv.model ? conv.model.split("/").pop() || conv.model : "";
    return (
      <div
        key={conv.id}
        className={`conv-item ${isActive ? "active" : ""}`}
        onClick={() => onSelect(conv.id)}
        title={conv.title}
      >
        <div className="conv-main">
          <div className="conv-title-row">
            <MessageSquare size={14} style={{ minWidth: 14, opacity: isActive ? 1 : 0.6 }} />
            <span className="conv-title">{conv.title || "Untitled Chat"}</span>
          </div>
          {shortModel && (
            <span className="conv-model-tag" title={`Model: ${conv.model}`}>
              {shortModel}
            </span>
          )}
        </div>
        <div className="conv-buttons">
          <button
            className={`conv-pin-btn ${conv.pinned ? "pinned" : ""}`}
            title={conv.pinned ? "Unpin" : "Pin to top"}
            onClick={(e) => {
              e.stopPropagation();
              onTogglePin(conv.id);
            }}
          >
            <Pin size={12} />
          </button>
          <button
            className="conv-delete-btn"
            title="Delete chat"
            onClick={(e) => {
              e.stopPropagation();
              onDelete(conv.id);
            }}
          >
            <Trash2 size={13} />
          </button>
        </div>
      </div>
    );
  };

  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <div className="brand">
          <div className="brand-icon">
            <Sparkles size={20} />
          </div>
          <div>
            <h1 className="brand-title">Ollama Vision Studio</h1>
            <div className="brand-sub">
              <span>Local Multi-LLM GUI</span>
            </div>
          </div>
        </div>

        <button className="new-chat-btn" onClick={onNewChat}>
          <MessageSquarePlus size={16} />
          <span>New Conversation</span>
        </button>

        {conversations.length > 3 && (
          <div className="conv-search">
            <Search size={13} />
            <input
              type="text"
              placeholder="Search chats…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            {query && (
              <button onClick={() => setQuery("")} title="Clear">
                <X size={12} />
              </button>
            )}
          </div>
        )}
      </div>

      <div className="conversations-list">
        {conversations.length === 0 ? (
          <div className="sidebar-empty">No saved conversations yet.</div>
        ) : filtered.length === 0 ? (
          <div className="sidebar-empty">No chats match “{query}”.</div>
        ) : (
          groups.map(([label, items]) => (
            <div key={label} className="conv-group">
              <div className="conv-group-label">{label}</div>
              {items.map(renderConv)}
            </div>
          ))
        )}
      </div>

      <div className="sidebar-footer">
        <div
          className="sidebar-footer-card"
          onClick={onOpenModelModal}
          title="Click to view all local models or pull new ones"
        >
          <div className="status-indicator">
            <span className={`status-dot ${ollamaOnline ? "" : "offline"}`} />
            <span
              style={{
                color: ollamaOnline ? "var(--text-main)" : "var(--danger)",
                fontWeight: 500,
              }}
            >
              {ollamaOnline ? `Ollama (${modelsCount} models)` : "Ollama Offline"}
            </span>
          </div>
          <div className="sidebar-model-row">
            <div style={{ display: "flex", alignItems: "center", gap: "5px", overflow: "hidden" }}>
              <Cpu size={12} color="var(--accent-primary)" style={{ minWidth: 12 }} />
              <span className="sidebar-model-name" title={currentModel}>
                {displayModelName}
              </span>
            </div>
            <ChevronRight size={13} style={{ opacity: 0.5 }} />
          </div>
        </div>
      </div>
    </aside>
  );
};