import React, { useState } from "react";
import { ChevronRight, Wrench, CheckCircle2, AlertCircle, Zap } from "lucide-react";
import type { ToolCall } from "../types";

interface ToolCallPanelProps {
  calls: ToolCall[];
  /** Live results arriving over the stream, keyed by tool call id. */
  live?: Record<string, { ok: boolean; summary: string; duration_ms: number }>;
  liveName?: Record<string, string>;
  running?: boolean;
}

interface Row {
  id: string;
  name: string;
  args?: Record<string, any>;
  ok: boolean;
  cached?: boolean;
  duration_ms?: number;
  error?: string | null;
  summary?: string;
}

const pretty = (v: any): string =>
  typeof v === "string" ? v : JSON.stringify(v, null, 2);

/** Collapsible strip showing which skills the model invoked and how long each took. */
export const ToolCallPanel: React.FC<ToolCallPanelProps> = ({
  calls,
  live,
  liveName,
  running,
}) => {
  const [open, setOpen] = useState(false);

  // Merge persisted calls with anything still streaming in.
  const rows: Row[] = [];

  for (const c of calls || []) {
    rows.push({
      id: c.id ?? c.name,
      name: c.name,
      args: c.args,
      ok: c.ok,
      cached: c.cached,
      duration_ms: c.duration_ms,
      error: c.error,
      summary: c.result ? pretty(c.result) : undefined,
    });
  }

  if (live) {
    for (const [id, r] of Object.entries(live)) {
      if (!rows.some((row) => row.id === id)) {
        rows.push({
          id,
          name: liveName?.[id] ?? id,
          ok: r.ok,
          duration_ms: r.duration_ms,
          summary: r.summary,
        });
      }
    }
  }

  if (rows.length === 0 && !running) return null;

  const totalMs = rows.reduce((n, r) => n + (r.duration_ms || 0), 0);
  const failures = rows.filter((r) => !r.ok).length;

  return (
    <div className={`tool-panel ${open ? "open" : ""}`}>
      <button className="tool-panel-header" onClick={() => setOpen((o) => !o)}>
        <ChevronRight size={13} className={`tool-panel-chevron ${open ? "rotated" : ""}`} />
        {running ? (
          <Wrench size={12} className="spin-icon" />
        ) : (
          <Wrench size={12} />
        )}
        <span>
          {running
            ? "Using tools…"
            : `Used ${rows.length} tool${rows.length === 1 ? "" : "s"}`}
          {!running && totalMs > 0 && (
            <span className="tool-panel-time"> ({totalMs}ms)</span>
          )}
        </span>
        {!running && failures > 0 && (
          <span className="tool-panel-fail">{failures} failed</span>
        )}
      </button>

      {open && (
        <div className="tool-panel-body">
          {rows.map((r) => (
            <div key={r.id} className={`tool-row ${r.ok ? "" : "failed"}`}>
              <div className="tool-row-head">
                {r.ok ? (
                  <CheckCircle2 size={12} className="tool-ok" />
                ) : (
                  <AlertCircle size={12} className="tool-bad" />
                )}
                <code className="tool-row-name">{r.name}</code>
                {r.cached && (
                  <span className="tool-badge cached" title="Served from cache">
                    <Zap size={9} /> cached
                  </span>
                )}
                {r.duration_ms != null && (
                  <span className="tool-row-dur">{r.duration_ms}ms</span>
                )}
              </div>

              {r.args && Object.keys(r.args).length > 0 && (
                <pre className="tool-row-args">{pretty(r.args)}</pre>
              )}
              {r.error && <pre className="tool-row-error">{r.error}</pre>}
              {!r.error && r.summary && (
                <pre className="tool-row-out">{r.summary}</pre>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};