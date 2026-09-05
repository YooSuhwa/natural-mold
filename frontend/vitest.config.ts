import { defineConfig } from 'vitest/config'
import path from 'path'

const maxWorkers = process.env.VITEST_MAX_WORKERS ?? '4'
const coverageDirectory = process.env.MOLDY_VITEST_COVERAGE_DIRECTORY ?? 'coverage'

if (
  process.env.MOLDY_VITEST_COVERAGE_DIRECTORY !== undefined &&
  !path.isAbsolute(coverageDirectory)
) {
  throw new Error('MOLDY_VITEST_COVERAGE_DIRECTORY must be an absolute path')
}

export default defineConfig({
  test: {
    environment: 'jsdom',
    setupFiles: ['./tests/setup.ts'],
    globals: true,
    css: false,
    exclude: ['e2e/**', 'e2e-demo/**', 'node_modules/**'],
    maxWorkers,
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json-summary'],
      reportsDirectory: coverageDirectory,
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/components/ui/**', 'src/app/layout.tsx', 'src/lib/types/**'],
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
})
