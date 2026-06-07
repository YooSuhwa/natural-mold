import type { Metadata } from 'next'
import localFont from 'next/font/local'
import { ThemeProvider } from 'next-themes'
import { NextIntlClientProvider } from 'next-intl'
import { getLocale, getTimeZone, getTranslations } from 'next-intl/server'
import { Toaster } from 'sonner'
import './globals.css'
import '@/components/agent-prism/theme/theme.css'
import { AppLayout } from '@/components/layout/app-layout'
import { ROOT_MESSAGE_NAMESPACES, getScopedMessages } from '@/i18n/scoped-messages'

const pretendard = localFont({
  src: 'fonts/PretendardVariable.woff2',
  variable: '--font-sans',
  display: 'swap',
  weight: '45 920',
})

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations('metadata')
  return {
    title: t('title'),
    description: t('description'),
  }
}

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  const [locale, messages, timeZone] = await Promise.all([
    getLocale(),
    getScopedMessages(ROOT_MESSAGE_NAMESPACES),
    getTimeZone(),
  ])

  return (
    <html
      lang={locale}
      className={`${pretendard.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <body className="h-full bg-background font-sans text-foreground">
        <NextIntlClientProvider messages={messages} timeZone={timeZone}>
          <ThemeProvider
            attribute="class"
            defaultTheme="system"
            enableSystem
            disableTransitionOnChange
          >
            <AppLayout>{children}</AppLayout>
            <Toaster
              position="top-center"
              richColors
              toastOptions={{
                style: {
                  fontSize: '15px',
                  padding: '16px 24px',
                },
              }}
            />
          </ThemeProvider>
        </NextIntlClientProvider>
      </body>
    </html>
  )
}
