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
        both_aspects: false,
        generate_video: false,
        include_cast_references: false,
        chatgpt_dual: false,
        lock_blocking: false,
        focal_character_id: null,
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
        both_aspects: false,
        generate_video: false,
        include_cast_references: false,
        chatgpt_dual: false,
        lock_blocking: false,
        focal_character_id: null,
      }),
    })
  })

  it('sends chatgpt_dual flag when chatgptDual is requested', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ id: 'job-3' }), {
      status: 202,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await api.generateShotImage('shot-3', '16:9', [], false, null, false, { chatgptDual: true })

    expect(fetchMock).toHaveBeenCalledWith('/api/shots/shot-3/image/generate?aspect_ratio=16%3A9', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        character_ids: [],
        refresh_continuity: false,
        image_reference_id: null,
        use_default_image_reference: false,
        both_aspects: false,
        generate_video: false,
        include_cast_references: false,
        chatgpt_dual: true,
        lock_blocking: false,
        focal_character_id: null,
      }),
    })
  })

  it('sends lock_blocking and focal_character_id when requested', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ id: 'job-4' }), {
      status: 202,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await api.generateShotImage('shot-3', '16:9', ['c1'], false, 'ref-1', false, {
      lockBlocking: true,
      focalCharacterId: 'c1',
    })

    expect(fetchMock).toHaveBeenCalledWith('/api/shots/shot-3/image/generate?aspect_ratio=16%3A9', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        character_ids: ['c1'],
        refresh_continuity: false,
        image_reference_id: 'ref-1',
        use_default_image_reference: false,
        both_aspects: false,
        generate_video: false,
        include_cast_references: false,
        chatgpt_dual: false,
        lock_blocking: true,
        focal_character_id: 'c1',
      }),
    })
  })

  it('calls deriveTakeFromCrop endpoint', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ id: 'shot-3' }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await api.deriveTakeFromCrop('shot-3', {
      aspect_ratio: '16:9',
      x: 0.1,
      y: 0.2,
      width: 0.5,
      height: 0.6,
      label: 'Close-up Tobi',
    })

    expect(fetchMock).toHaveBeenCalledWith('/api/shots/shot-3/derive-take', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        aspect_ratio: '16:9',
        x: 0.1,
        y: 0.2,
        width: 0.5,
        height: 0.6,
        label: 'Close-up Tobi',
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
