export type CopyResult = {
  ok: boolean;
  error?: string;
};

function execCommandCopy(text: string): CopyResult {
  if (typeof document === "undefined") {
    return { ok: false, error: "Clipboard unavailable in this environment." };
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "true");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  textarea.style.top = "0";
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  textarea.setSelectionRange(0, text.length);

  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : "Copy failed" };
  } finally {
    document.body.removeChild(textarea);
  }

  return ok ? { ok: true } : { ok: false, error: "Copy failed" };
}

/** Copy text from a user gesture (button click). Uses execCommand when Clipboard API is unavailable. */
export function copyTextToClipboard(text: string): CopyResult {
  const trimmed = text.trim();
  if (!trimmed) {
    return { ok: false, error: "Nothing to copy" };
  }

  const canUseClipboardApi =
    typeof window !== "undefined" &&
    window.isSecureContext &&
    typeof navigator !== "undefined" &&
    Boolean(navigator.clipboard?.writeText);

  if (canUseClipboardApi) {
    try {
      void navigator.clipboard!.writeText(trimmed).catch(() => {
        execCommandCopy(trimmed);
      });
      return { ok: true };
    } catch {
      return execCommandCopy(trimmed);
    }
  }

  return execCommandCopy(trimmed);
}
