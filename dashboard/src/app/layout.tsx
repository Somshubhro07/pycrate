import type { Metadata } from "next";
import { Outfit, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { Sidebar } from "@/components/Sidebar";

const outfit = Outfit({
  subsets: ["latin"],
  variable: "--font-sans",
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "PyCrate Dashboard",
  description:
    "Real-time container management dashboard for PyCrate runtime",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={`${outfit.variable} ${jetbrainsMono.variable}`}>
      <body className="flex min-h-screen">
        <Sidebar />
        <main className="flex-1 ml-[72px] lg:ml-[240px] p-6 lg:p-8 relative z-10">
          {children}
        </main>
      </body>
    </html>
  );
}
