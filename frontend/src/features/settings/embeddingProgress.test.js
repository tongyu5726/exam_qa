import { describe, expect, it } from 'vitest'
import { measuredPercent } from './embeddingProgress.js'

describe('measuredPercent', () => {
  it('keeps unknown progress distinct from zero', () => {
    expect(measuredPercent(null)).toBeNull()
    expect(measuredPercent(undefined)).toBeNull()
    expect(measuredPercent(0)).toBe(0)
  })

  it('accepts measured values within the progress range', () => {
    expect(measuredPercent(42)).toBe(42)
    expect(measuredPercent(120)).toBe(100)
  })
})
