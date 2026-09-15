import type { NextConfig } from "next";

// Where the browser-facing pages reach the backend. Pages call "/api/...";
// Next rewrites that prefix to the real backend, so the operator never deals
// with two origins and the backend needs no CORS allowances for the UI.
const API_BASE_URL = process.env.API_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${API_BASE_URL}/:path*`,
      },
    ];
  },
};

export default nextConfig;