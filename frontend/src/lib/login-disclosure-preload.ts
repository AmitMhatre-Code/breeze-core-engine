import type { QueryClient } from "@tanstack/react-query";
import {
  fetchLoginDisclosureCurrent,
  fetchLoginDisclosurePublicCurrent,
  type LoginDisclosureDocument,
} from "@/lib/login-disclosure";

const QUERY_KEY = ["login-disclosure", "current"] as const;

let preloaded: LoginDisclosureDocument | null = null;
let inflight: Promise<LoginDisclosureDocument | null> | null = null;

export function getPreloadedLoginDisclosure(): LoginDisclosureDocument | null {
  return preloaded;
}

export function setPreloadedLoginDisclosure(doc: LoginDisclosureDocument): void {
  preloaded = doc;
}

export function clearPreloadedLoginDisclosure(): void {
  preloaded = null;
  inflight = null;
}

function cachePreloaded(doc: LoginDisclosureDocument, queryClient?: QueryClient): void {
  preloaded = doc;
  queryClient?.setQueryData(QUERY_KEY, doc);
}

/** Preload disclosure text before the user is authenticated (login/challenge flow). */
export function preloadLoginDisclosure(): Promise<LoginDisclosureDocument | null> {
  if (preloaded) return Promise.resolve(preloaded);
  if (inflight) return inflight;

  inflight = fetchLoginDisclosurePublicCurrent()
    .then((doc) => {
      cachePreloaded(doc);
      return doc;
    })
    .catch(() => null)
    .finally(() => {
      inflight = null;
    });

  return inflight;
}

/** Refresh disclosure after authentication and keep React Query in sync. */
export async function preloadLoginDisclosureAuthed(
  queryClient?: QueryClient,
): Promise<LoginDisclosureDocument | null> {
  try {
    const doc = await fetchLoginDisclosureCurrent();
    cachePreloaded(doc, queryClient);
    return doc;
  } catch {
    return preloaded;
  }
}
