import { describe, expect, it } from 'vitest'
import { coverUvRect, parseCube } from '@/lib/lutGl'

describe('parseCube', () => {
  it('parses a minimal 2³ cube', () => {
    const text = [
      'TITLE "test"',
      'LUT_3D_SIZE 2',
      'DOMAIN_MIN 0.0 0.0 0.0',
      'DOMAIN_MAX 1.0 1.0 1.0',
      '0 0 0',
      '1 0 0',
      '0 1 0',
      '1 1 0',
      '0 0 1',
      '1 0 1',
      '0 1 1',
      '1 1 1',
    ].join('\n')
    const cube = parseCube(text)
    expect(cube.size).toBe(2)
    expect(cube.data.length).toBe(2 * 2 * 2 * 3)
    expect(cube.data[0]).toBe(0)
    expect(cube.data[3]).toBe(1)
  })

  it('rejects incomplete data', () => {
    expect(() => parseCube('LUT_3D_SIZE 2\n0 0 0\n')).toThrow(/Incomplete/)
  })
})

describe('coverUvRect', () => {
  it('crops the wider source horizontally for a tall destination', () => {
    const uv = coverUvRect(1920, 1080, 1080, 1920, 0.5, 0.5)
    expect(uv.v0).toBeCloseTo(0)
    expect(uv.v1).toBeCloseTo(1)
    expect(uv.u1 - uv.u0).toBeLessThan(1)
    expect((uv.u0 + uv.u1) / 2).toBeCloseTo(0.5)
  })

  it('respects focal X', () => {
    const left = coverUvRect(1920, 1080, 1080, 1920, 0, 0.5)
    const right = coverUvRect(1920, 1080, 1080, 1920, 1, 0.5)
    expect(left.u0).toBeCloseTo(0)
    expect(right.u1).toBeCloseTo(1)
  })
})
