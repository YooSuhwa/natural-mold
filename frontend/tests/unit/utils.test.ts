import { describe, it, expect } from 'vitest'
import { cn } from '@/lib/utils'

describe('cn()', () => {
  it('returns a single class string as-is', () => {
    expect(cn('text-red-500')).toBe('text-red-500')
  })

  it('merges multiple class strings', () => {
    const result = cn('px-4 py-2', 'bg-blue-500', 'text-white')
    expect(result).toContain('px-4')
    expect(result).toContain('py-2')
    expect(result).toContain('bg-blue-500')
    expect(result).toContain('text-white')
  })

  it('handles conditional classes (clsx pattern)', () => {
    const isActive = true
    const isDisabled = false
    const result = cn('base-class', isActive && 'active-class', isDisabled && 'disabled-class')
    expect(result).toContain('base-class')
    expect(result).toContain('active-class')
    expect(result).not.toContain('disabled-class')
  })

  it('resolves tailwind conflicts by keeping the last class', () => {
    const result = cn('px-2', 'px-4')
    expect(result).toBe('px-4')
    expect(result).not.toContain('px-2')
  })
})
