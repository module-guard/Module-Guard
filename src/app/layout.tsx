import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = {
  title: {
    default: "ModuleGuard | Terraform Module Intelligence",
    template: "%s | ModuleGuard",
  },
  description:
    "Terraform 보안 설정과 공급망 영향 경로를 코드 근거로 분석하는 플랫폼입니다.",
};
export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ko">
      <body>
        {children}
      </body>
    </html>
  );
}
