import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // Disable image optimization for Vercel free tier
  images: {
    unoptimized: true,
  },
};

export default nextConfig;
