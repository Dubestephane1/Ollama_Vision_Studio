import React, { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import {
  Bot,
  User,
  Image as ImageIcon,
  Sparkles,
  FileSearch,
  HardDrive,
  Copy,
  Check,
  RefreshCw,
  Pencil,
  Trash2,
  GitBranch,
  Scissors,
  Loader2,
} from "lucide-react";
import type { Message, ToolCall } from "../types";
import { ToolCallPanel } from "./ToolCallPanel";

interface MessageListProps {
  messages: Message[];
  isStreaming: boolean;
  streamingText: string;
  streamingToolCalls: ToolCall[];
  liveTools: Record<string, { ok: boolean; summary: string; duration_ms: number }>;
  liveToolNames: Record<string, string>;
  statusText: string | null;
  onSelectPrompt: (prompt: string) => void;
  onRegenerate: () => void;
  onEdit: (id: string, content: string) => void;
  onDelete: (id: string) => void;
  onBranch: (id: string) => void;
  onStop: () => void;
}

/** Copy-to-clipboard button with a transient confirmation tick. */
const CopyButton: React.FC<{ text: string; title?: string }> = ({
  text,
  title = "Copy",
}) => {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // Clipboard API needs a secure context; fall back to a temp textarea.
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy");
      } catch {
        return;
      } finally {
        document.body.removeChild(ta);
      }
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1400);
  };

  return (
    <button
      className="msg-action"
      onClick={copy}
      title={copied ? "Copied" : title}
      aria-label={title}
    >
      {copied ? <Check size={13} /> : <Copy size={13} />}
    </button>
  );
};

