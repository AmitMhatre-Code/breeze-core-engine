function asHtmlElement(target: EventTarget | null): HTMLElement | null {
  if (target == null || typeof target !== "object") return null;
  if (!("tagName" in target)) return null;
  return target as HTMLElement;
}

export function isTypingTarget(target: EventTarget | null): boolean {
  const el = asHtmlElement(target);
  if (!el) return false;
  const tag = el.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if (el.isContentEditable) return true;
  return false;
}
