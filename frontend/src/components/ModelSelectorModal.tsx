import React, { useState } from "react";
import {
  X,
  Cpu,
  RefreshCw,
  Search,
  Check,
  HardDrive,
  Layers,
  Sparkles,
  Download,
  AlertCircle,
  ExternalLink
} from "lucide-react";
import { OllamaModel } from "../types";
import { readSSE } from "../lib/sse";

interface ModelSelectorModalProps {
  isOpen: boolean;
  onClose: () => void;
  models: OllamaModel[];
  currentModel: string;
  onSelectModel: (modelName: string) => void;
  onRefreshModels: () => Promise<void>;
  ollamaOnline: boolean;
  ollamaHost: string;
}

export const ModelSelectorModal: React.FC<ModelSelectorModalProps> = ({
  isOpen,
  onClose,
  models,
  currentModel,
  onSelectModel,
  onRefreshModels,
  ollamaOnline,
  ollamaHost,
}) => {
  const [searchQuery, setSearchQuery] = useState("");
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [pullInput, setPullInput] = useState("");
  const [pulling, setPulling] = useState(false);
  const [pullStatus, setPullStatus] = useState<string | null>(null);

  if (!isOpen) return null;

  const handleRefresh = async () => {
    setIsRefreshing(true);
    try {
      await onRefreshModels();
    } finally {
      setIsRefreshing(false);
    }
  };

  const filteredModels = models.filter((m) => {
    const q = searchQuery.toLowerCase();
    return (
      m.name.toLowerCase().includes(q) ||
      (m.family && m.family.toLowerCase().includes(q)) ||
      (m.parameter_size && m.parameter_size.toLowerCase().includes(q)) ||
      (m.quantization && m.quantization.toLowerCase().includes(q))
    );
  });

  const handleSelect = (modelName: string) => {
    onSelectModel(modelName);
    onClose();
  };

  const handlePullModel = async (e: React.FormEvent) => {
    e.preventDefault();
    const targetModel = pullInput.trim();
    if (!targetModel) return;

    setPulling(true);
    setPullStatus(`Requesting pull for "${targetModel}" from Ollama library...`);

    try {
      const res = await fetch("/api/models/pull", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model_name: targetModel }),
      });

      if (!res.ok) {
        // Fallback info if pull endpoint isn't supported or fails
        const errData = await res.json().catch(() => ({}));
        setPullStatus(`Note: You can pull this in terminal by running: ollama pull ${targetModel}`);
        return;
      }

      if (res.body) {
        await readSSE(res, (ev: any) => {
          if (ev.type !== "progress") return;
          let progress = ev.status;
          if (ev.total && ev.completed) {
            const pct = Math.round((ev.completed / ev.total) * 100);
            progress += ` (${pct}%)`;
          }
          setPullStatus(progress);
          if (ev.done) {
            if (ev.error) {
              setPullStatus(`Pull failed: ${progress}`);
            } else {
              setPullStatus(`Successfully pulled ${targetModel}!`);
              onRefreshModels().then(() => onSelectModel(targetModel));
            }
          }
        });
      }
      await onRefreshModels();
      setPullInput("");
    } catch (err: any) {
      setPullStatus(`Run 'ollama pull ${targetModel}' in your terminal to download.`);
    } finally {
      setPulling(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content model-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div style={{ display: "flex", alignItems: "center", gap: "0.6rem" }}>
            <div className="modal-header-icon">
              <Cpu size={20} />
            </div>
            <div>
              <h3 style={{ fontSize: "1.15rem", fontWeight: 700, margin: 0 }}>
                Ollama Models & LLM Selector
              </h3>
              <p style={{ fontSize: "0.76rem", color: "var(--text-muted)", margin: 0 }}>
                Connects to local Ollama instance ({ollamaHost})
              </p>
            </div>
          </div>
          <button className="action-btn" onClick={onClose} title="Close">
            <X size={18} />
          </button>
        </div>

        {/* Status & Search Toolbar */}
        <div className="model-modal-toolbar">
          <div className="model-search-bar">
            <Search size={15} style={{ color: "var(--text-faint)" }} />
            <input
              type="text"
              placeholder="Search by model name, parameter size, or family..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              autoFocus
            />
            {searchQuery && (
              <button
                className="clear-search-btn"
                onClick={() => setSearchQuery("")}
                title="Clear filter"
              >
                <X size={13} />
              </button>
            )}
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
            <button
              className="refresh-models-btn"
              onClick={handleRefresh}
              disabled={isRefreshing}
              title="Refresh installed models from Ollama"
            >
              <RefreshCw size={13} className={isRefreshing ? "spin-icon" : ""} />
              <span>Refresh</span>
            </button>
          </div>
        </div>

        {/* Ollama Connection Banner */}
        <div className={`ollama-status-banner ${ollamaOnline ? "online" : "offline"}`}>
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
            <span className={`status-dot ${ollamaOnline ? "" : "offline"}`} />
            <span style={{ fontSize: "0.8rem", fontWeight: 500 }}>
              {ollamaOnline
                ? `Ollama Service Online • ${models.length} model${models.length === 1 ? "" : "s"} installed locally`
                : `Ollama Service is Offline at ${ollamaHost}`}
            </span>
          </div>
          <span style={{ fontSize: "0.74rem", opacity: 0.8 }}>
            {ollamaOnline ? "Ready for inference" : "Make sure Ollama is running: 'ollama serve'"}
          </span>
        </div>

        {/* Models List */}
        <div className="models-list-scroll">
          {filteredModels.length === 0 ? (
            <div className="models-empty-state">
              <AlertCircle size={28} style={{ color: "var(--text-faint)", marginBottom: "0.5rem" }} />
              <p style={{ fontWeight: 600, fontSize: "0.9rem" }}>No matching models found</p>
              <p style={{ fontSize: "0.8rem", color: "var(--text-muted)" }}>
                {models.length === 0
                  ? "No models were detected in Ollama. Pull a model using the form below or run 'ollama pull <model>' in terminal."
                  : `No models match "${searchQuery}". Try a different keyword.`}
              </p>
            </div>
          ) : (
            filteredModels.map((m) => {
              const isSelected = m.name === currentModel;
              return (
                <div
                  key={m.name}
                  className={`model-card-item ${isSelected ? "selected" : ""}`}
                  onClick={() => handleSelect(m.name)}
                >
                  <div className="model-card-left">
                    <div className="model-card-title-row">
                      <span className="model-card-name">{m.name}</span>
                      {isSelected && (
                        <span className="active-model-tag">
                          <Check size={11} /> Current LLM
                        </span>
                      )}
                    </div>

                    <div className="model-card-tags">
                      {m.parameter_size && (
                        <span className="model-param-tag">
                          <Layers size={11} />
                          {m.parameter_size}
                        </span>
                      )}
                      {m.size_formatted && (
                        <span className="model-size-tag">
                          <HardDrive size={11} />
                          {m.size_formatted}
                        </span>
                      )}
                      {m.family && (
                        <span className="model-family-tag">
                          {m.family}
                        </span>
                      )}
                      {m.quantization && (
                        <span className="model-quant-tag">
                          {m.quantization}
                        </span>
                      )}
                    </div>
                  </div>

                  <div className="model-card-right">
                    <button
                      className={`select-model-btn ${isSelected ? "is-active" : ""}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        handleSelect(m.name);
                      }}
                    >
                      {isSelected ? "Active" : "Switch To Model"}
                    </button>
                  </div>
                </div>
              );
            })
          )}
        </div>

        {/* Pull / Add Custom Model section */}
        <div className="model-pull-footer">
          <form onSubmit={handlePullModel} className="pull-model-form">
            <div style={{ display: "flex", alignItems: "center", gap: "0.4rem", color: "var(--text-muted)", fontSize: "0.76rem", width: "100%" }}>
              <Sparkles size={13} color="var(--accent-primary)" />
              <span>Need another model? Download directly from Ollama library:</span>
            </div>
            <div style={{ display: "flex", gap: "0.5rem", width: "100%", marginTop: "0.25rem" }}>
              <input
                type="text"
                placeholder="e.g. llama3.2, mistral:7b, deepseek-r1:8b..."
                value={pullInput}
                onChange={(e) => setPullInput(e.target.value)}
                disabled={pulling}
                className="pull-input"
              />
              <button
                type="submit"
                disabled={pulling || !pullInput.trim()}
                className="pull-submit-btn"
              >
                {pulling ? (
                  <>
                    <RefreshCw size={13} className="spin-icon" />
                    <span>Pulling...</span>
                  </>
                ) : (
                  <>
                    <Download size={13} />
                    <span>Pull Model</span>
                  </>
                )}
              </button>
            </div>
            {pullStatus && (
              <div className="pull-status-message">
                <span>{pullStatus}</span>
              </div>
            )}
          </form>
        </div>
      </div>
    </div>
  );
};
