import { dirname } from "node:path"
import { fileURLToPath } from "node:url"
import type { NextConfig } from "next"
import createNextIntlPlugin from 'next-intl/plugin'

const withNextIntl = createNextIntlPlugin('./src/i18n/request.ts')
const frontendRoot = dirname(fileURLToPath(import.meta.url))

const nextConfig: NextConfig = {
  output: "standalone",
  allowedDevOrigins: ['127.0.0.1'],
  turbopack: {
    root: frontendRoot,
  },
}

export default withNextIntl(nextConfig)
