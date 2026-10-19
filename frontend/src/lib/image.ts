/** Client-side image preparation. */

/** Longest edge (px) after downscaling. Ollama vision models prefer ~1568. */
export const MAX_EDGE = 1568;

export interface PreparedImage {
  blob: Blob;
  filename: string;
  width: number;
  height: number;
  originalWidth: number;
  originalHeight: number;
  downscaled: boolean;
}

/**
 * Downscale an oversized image in the browser before upload.
 *
 * Without this a 6000x4000 photo becomes ~50MB of base64 JSON in a single
 * POST and every subsequent turn re-encodes it.
 */
export async function prepareImage(
  file: File,
  maxEdge: number = MAX_EDGE
): Promise<PreparedImage> {
  const bitmap = await createImageBitmap(file);
  const { width: ow, height: oh } = bitmap;

  const scale = Math.min(1, maxEdge / Math.max(ow, oh));
  const w = Math.max(1, Math.round(ow * scale));
  const h = Math.max(1, Math.round(oh * scale));
  const downscaled = scale < 1;

  // If downscaling would not help (or canvas fails), send the original.
  if (!downscaled) {
    bitmap.close?.();
    return {
      blob: file,
      filename: file.name,
      width: ow,
      height: oh,
      originalWidth: ow,
      originalHeight: oh,
      downscaled: false,
    };
  }

  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    bitmap.close?.();
    return {
      blob: file,
      filename: file.name,
      width: ow,
      height: oh,
      originalWidth: ow,
      originalHeight: oh,
      downscaled: false,
    };
  }

  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(bitmap, 0, 0, w, h);
  bitmap.close?.();

  const blob = await new Promise<Blob | null>((resolve) =>
    canvas.toBlob(resolve, "image/jpeg", 0.9)
  );

  if (!blob) {
    return {
      blob: file,
      filename: file.name,
      width: ow,
      height: oh,
      originalWidth: ow,
      originalHeight: oh,
      downscaled: false,
    };
  }

  const base = file.name.replace(/\.[^.]+$/, "") || "image";
  return {
    blob,
    filename: `${base}.jpg`,
    width: w,
    height: h,
    originalWidth: ow,
    originalHeight: oh,
    downscaled: true,
  };
}

export function isImageFile(file: File): boolean {
  return file.type.startsWith("image/");
}