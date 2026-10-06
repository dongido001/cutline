import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'

describe('image generation API', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('sends the explicitly selected previous-shot reference ID', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ id: 'job-1' }), {
      status: 202,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await api.generateShotImage('shot-3', '16:9', ['character-1'], true, 'reference-2')

    expect(fetchMock).toHaveBeenCalledWith('/api/shots/shot-3/image/generate?aspect_ratio=16%3A9', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        character_ids: ['character-1'],
        refresh_continuity: true,
        image_reference_id: 'reference-2',
        use_default_image_reference: false,
      }),
    })
  })

  it('can skip the automatic previous-shot still', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ id: 'job-2' }), {
      status: 202,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await api.generateShotImage('shot-3', '16:9', [], false, null, false)

    expect(fetchMock).toHaveBeenCalledWith('/api/shots/shot-3/image/generate?aspect_ratio=16%3A9', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        character_ids: [],
        refresh_continuity: false,
        image_reference_id: null,
        use_default_image_reference: false,
      }),
    })
  })
})


describe('publish API', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('queues a rich publish request', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ id: 'job-publish' }), {
      status: 202,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await api.publishProject('project-1', {
      platforms: ['youtube', 'facebook'],
      scope: 'shots',
      variant: 'shorts',
      title: 'Launch',
      description: 'Description',
      tags: ['drama'],
      privacy: { youtube: 'private', facebook: 'self' },
      selected_asset_ids: ['shot-1'],
      made_for_kids: false,
    })

    expect(fetchMock).toHaveBeenCalledWith('/api/projects/project-1/publish', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        platforms: ['youtube', 'facebook'],
        scope: 'shots',
        variant: 'shorts',
        title: 'Launch',
        description: 'Description',
        tags: ['drama'],
        privacy: { youtube: 'private', facebook: 'self' },
        selected_asset_ids: ['shot-1'],
        made_for_kids: false,
      }),
    })
  })
})
