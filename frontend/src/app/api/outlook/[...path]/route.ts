const DEFAULT_BACKEND_UPSTREAM = "http://localhost:8000";
const DEFAULT_TIMEOUT_MS = 120_000;

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 120;

function getBackendUpstream(): string {
  const upstream =
    process.env.BACKEND_UPSTREAM_URL ??
    process.env.NEXT_PUBLIC_BACKEND_UPSTREAM_URL ??
    DEFAULT_BACKEND_UPSTREAM;
  return upstream.replace(/\/$/, "");
}

function filterRequestHeaders(headers: Headers): Headers {
  const out = new Headers();
  for (const [k, v] of headers.entries()) {
    const key = k.toLowerCase();
    // Skip hop-by-hop headers; allow cookies to pass through.
    if (key === "connection") continue;
    if (key === "keep-alive") continue;
    if (key === "proxy-authenticate") continue;
    if (key === "proxy-authorization") continue;
    if (key === "te") continue;
    if (key === "trailer") continue;
    if (key === "transfer-encoding") continue;
    if (key === "upgrade") continue;
    if (key === "host") continue;
    out.set(k, v);
  }
  return out;
}

function filterResponseHeaders(headers: Headers): Headers {
  const out = new Headers();
  for (const [k, v] of headers.entries()) {
    const key = k.toLowerCase();
    if (key === "connection") continue;
    if (key === "keep-alive") continue;
    if (key === "proxy-authenticate") continue;
    if (key === "proxy-authorization") continue;
    if (key === "te") continue;
    if (key === "trailer") continue;
    if (key === "transfer-encoding") continue;
    if (key === "upgrade") continue;
    out.append(k, v);
  }
  return out;
}

async function proxy(req: Request, ctx: { params: Promise<{ path?: string[] }> }) {
  const { path = [] } = await ctx.params;
  const url = new URL(req.url);
  const upstream = getBackendUpstream();
  const upstreamUrl = new URL(`/api/outlook/${path.map(encodeURIComponent).join("/")}`, upstream);
  upstreamUrl.search = url.search;

  const controller = new AbortController();
  const t = setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS);
  try {
    const upstreamRes = await fetch(upstreamUrl.toString(), {
      method: req.method,
      headers: filterRequestHeaders(req.headers),
      body: req.method === "GET" || req.method === "HEAD" ? undefined : req.body,
      // Required so session cookies flow to/from backend via this same origin.
      credentials: "include",
      redirect: "manual",
      signal: controller.signal,
    });

    return new Response(upstreamRes.body, {
      status: upstreamRes.status,
      statusText: upstreamRes.statusText,
      headers: filterResponseHeaders(upstreamRes.headers),
    });
  } finally {
    clearTimeout(t);
  }
}

export function GET(req: Request, ctx: { params: Promise<{ path?: string[] }> }) {
  return proxy(req, ctx);
}

export function POST(req: Request, ctx: { params: Promise<{ path?: string[] }> }) {
  return proxy(req, ctx);
}

export function PUT(req: Request, ctx: { params: Promise<{ path?: string[] }> }) {
  return proxy(req, ctx);
}

export function PATCH(req: Request, ctx: { params: Promise<{ path?: string[] }> }) {
  return proxy(req, ctx);
}

export function DELETE(req: Request, ctx: { params: Promise<{ path?: string[] }> }) {
  return proxy(req, ctx);
}

