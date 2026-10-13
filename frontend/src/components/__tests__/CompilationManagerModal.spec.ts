import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import CompilationManagerModal from '@/components/CompilationManagerModal.vue'
import { useStudioStore } from '@/stores/studio'
import type { ProjectDetail } from '@/lib/api'

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

const mockProject: ProjectDetail = {
  id: 'proj-123',
  title: 'Test Show',
  status: 'ready',
  aspect_ratio: '16:9',
  target_duration_seconds: 60,
  text_provider: 'mock',
  episode_label: 'Episode',
  created_at: '2026-10-10T10:00:00Z',
  updated_at: '2026-10-10T10:00:00Z',
  current_episode_id: 'ep-1',
  current_version: null,
  final_cut: null,
  variant_exports: [],
  characters: [],
  publishes: [],
  episodes: [
    { id: 'ep-1', project_id: 'proj-123', episode_number: 1, title: 'Episode 1', status: 'ready', shot_count: 5, duration_ms: 25000, created_at: '', updated_at: '' },
    { id: 'ep-2', project_id: 'proj-123', episode_number: 2, title: 'Episode 2', status: 'ready', shot_count: 6, duration_ms: 30000, created_at: '', updated_at: '' },
    { id: 'ep-3', project_id: 'proj-123', episode_number: 3, title: 'Episode 3', status: 'ready', shot_count: 4, duration_ms: 20000, created_at: '', updated_at: '' },
    { id: 'ep-4', project_id: 'proj-123', episode_number: 4, title: 'Episode 4', status: 'ready', shot_count: 5, duration_ms: 25000, created_at: '', updated_at: '' },
    { id: 'ep-5', project_id: 'proj-123', episode_number: 5, title: 'Episode 5', status: 'ready', shot_count: 5, duration_ms: 25000, created_at: '', updated_at: '' },
  ],
  compilations: [
    {
      id: 'comp-1',
      project_id: 'proj-123',
      title: 'Batch 1-3',
      description: 'First three episodes',
      episode_ids: ['ep-1', 'ep-2', 'ep-3'],
      settings: {},
      created_at: '2026-10-10T10:00:00Z',
      updated_at: '2026-10-10T10:00:00Z',
      clip_count: 15,
      duration_ms: 75000,
    },
  ],
}

it('allows selecting episodes via preset and creates a new compilation box', async () => {
  const store = useStudioStore()
  store.currentProject = mockProject

  const createSpy = vi.spyOn(store, 'createCompilation').mockResolvedValue({
    id: 'comp-new',
    project_id: 'proj-123',
    title: 'Episodes 1–4 Compilation',
    description: '',
    episode_ids: ['ep-1', 'ep-2', 'ep-3', 'ep-4'],
    settings: {},
    created_at: '2026-10-10T10:00:00Z',
    updated_at: '2026-10-10T10:00:00Z',
    clip_count: 20,
    duration_ms: 100000,
  })

  const wrapper = mount(CompilationManagerModal, {
    props: { show: true },
  })
  await flushPromises()

  expect(wrapper.text()).toContain('Multi-Episode Compilation Boxes')

  // Click preset "Episodes 1–4"
  const presetBtns = wrapper.findAll('.preset-btn')
  const ep1To4Btn = presetBtns.find((b) => b.text().includes('Episodes 1–4'))
  expect(ep1To4Btn).toBeDefined()
  await ep1To4Btn!.trigger('click')
  await flushPromises()

  // Verify form title auto-generated
  const titleInput = wrapper.find<HTMLInputElement>('#comp-title')
  expect(titleInput.element.value).toContain('Episodes 1–4')

  // Submit form
  const createBtn = wrapper.find('.primary-btn')
  await createBtn.trigger('click')
  await flushPromises()

  expect(createSpy).toHaveBeenCalledWith(
    expect.objectContaining({
      episode_ids: ['ep-1', 'ep-2', 'ep-3', 'ep-4'],
    })
  )

  expect(wrapper.emitted('open-compilation')).toEqual([['comp-new']])
  expect(wrapper.emitted('close')).toHaveLength(1)
  wrapper.unmount()
})

it('opens an existing compilation in Assemble', async () => {
  const store = useStudioStore()
  store.currentProject = mockProject

  const wrapper = mount(CompilationManagerModal, {
    props: { show: true },
  })
  await flushPromises()

  // Switch to Saved Compilations tab
  const tabs = wrapper.findAll('.tab-btn')
  const savedTab = tabs.find((t) => t.text().includes('Saved Compilations'))
  expect(savedTab).toBeDefined()
  await savedTab!.trigger('click')
  await flushPromises()

  expect(wrapper.text()).toContain('Batch 1-3')

  const openBtn = wrapper.find('.open-btn')
  expect(openBtn.exists()).toBe(true)
  await openBtn.trigger('click')
  await flushPromises()

  expect(wrapper.emitted('open-compilation')).toEqual([['comp-1']])
  expect(wrapper.emitted('close')).toHaveLength(1)
  wrapper.unmount()
})
