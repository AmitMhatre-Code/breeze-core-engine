import { getBackendBaseUrl } from "@/lib/config";
import { handleUnauthorizedApiResponse } from "@/lib/auth-session-expired";

export type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export type SessionPolicy = "default" | "passive";

/** Thrown when the backend returns a non-OK status; includes HTTP status when available. */
export class ApiHttpError extends Error {
  readonly status: number;
  readonly payload: unknown;

  constructor(status: number, message: string, payload: unknown) {
    super(message);
    this.name = "ApiHttpError";
    this.status = status;
    this.payload = payload;
  }
}

export type ApiRequestOpts = {
  signal?: AbortSignal;
  sessionPolicy?: SessionPolicy;
  headers?: Record<string, string>;
};

function normalizeGetOpts(opts?: AbortSignal | ApiRequestOpts): ApiRequestOpts {
  if (opts === undefined) return {};
  if (opts instanceof AbortSignal) return { signal: opts };
  return opts;
}

function formatErrorPayload(payload: unknown): string {
  if (typeof payload === "string") {
    const trimmed = payload.trimStart();
    if (
      trimmed.startsWith("<!DOCTYPE") ||
      trimmed.startsWith("<html") ||
      trimmed.startsWith("<HTML")
    ) {
      return "Request to backend failed. Check logs for details.";
    }
    return payload;
  }
  if (payload && typeof payload === "object") {
    const o = payload as Record<string, unknown>;
    const detail = o.detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object") {
      const d = detail as Record<string, unknown>;
      if (typeof d.message === "string") return d.message;
      if (typeof d.error_code === "string" && typeof d.message === "string") {
        return `${d.error_code}: ${d.message}`;
      }
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
    sessionPolicy?: SessionPolicy;
  } = {},
): Promise<TResponse> {
  const url = new URL(path, getBackendBaseUrl());
  const {
    method = "GET",
    body,
    signal,
    credentials = "include",
    headers: extraHeaders,
    sessionPolicy = "default",
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
    const message = formatErrorPayload(payload);
    if (res.status === 401 && sessionPolicy !== "passive") {
      await handleUnauthorizedApiResponse(path, res.status, message);
    }
    throw new ApiHttpError(res.status, message, payload);
  }

  return payload as TResponse;
}

export const apiClient = {
  get: <TResponse>(path: string, opts?: AbortSignal | ApiRequestOpts) => {
    const { signal, sessionPolicy, headers } = normalizeGetOpts(opts);
    return request<TResponse>(path, { method: "GET", signal, sessionPolicy, headers });
  },
  post: <TResponse, TBody = unknown>(
    path: string,
    body: TBody,
    opts?: { signal?: AbortSignal; headers?: Record<string, string>; sessionPolicy?: SessionPolicy },
  ) =>
    request<TResponse, TBody>(path, {
      method: "POST",
      body,
      signal: opts?.signal,
      headers: opts?.headers,
      sessionPolicy: opts?.sessionPolicy,
    }),
  put: <TResponse, TBody = unknown>(
    path: string,
    body: TBody,
    opts?: { signal?: AbortSignal; headers?: Record<string, string>; sessionPolicy?: SessionPolicy },
  ) =>
    request<TResponse, TBody>(path, {
      method: "PUT",
      body,
      signal: opts?.signal,
      headers: opts?.headers,
      sessionPolicy: opts?.sessionPolicy,
    }),
  patch: <TResponse, TBody = unknown>(
    path: string,
    body: TBody,
    opts?: { signal?: AbortSignal; headers?: Record<string, string>; sessionPolicy?: SessionPolicy },
  ) =>
    request<TResponse, TBody>(path, {
      method: "PATCH",
      body,
      signal: opts?.signal,
      headers: opts?.headers,
      sessionPolicy: opts?.sessionPolicy,
    }),
  delete: <TResponse>(path: string, opts?: AbortSignal | ApiRequestOpts) => {
    const { signal, sessionPolicy, headers } = normalizeGetOpts(opts);
    return request<TResponse>(path, { method: "DELETE", signal, sessionPolicy, headers });
  },
  postForm: async <TResponse>(
    path: string,
    form: FormData,
    opts?: { signal?: AbortSignal; sessionPolicy?: SessionPolicy },
  ): Promise<TResponse> => {
    const url = new URL(path, getBackendBaseUrl());
    const sessionPolicy = opts?.sessionPolicy ?? "default";
    const res = await fetch(url.toString(), {
      method: "POST",
      credentials: "include",
      signal: opts?.signal,
      body: form,
    });
    const isJson = res.headers.get("content-type")?.includes("application/json");
    const payload = isJson ? await res.json() : await res.text();
    if (!res.ok) {
      const message = formatErrorPayload(payload);
      if (res.status === 401 && sessionPolicy !== "passive") {
        await handleUnauthorizedApiResponse(path, res.status, message);
      }
      throw new ApiHttpError(res.status, message, payload);
    }
    return payload as TResponse;
  },
};
