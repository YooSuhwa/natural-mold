export const USAGE_PRESETS = ['7d', '30d', '90d', 'custom'] as const
export type UsagePreset = (typeof USAGE_PRESETS)[number]
