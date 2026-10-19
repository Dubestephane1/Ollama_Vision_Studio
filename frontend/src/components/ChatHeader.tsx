import React, { useEffect, useRef, useState } from "react";
import {
  Wrench,
  Check,
  Edit2,
  Cpu,
  ChevronDown,
  Search,
  ExternalLink,
  Sparkles,
  Layers,
  HardDrive,
  Settings,
} from "lucide-react";
import type { Conversation, OllamaModel } from "../types";

interface ChatHeaderProps {
  conversation: Conversation | null;
  onUpdateTitle: (title: string) => void;
  onOpenSkills: () => void;
  onOpenSettings: () => void;
  activeSkillsCount: number;
  model: string;
  models: OllamaModel[];
  onSelectModel: (modelName: string) => void;
  onOpenModelModal: () => void;
  ollamaOnline: boolean;
}

export const ChatHeader: React.FC<ChatHeaderProps> = ({
  conversation,
  onUpdateTitle,
  onOpenSkills,
  onOpenSettings,
  activeSkillsCount,
  model,
  models,
  onSelectModel,
  onOpenModelModal,
  ollamaOnline,
}) => {
  const [isEditing, setIsEditing] = useState(false);
  const [title, setTitle] = useState(conversation?.title || "New Chat");
  const [isDropdownOpen, setIsDropdownOpen] = useState(false);
  const [searchFilter, setSearchFilter] = useState("");
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setTitle(conversation?.title || "New Chat");
  }, [conversation?.title]);

  useEffect(() => {
    if (!isDropdownOpen) return;
    const onClick = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setIsDropdownOpen(false);
      }
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [isDropdownOpen]);

  const handleSaveTitle = () => {
    setIsEditing(false);
    if (title.trim() && title.trim() !== conversation?.title) {
      onUpdateTitle(title.trim());
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") handleSaveTitle();
    else if (e.key === "Escape") {
      setTitle(conversation?.title || "New Chat");
      setIsEditing(false);
    }
  };

  const currentModelInfo = models.find((m) => m.name === model);

  const filtered = models.filter((m) => {
    if (!searchFilter) return true;
    const q = searchFilter.toLowerCase();
    return (
      m.name.toLowerCase().includes(q) ||
      (m.parameter_size && m.parameter_size.toLowerCase().includes(q)) ||
      (m.family && m.family.toLowerCase().includes(q))
    );
  });

  return (
    <header className="chat-header">
      <div className="header-left">
        {isEditing ? (
          <div style={{ display: "flex", alignItems: "center", gap: "0.25rem" }}>
            <input
              type="text"
              className="editable-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              onBlur={handleSaveTitle}
              onKeyDown={handleKeyDown}
              autoFocus
            />
            <button className="action-btn" onClick={handleSaveTitle} title="Save title">
              <Check size={14} />
            </button>
          </div>
        ) : (
          <div
            className="title-edit-wrap"
            onClick={() => setIsEditing(true)}
            title="Click to rename chat"
          >
            <h2 className="editable-title">{conversation?.title || "New Chat"}</h2>
            <Edit2 size={12} style={{ opacity: 0.4 }} />
          </div>
        )}

        <div className="model-dropdown-container" ref={dropdownRef}>
          <button
            className={`model-selector-btn ${isDropdownOpen ? "active" : ""} ${
              ollamaOnline ? "" : "offline"
            }`}
            onClick={() => setIsDropdownOpen((p) => !p)}
            title="Click to choose another Ollama LLM model"
          >
            <Cpu size={13} className="model-icon" />
            <span className="model-name-text">{model}</span>
            {currentModelInfo?.parameter_size && (
              <span className="model-size-badge">{currentModelInfo.parameter_size}</span>
            )}
            <ChevronDown size={12} className={`chevron-icon ${isDropdownOpen ? "open" : ""}`} />
          </button>

          {isDropdownOpen && (
            <div className="model-dropdown-menu">
              <div className="model-dropdown-header">
                <div className="model-dropdown-title">
                  <span style={{ fontWeight: 600, color: "var(--text-main)" }}>Local Models</span>
                  <span className="model-count-tag">{models.length}</span>
                </div>
                <button
                  className="model-details-link-btn"
                  onClick={() => {
                    setIsDropdownOpen(false);
                    onOpenModelModal();
                  }}
                  title="Open model manager and download new models"
                >
                  <span>All Specs &amp; Pull</span>
                  <ExternalLink size={11} />
                </button>
              </div>

              {models.length > 4 && (
                <div className="model-dropdown-search">
                  <Search size={12} style={{ color: "var(--text-faint)" }} />
                  <input
                    type="text"
                    placeholder="Search model…"
                    value={searchFilter}
                    onChange={(e) => setSearchFilter(e.target.value)}
                    autoFocus
                  />
                </div>
              )}

              <div className="model-dropdown-list">
                {filtered.length === 0 ? (
                  <div className="dd-empty">No matching model found</div>
                ) : (
                  filtered.map((m) => {
                    const isSelected = m.name === model;
                    return (
                      <div
                        key={m.name}
                        className={`model-dropdown-item ${isSelected ? "selected" : ""}`}
                        onClick={() => {
                          onSelectModel(m.name);
                          setIsDropdownOpen(false);
                        }}
                      >
                        <div className="model-item-info">
                          <span className="model-item-name">{m.name}</span>
                          <div className="model-item-meta">
                            {m.parameter_size && <span>{m.parameter_size}</span>}
                            {m.size_formatted && <span>• {m.size_formatted}</span>}
                            {m.quantization && <span>• {m.quantization}</span>}
                          </div>
                        </div>
                        {isSelected && <Check size={14} className="model-item-check" />}
                      </div>
                    );
                  })
                )}
              </div>

              <div className="model-dropdown-footer">
                <button
                  className="model-dropdown-manage-btn"
                  onClick={() => {
                    setIsDropdownOpen(false);
                    onOpenModelModal();
                  }}
                >
                  <Sparkles size={12} />
                  <span>Manage Models / Pull New LLM</span>
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="header-right">
        <button
          className={`skills-pill-btn ${activeSkillsCount > 0 ? "active" : ""}`}
          onClick={onOpenSkills}
          title="Choose which tools the model may call"
        >
          <Wrench size={13} />
          <span>Tools</span>
          {activeSkillsCount > 0 && (
            <span className="skills-count-badge">{activeSkillsCount}</span>
          )}
        </button>

        <button
          className="action-btn"
          onClick={onOpenSettings}
          title="System prompt, model parameters and export"
        >
          <Settings size={16} />
        </button>
      </div>
    </header>
  );
};