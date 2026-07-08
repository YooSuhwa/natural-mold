'use client'

// Last-resort boundary: this replaces the crashed root layout, so the
// next-intl provider is unavailable — copy stays static English (the base
// i18n guard only flags Korean) and the stylesheet is imported directly
// because the root layout's import no longer applies.
import './globals.css'

import { Button } from '@/components/ui/button'

type GlobalErrorProps = {
  readonly error: Error & { digest?: string }
  readonly reset: () => void
}

export default function GlobalError({ reset }: GlobalErrorProps) {
  return (
    <html lang="ko">
      <body>
        <div
          role="alert"
          className="flex min-h-screen flex-col items-center justify-center gap-4 p-6 text-center"
        >
          <div>
            <p className="text-sm font-semibold text-foreground">Something went wrong</p>
            <p className="mt-1 text-sm text-muted-foreground">
              An unexpected error occurred. Please try again.
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={reset}>
            Try again
          </Button>
        </div>
      </body>
    </html>
  )
}
