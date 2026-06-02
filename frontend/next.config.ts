import { dirname } from "node:path"
import { fileURLToPath } from "node:url"
import type { NextConfig } from "next"
import createNextIntlPlugin from 'next-intl/plugin'

const withNextIntl = createNextIntlPlugin('./src/i18n/request.ts')
const frontendRoot = dirname(fileURLToPath(import.meta.url))
const workspaceRoot = dirname(frontendRoot)

const nextConfig: NextConfig = {
  output: "standalone",
  allowedDevOrigins: ['127.0.0.1'],
  images: {
    unoptimized: true,
  },
  turbopack: {
    root: workspaceRoot,
  },
}

export default withNextIntl(nextConfig)