export const MessageList: React.FC<MessageListProps> = ({
  messages,
  isStreaming,
  streamingText,
  streamingToolCalls,
  liveTools,
  liveToolNames,
  statusText,
  onSelectPrompt,
  onRegenerate,
  onEdit,
  onDelete,
  onBranch,
  onStop,
}) => {
  const bottomRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  // Only auto-scroll when the user is already near the bottom, so scrolling
  // back to read earlier messages is not yanked away mid-stream.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const nearBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight < 220;
    if (nearBottom || messages.length <= 1) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [messages, streamingText, statusText]);

  const beginEdit = (m: Message) => {
    setEditingId(m.id);
    setDraft(m.content);
  };

  const commitEdit = () => {
    if (editingId && draft.trim()) {
      onEdit(editingId, draft.trim());
    }
    setEditingId(null);
    setDraft("");
  };

  const formatStats = (m: Message) => {
    const s = m.stats;
    if (!s || !s.tokens) return null;
    const tps =
      s.tokens && s.eval_ms ? (s.tokens / (s.eval_ms / 1000)).toFixed(1) : null;
    return (
      <span className="msg-stats">
        {s.tokens} tokens
        {tps && <> · {tps} tok/s</>}
        {s.total_ms != null && <> · {(s.total_ms / 1000).toFixed(1)}s</>}
        {s.done_reason === "length" && <> · hit length limit</>}
      </span>
    );
  };

  if (messages.length === 0 && !isStreaming) {
    return (
      <div className="messages-container" ref={scrollRef}>
        <div className="empty-state">
          <div className="empty-icon-wrapper">
            <Sparkles size={32} color="#fff" />
          </div>
          <h2 className="empty-title">Ollama Vision Studio</h2>
          <p className="empty-desc">
            Chat with any local Ollama model. Attach images, enable skills, and
            the model will call them on its own when a question needs live data.
          </p>

          <div className="starter-cards">
            <div
              className="starter-card"
              onClick={() =>
                onSelectPrompt(
                  "Please analyze this image in detail: identify all objects, scene context, and text."
                )
              }
            >
              <div className="starter-card-head">
                <ImageIcon size={16} color="var(--accent-primary)" />
                <h4>Scene Analysis</h4>
              </div>
              <p>Detect objects, context, layout, and colours from an image.</p>
            </div>

            <div
              className="starter-card"
              onClick={() =>
                onSelectPrompt(
                  "Please read and extract all visible text, numbers, or code in this image."
                )
              }
            >
              <div className="starter-card-head">
                <FileSearch size={16} color="#a855f7" />
                <h4>OCR & Transcription</h4>
              </div>
              <p>Extract text from documents, labels, signs, or screenshots.</p>
            </div>

            <div
              className="starter-card"
              onClick={() =>
                onSelectPrompt(
                  "Who was Ada Lovelace? Use the wikipedia tool to check."
                )
              }
            >
              <div className="starter-card-head">
                <HardDrive size={16} color="#10b981" />
                <h4>Look Something Up</h4>
              </div>
              <p>Let the model call Wikipedia or search the live web itself.</p>
            </div>

            <div
              className="starter-card"
              onClick={() =>
                onSelectPrompt(
                  "What is the latest top global news today? Summarise it factually."
                )
              }
            >
              <div className="starter-card-head">
                <Sparkles size={16} color="#ec4899" />
                <h4>Live News</h4>
              </div>
              <p>Fetch current headlines from multiple independent sources.</p>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="messages-container" ref={scrollRef}>
      {messages.map((msg, idx) => {
        const isUser = msg.role === "user";
        const isLast = idx === messages.length - 1;
        const isEditing = editingId === msg.id;

        return (
          <div key={msg.id} className={`message-row ${isUser ? "user" : "assistant"}`}>
            {!isUser && (
              <div className="avatar assistant">
                <Bot size={18} />
              </div>
            )}

            <div className="bubble-wrap">
              <div className="bubble">
                {msg.images && msg.images.length > 0 && (
                  <div className="message-images">
                    {msg.images.map((imgUrl, i) => (
                      <a key={i} href={imgUrl} target="_blank" rel="noopener noreferrer">
                        <img src={imgUrl} alt={`Attachment ${i + 1}`} className="attached-img" />
                      </a>
                    ))}
                  </div>
                )}

                {isEditing ? (
                  <div className="msg-edit">
                    <textarea
                      className="msg-edit-area"
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      autoFocus
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey) {
                          e.preventDefault();
                          commitEdit();
                        } else if (e.key === "Escape") {
                          setEditingId(null);
                        }
                      }}
                    />
                    <div className="msg-edit-actions">
                      <button className="btn-primary" onClick={commitEdit}>
                        Save &amp; resend
                      </button>
                      <button className="btn-ghost" onClick={() => setEditingId(null)}>
                        Cancel
                      </button>
                      <span className="msg-edit-hint">
                        Editing truncates everything after this message
                      </span>
                    </div>
                  </div>
                ) : isUser ? (
                  <div style={{ whiteSpace: "pre-wrap" }}>{msg.content}</div>
                ) : (
                  <div className="markdown-content">
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm]}
                      rehypePlugins={[rehypeHighlight]}
                      components={{
                        a: ({ node, ...props }) => (
                          <a {...props} target="_blank" rel="noopener noreferrer" />
                        ),
                      }}
                    >
                      {msg.content}
                    </ReactMarkdown>
                  </div>
                )}
              </div>

              {!isEditing && (
                <div className="msg-meta">
                  {msg.model && !isUser && (
                    <span className="msg-model">{msg.model.split("/").pop()}</span>
                  )}
                  {!!msg.truncated && (
                    <span className="msg-truncated" title="Generation was cut short">
                      incomplete
                    </span>
                  )}
                  {formatStats(msg)}
                  {!!msg.tool_calls?.length && (
                    <ToolCallPanel calls={msg.tool_calls} />
                  )}
                </div>
              )}

              {!isEditing && (
                <div className="msg-actions">
                  <CopyButton text={msg.content} />
                  {isUser ? (
                    <>
                      <button className="msg-action" onClick={() => beginEdit(msg)} title="Edit & resend">
                        <Pencil size={13} />
                      </button>
                      <button
                        className="msg-action"
                        onClick={() => onBranch(msg.id)}
                        title="Branch a new chat from here"
                      >
                        <GitBranch size={13} />
                      </button>
                    </>
                  ) : (
                    isLast && (
                      <button
                        className="msg-action"
                        onClick={onRegenerate}
                        title="Regenerate this response"
                      >
                        <RefreshCw size={13} />
                      </button>
                    )
                  )}
                  <button
                    className="msg-action danger"
                    onClick={() => onDelete(msg.id)}
                    title="Delete this message"
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              )}
            </div>

            {isUser && (
              <div className="avatar user">
                <User size={18} />
              </div>
            )}
          </div>
        );
      })}

      {isStreaming && (
        <div className="message-row assistant">
          <div className="avatar assistant">
            <Bot size={18} />
          </div>
          <div className="bubble-wrap">
            <div className="bubble">
              {statusText && !streamingText && (
                <div className="streaming-status">
                  <Loader2 size={13} className="animate-spin" />
                  <span>{statusText}</span>
                </div>
              )}
              <div className="markdown-content">
                <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
                  {streamingText}
                </ReactMarkdown>
                <span className="streaming-cursor" />
              </div>
            </div>

            <div className="msg-meta">
              <ToolCallPanel
                calls={[]}
                live={liveTools}
                liveName={liveToolNames}
                running={Object.keys(liveTools).length > 0}
              />
            </div>

            <div className="msg-actions streaming-actions">
              <button className="msg-action" onClick={onStop} title="Stop generating (Esc)">
                <Scissors size={13} />
                <span className="msg-action-label">Stop</span>
              </button>
            </div>
          </div>
        </div>
      )}

      <div ref={bottomRef} />
    </div>
  );
};