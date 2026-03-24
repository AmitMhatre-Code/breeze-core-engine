import { getBackendBaseUrl } from "@/lib/config";

export type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

function formatErrorPayload(payload: unknown): string {
  if (typeof payload === "string") return payload;
  if (payload && typeof payload === "object") {
    const o = payload as Record<string, unknown>;
    const detail = o.detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object") {
      const d = detail as Record<string, unknown>;
      if (Array.isArray(d.errors) && d.errors.length) {
        const first = d.errors[0] as Record<string, unknown>;
        return (
          (first.user_message as string) ||
          (first.contents as string) ||
          "Request failed"
        );
      }
    }
    if (typeof o.message === "string") return o.message;
  }
  return "Request to backend failed. Check logs for details.";
}

async function request<TResponse, TBody = unknown>(
  path: string,
  options: {
    method?: HttpMethod;
    body?: TBody;
    signal?: AbortSignal;
    credentials?: RequestCredentials;
    headers?: Record<string, string>;
  } = {},
): Promise<TResponse> {
  const url = new URL(path, getBackendBaseUrl());
  const {
    method = "GET",
    body,
    signal,
    credentials = "include",
    headers: extraHeaders,
  } = options;

  const res = await fetch(url.toString(), {
    method,
    credentials,
    signal,
    headers: {
      "Content-Type": "application/json",
      ...(extraHeaders || {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });

  const isJson = res.headers.get("content-type")?.includes("application/json");
  const payload = isJson ? await res.json() : await res.text();

  if (!res.ok) {
    throw new Error(formatErrorPayload(payload));
  }

  return payload as TResponse;
}

export const apiClient = {
  get: <TResponse>(path: string, signal?: AbortSignal) =>
    request<TResponse>(path, { method: "GET", signal }),
  post: <TResponse, TBody = unknown>(
    path: string,
    body: TBody,
    opts?: { signal?: AbortSignal; headers?: Record<string, string> },
  ) =>
    request<TResponse, TBody>(path, {
      method: "POST",
      body,
      signal: opts?.signal,
      headers: opts?.headers,
    }),
  postForm: async <TResponse>(
    path: string,
    form: FormData,
    opts?: { signal?: AbortSignal },
  ): Promise<TResponse> => {
    const url = new URL(path, getBackendBaseUrl());
    const res = await fetch(url.toString(), {
      method: "POST",
      credentials: "include",
      signal: opts?.signal,
      body: form,
    });
    const isJson = res.headers.get("content-type")?.includes("application/json");
    const payload = isJson ? await res.json() : await res.text();
    if (!res.ok) {
      throw new Error(formatErrorPayload(payload));
    }
    return payload as TResponse;
  },
};
