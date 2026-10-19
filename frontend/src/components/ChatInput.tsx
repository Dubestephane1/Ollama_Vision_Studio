import React, { useEffect, useRef, useState } from "react";
import { ArrowUp, Image as ImageIcon, X, Loader2, Wrench, Square, Cpu } from "lucide-react";
import type { UploadedImage } from "../types";
import { isImageFile, prepareImage } from "../lib/image";

interface ChatInputProps {
  onSendMessage: (text: string, images: string[]) => void;
  isStreaming: boolean;
  onUploadImage: (file: File | Blob, filename?: string) => Promise<UploadedImage | null>;
  activeSkills: string[];
  currentModel?: string;
  onOpenModelModal?: () => void;
  onStop?: () => void;
  maxImages?: number;
  skillNames?: Record<string, string>;
}

export const ChatInput: React.FC<ChatInputProps> = ({
  onSendMessage,
  isStreaming,
  onUploadImage,
  activeSkills,
  currentModel = "openbmb/minicpm-v4.7:1b",
  onOpenModelModal,
  onStop,
  maxImages = 8,
  skillNames,
}) => {
  const [text, setText] = useState("");
  const [attachedImages, setAttachedImages] = useState<UploadedImage[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!isStreaming) {
      const timer = setTimeout(() => textareaRef.current?.focus(), 50);
      return () => clearTimeout(timer);
    }
  }, [isStreaming]);

  // Esc stops generation, but only when the composer is empty so it does not
  // interfere with normal typing.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && isStreaming && !text.trim()) {
        e.preventDefault();
        onStop?.();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isStreaming, text, onStop]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const handleSubmit = () => {
    if (isStreaming || isUploading) return;
    if (!text.trim() && attachedImages.length === 0) return;

    onSendMessage(text.trim(), attachedImages.map((i) => i.url));
    setText("");
    setAttachedImages([]);
    if (textareaRef.current) textareaRef.current.style.height = "auto";
  };

  const processFiles = async (files: File[]) => {
    const images = files.filter(isImageFile);
    const rejected = files.length - images.length;
    if (rejected > 0) {
      console.warn(`Skipped ${rejected} non-image file(s)`);
    }
    if (images.length === 0) return;

    setIsUploading(true);
    try {
      for (const file of images) {
        if (attachedImages.length >= maxImages) break;
        try {
          const prepared = await prepareImage(file);
          const res = await onUploadImage(prepared.blob, prepared.filename);
          if (res) setAttachedImages((prev) => [...prev, res]);
        } catch (e: any) {
          console.error("Upload failed:", e);
          alert(e.message || "Image upload failed");
        }
      }
    } finally {
      setIsUploading(false);
      setTimeout(() => textareaRef.current?.focus(), 50);
    }
  };

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    if (files.length === 0) return;
    await processFiles(files);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const handlePaste = async (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const items = Array.from(e.clipboardData.items);
    for (const item of items) {
      if (item.type.indexOf("image") !== -1) {
        const file = item.getAsFile();
        if (file) {
          e.preventDefault();
          await processFiles([file]);
          break;
        }
      }
    }
  };

  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    const files = Array.from(e.dataTransfer.files || []);
    if (files.length > 0) await processFiles(files);
  };

  const removeImage = (filename: string) => {
    setAttachedImages((prev) => prev.filter((i) => i.filename !== filename));
    textareaRef.current?.focus();
  };

  const handleTextChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setText(e.target.value);
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 180)}px`;
    }
  };

  const atLimit = attachedImages.length >= maxImages;

  return (
    <div className="input-area-wrapper">
      <div
        className="input-box"
        onDrop={handleDrop}
        onDragOver={(e) => e.preventDefault()}
        onClick={() => textareaRef.current?.focus()}
      >
        {attachedImages.length > 0 && (
          <div className="image-preview-bar" onClick={(e) => e.stopPropagation()}>
            {attachedImages.map((img) => (
              <div key={img.filename} className="preview-thumb-container">
                <img src={img.url} alt="Attached Preview" className="preview-thumb" />
                <button
                  className="remove-thumb-btn"
                  onClick={() => removeImage(img.filename)}
                  title="Remove image"
                >
                  <X size={12} />
                </button>
              </div>
            ))}
            {attachedImages.length >= maxImages && (
              <span className="preview-limit">max {maxImages}</span>
            )}
          </div>
        )}

        {activeSkills.length > 0 && (
          <div className="skills-active-bar" onClick={(e) => e.stopPropagation()}>
            <Wrench size={11} color="var(--accent-primary)" />
            <span style={{ color: "var(--text-faint)" }}>Tools:</span>
            {activeSkills.map((s) => (
              <span key={s} className="skill-tag">
                {skillNames?.[s] || s}
              </span>
            ))}
          </div>
        )}

        <div className="input-row">
          <input
            type="file"
            ref={fileInputRef}
            style={{ display: "none" }}
            accept="image/*"
            multiple
            onChange={handleFileChange}
          />

          <button
            className="action-btn"
            title="Attach image(s) — or paste a screenshot with Ctrl+V"
            onClick={(e) => {
              e.stopPropagation();
              fileInputRef.current?.click();
            }}
            disabled={isUploading || isStreaming || atLimit}
          >
            {isUploading ? <Loader2 size={18} className="animate-spin" /> : <ImageIcon size={18} />}
          </button>

          <textarea
            ref={textareaRef}
            className="chat-textarea"
            placeholder={
              attachedImages.length > 0
                ? "Ask anything about these images… (Shift+Enter for newline)"
                : `Ask ${currentModel} anything… (Shift+Enter for newline)`
            }
            value={text}
            onChange={handleTextChange}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            rows={1}
          />

          {isStreaming ? (
            <button className="send-btn stop" onClick={onStop} title="Stop generating (Esc)">
              <Square size={16} fill="currentColor" />
            </button>
          ) : (
            <button
              className="send-btn"
              onClick={(e) => {
                e.stopPropagation();
                handleSubmit();
              }}
              disabled={isUploading || (!text.trim() && attachedImages.length === 0)}
              title="Send message"
            >
              <ArrowUp size={18} />
            </button>
          )}
        </div>
      </div>

      <div className="input-hints">
        <span>Ctrl+V pastes a screenshot · Esc stops generation</span>
        <span className="input-model-pill" onClick={onOpenModelModal} title="Click to switch LLM model">
          <Cpu size={11} />
          Model: <strong>{currentModel}</strong> (click to change)
        </span>
      </div>
    </div>
  );
};