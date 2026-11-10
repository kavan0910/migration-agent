import type { Metadata, Viewport } from "next";
import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import "@fontsource/instrument-serif/400.css";
import "@fontsource/instrument-serif/400-italic.css";
import "./globals.css";
import { Providers } from "@/components/Providers";

export const metadata: Metadata = {
  title: { default: "Migration Agent", template: "%s · Migration Agent" },
  description: "Understand a legacy service before you migrate it. Every claim links to the code that proves it.",
};
export const viewport: Viewport = {
  colorScheme: "light dark",
  themeColor: [{ media: "(prefers-color-scheme: light)", color: "#f5f3ec" }, { media: "(prefers-color-scheme: dark)", color: "#0a0a08" }],
};

const themeScript = `try{var t=localStorage.getItem('ma-theme');if(t==='light'||t==='dark')document.documentElement.setAttribute('data-theme',t)}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body>
        <a href="#main" className="skip-link">Skip to content</a>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
