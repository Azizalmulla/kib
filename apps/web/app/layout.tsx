import type { Metadata } from "next";
import { Noto_Naskh_Arabic, Space_Grotesk } from "next/font/google";
import "./globals.css";

const spaceGrotesk = Space_Grotesk({
  subsets: ["latin"],
  variable: "--font-sans"
});

const notoNaskhArabic = Noto_Naskh_Arabic({
  subsets: ["arabic"],
  variable: "--font-arabic"
});

export const metadata: Metadata = {
  title: "KIB Knowledge Copilot",
  description: "Internal knowledge assistant for KIB policies and procedures."
};

export default function RootLayout({
  children
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={`${spaceGrotesk.variable} ${notoNaskhArabic.variable}`}>
        {children}
      </body>
    </html>
  );
}
