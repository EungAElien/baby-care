import type { Metadata } from "next";
import { AppProviders } from "@/components/app-providers";
import { MockSessionProvider } from "@/lib/mock/session";
import "./globals.css";

export const metadata: Metadata = {
  title: "아기 돌봄 도우미",
  description: "아기 돌봄 기록을 위한 웹 앱",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ko">
      <body>
        <AppProviders>
          <MockSessionProvider>{children}</MockSessionProvider>
        </AppProviders>
      </body>
    </html>
  );
}
