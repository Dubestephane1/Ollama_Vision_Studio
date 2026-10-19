import { useCallback, useRef } from "react";

/**
 * Typed payload for every server-sent event emitted by the backend.
 * See Phase 3.4 of the plan for the full protocol.
 */
export interface SSEEvent {
  type:
    | "status"
    | "tool_start"
    | "tool_result"
    | "chunk"
    | "stats"
    | "done"
    | "error";
  [key: string]: any;
}

/**
 * Incremental SSE frame parser.
 *
 * The critical detail: a frame can be split across any number of network
 * chunks, so we must retain the incomplete tail between calls. Splitting each
 * chunk on "\n" without carry-over silently drops tokens.
 */
export function createSSEParser<T = SSEEvent>(onEvent: (e: T) => void) {
  let buffer = "";

  const consume = (frame: string) => {
    for (const line of frame.split("\n")) {
      if (!line.startsWith("data:")) continue;
      const raw = line.slice(5).trim();
      if (!raw) continue;
      try {
        onEvent(JSON.parse(raw));
      } catch {
        // Ignore malformed frames rather than killing the stream.
      }
    }
  };

  return {
    push(text: string) {
      buffer += text;
      const frames = buffer.split("\n\n");
      // The last element is either empty or an incomplete frame - keep it.
      buffer = frames.pop() ?? "";
      for (const frame of frames) consume(frame);
    },
    /** Parse any trailing frame that never got its terminating \n\n. */
    flush() {
      if (buffer.trim()) consume(buffer);
      buffer = "";
    },
  };
}

/**
 * Read a Response body as SSE events, honouring an AbortSignal.
 */
export async function readSSE<T = SSEEvent>(
  res: Response,
  onEvent: (e: T) => void,
  signal?: AbortSignal
): Promise<void> {
  if (!res.body) throw new Error("Response has no readable body.");

  const reader = res.body.getReader();
  // NOTE: { stream: true } is mandatory - without it multi-byte UTF-8
  // characters get split across chunks and render as mojibake.
  const decoder = new TextDecoder("utf-8");
  const parser = createSSEParser(onEvent);

  const abortHandler = () => reader.cancel().catch(() => {});
  signal?.addEventListener("abort", abortHandler);

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      parser.push(decoder.decode(value, { stream: true }));
    }
    parser.flush();
  } finally {
    signal?.removeEventListener("abort", abortHandler);
    reader.releaseLock?.();
  }
}

/**
 * Convenience hook wrapper: keeps one AbortController per request so the
 * caller can stop generation at any time.
 */
export function useAbortableSSE() {
  const controllerRef = useRef<AbortController | null>(null);

  const start = useCallback(
    async (url: string, body: unknown, onEvent: (e: SSEEvent) => void) => {
      controllerRef.current?.abort();
      const controller = new AbortController();
      controllerRef.current = controller;

      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: controller.signal,
      });

      if (!res.ok) throw new Error(`Server returned ${res.status}`);
      await readSSE(res, onEvent, controller.signal);
    },
    []
  );

  const abort = useCallback(() => {
    controllerRef.current?.abort();
    controllerRef.current = null;
  }, []);

  return { start, abort };
}