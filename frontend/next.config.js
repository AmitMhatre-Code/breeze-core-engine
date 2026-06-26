const path = require("node:path");
const { loadEnvConfig } = require("@next/env");

// Merge repo-root `.env` for local dev; skip during Docker image build (DOCKER_BUILD=1).
if (process.env.DOCKER_BUILD !== "1") {
  loadEnvConfig(path.join(__dirname, ".."));
}

/** @type {import("next").NextConfig} */
const nextConfig = {
  env: {
    NEXT_PUBLIC_SALES_EMAIL:
      process.env.SALES_EMAIL || process.env.NEXT_PUBLIC_SALES_EMAIL || "",
  },
  output: "standalone",
  experimental: {
    proxyClientMaxBodySize: "128mb",
  },
  async redirects() {
    return [
      {
        source: "/strategy-builder-new",
        destination: "/strategy-builder",
        permanent: true,
      },
    ];
  },
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
        source: "/api/terms/:path*",
        destination: `${backendUpstream}/api/terms/:path*`,
      },
      {
        source: "/api/settings/:path*",
        destination: `${backendUpstream}/api/settings/:path*`,
      },
      {
        source: "/api/outlook/:path*",
        destination: `${backendUpstream}/api/outlook/:path*`,
      },
      { source: "/home/data", destination: `${backendUpstream}/home/data` },
      {
        source: "/deployment/license-status",
        destination: `${backendUpstream}/deployment/license-status`,
      },
      {
        source: "/dashboard/vix",
        destination: `${backendUpstream}/dashboard/vix`,
      },
      {
        source: "/dashboard/bootstrap",
        destination: `${backendUpstream}/dashboard/bootstrap`,
      },
      {
        source: "/dashboard/vix/:path*",
        destination: `${backendUpstream}/dashboard/vix/:path*`,
      },
      {
        source: "/portfolio/data",
        destination: `${backendUpstream}/portfolio/data`,
      },
      {
        source: "/portfolio/hedge-candidates",
        destination: `${backendUpstream}/portfolio/hedge-candidates`,
      },
      // Do not rewrite GET /portfolio — backend returns 302 → /portfolio (same URL) and breaks
      // when proxied through Next. App Router serves `src/app/portfolio/page.tsx`.
      {
        source: "/order/data/:path*",
        destination: `${backendUpstream}/order/data/:path*`,
      },
      {
        source: "/order/break-chunk-defaults",
        destination: `${backendUpstream}/order/break-chunk-defaults`,
      },
      {
        source: "/order/break-chunk",
        destination: `${backendUpstream}/order/break-chunk`,
      },
      {
        source: "/order/break-finalize",
        destination: `${backendUpstream}/order/break-finalize`,
      },
      {
        source: "/order",
        destination: `${backendUpstream}/order`,
      },
      {
        source: "/book/data",
        destination: `${backendUpstream}/book/data`,
      },
      {
        source: "/book/cancel-one",
        destination: `${backendUpstream}/book/cancel-one`,
      },
      {
        source: "/book/cancel-commit",
        destination: `${backendUpstream}/book/cancel-commit`,
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
        source: "/uncovered-shorts/scan",
        destination: `${backendUpstream}/uncovered-shorts/scan`,
      },
      {
        source: "/uncovered-shorts/covered-shorts-scan",
        destination: `${backendUpstream}/uncovered-shorts/covered-shorts-scan`,
      },
      {
        source: "/performance/data",
        destination: `${backendUpstream}/performance/data`,
      },
      {
        source: "/strategy-builder/underlyings",
        destination: `${backendUpstream}/strategy-builder/underlyings`,
      },
      {
        source: "/strategy-builder/chain",
        destination: `${backendUpstream}/strategy-builder/chain`,
      },
      {
        source: "/strategy-builder/covered-shorts-scan",
        destination: `${backendUpstream}/strategy-builder/covered-shorts-scan`,
      },
      {
        source: "/strategy-builder/propose-trades",
        destination: `${backendUpstream}/strategy-builder/propose-trades`,
      },
      {
        source: "/strategy-builder/propose-trades/jobs/:path*",
        destination: `${backendUpstream}/strategy-builder/propose-trades/jobs/:path*`,
      },
      {
        source: "/strategy-builder/margin",
        destination: `${backendUpstream}/strategy-builder/margin`,
      },
      {
        source: "/strategy-builder/execute",
        destination: `${backendUpstream}/strategy-builder/execute`,
      },
      {
        source: "/strategy-builder/audit/:path*",
        destination: `${backendUpstream}/strategy-builder/audit/:path*`,
      },
      {
        source: "/admin/data",
        destination: `${backendUpstream}/admin/data`,
      },
      {
        source: "/admin/tests/:path*",
        destination: `${backendUpstream}/admin/tests/:path*`,
      },
      {
        source: "/dev/mock-broker-cookie",
        destination: `${backendUpstream}/dev/mock-broker-cookie`,
      },
    ];
  },
};

module.exports = nextConfig;
