import type { Metadata } from "next"
import { Geist, Geist_Mono } from "next/font/google"
import { ThemeProvider } from "next-themes"
import { Toaster } from "sonner"
import "./globals.css"
import { AppLayout } from "@/components/layout/app-layout"

const geistSans = Geist({
  variable: "--font-sans",
  subsets: ["latin"],
})

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
})

export const metadata: Metadata = {
  title: "Moldy - AI Agent Builder",
  description: "대화로 만드는 AI 에이전트 빌더",
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html
      lang="ko"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <body className="h-full bg-background font-sans text-foreground">
        <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
        <AppLayout>{children}</AppLayout>
        <Toaster
          position="top-center"
          richColors
          toastOptions={{
            style: {
              fontSize: "15px",
              padding: "16px 24px",
            },
          }}
        />
        </ThemeProvider>
      </body>
    </html>
  )
}
