import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async rewrites() {
    const backendUpstream =
      process.env.BACKEND_UPSTREAM_URL ??
      process.env.NEXT_PUBLIC_BACKEND_UPSTREAM_URL ??
      "http://localhost:8000";

    return [
      {
        source: "/auth/:path*",
        destination: `${backendUpstream}/auth/:path*`,
      },
      {
        source: "/icici-return",
        destination: `${backendUpstream}/icici-return`,
      },
      {
        source: "/api/register",
        destination: `${backendUpstream}/api/register`,
      },
      {
        source: "/api/register/:path*",
        destination: `${backendUpstream}/api/register/:path*`,
      },
      {
        source: "/api/settings/:path*",
        destination: `${backendUpstream}/api/settings/:path*`,
      },
      { source: "/home/data", destination: `${backendUpstream}/home/data` },
      {
        source: "/dashboard/vix/:path*",
        destination: `${backendUpstream}/dashboard/vix/:path*`,
      },
      {
        source: "/portfolio/data",
        destination: `${backendUpstream}/portfolio/data`,
      },
      // Do not rewrite GET /portfolio — backend returns 302 → /portfolio (same URL) and breaks
      // when proxied through Next. App Router serves `src/app/portfolio/page.tsx`.
      {
        source: "/order/data/:path*",
        destination: `${backendUpstream}/order/data/:path*`,
      },
      {
        source: "/order",
        destination: `${backendUpstream}/order`,
      },
      {
        source: "/book/:path*",
        destination: `${backendUpstream}/book/:path*`,
      },
      {
        source: "/book",
        destination: `${backendUpstream}/book`,
      },
      {
        source: "/hedge/data",
        destination: `${backendUpstream}/hedge/data`,
      },
      {
        source: "/vertical-spread/data",
        destination: `${backendUpstream}/vertical-spread/data`,
      },
      {
        source: "/uncovered-shorts/data",
        destination: `${backendUpstream}/uncovered-shorts/data`,
      },
      {
        source: "/performance/data",
        destination: `${backendUpstream}/performance/data`,
      },
      {
        source: "/admin/data",
        destination: `${backendUpstream}/admin/data`,
      },
      {
        source: "/admin/tests/:path*",
        destination: `${backendUpstream}/admin/tests/:path*`,
      },
    ];
  },
};

export default nextConfig;
