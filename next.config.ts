import type { NextConfig } from "next";
const nextConfig: NextConfig = {
  output: process.env.NEXT_OUTPUT === "server" ? undefined : "standalone",
  devIndicators: false,
  distDir:
    process.env.NEXT_DIST_DIR ||
    (process.env.NODE_ENV === "development" ? ".next-dev" : ".next"),
  // GitHub 프로필 사진
  images: {
    remotePatterns: [
      { protocol: "https", hostname: "avatars.githubusercontent.com" },
    ],
  },
  // 사이드바 시절 주소. 발표 전까지는 임시(307)로 두고, 이후 영구로 바꾼다.
  async redirects() {
    const moved: [string, string][] = [
      ["/dashboard", "/home"],
      ["/history", "/home"],
      ["/modules", "/explore"],
      ["/modules/:id", "/explore"],
      ["/trusted", "/explore"],
    ];
    return moved.map(([source, destination]) => ({
      source,
      destination,
      permanent: false,
    }));
  },
};
export default nextConfig;
