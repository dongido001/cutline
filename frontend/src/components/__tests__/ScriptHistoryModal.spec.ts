import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
import ScriptHistoryModal from '@/components/ScriptHistoryModal.vue'
import { api, type ScriptVersion } from '@/lib/api'

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api')
  return { ...actual, api: { ...actual.api, scriptHistory: vi.fn(), scriptVersion: vi.fn(), restoreScriptVersion: vi.fn() } }
})

beforeEach(() => {
  vi.clearAllMocks()
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
})

it('previews an older version without restoring until the explicit restore action', async () => {
  const history = { current_version_id: 'v2', current_fingerprint: 'fingerprint', versions: [
    { id: 'v2', version_number: 2, status: 'review', created_at: '2026-10-07T10:00:00Z' },
    { id: 'v1', version_number: 1, status: 'approved', created_at: '2026-10-06T10:00:00Z' },
  ] }
  vi.mocked(api.scriptHistory).mockResolvedValue(history)
  vi.mocked(api.scriptVersion).mockImplementation(async (id) => ({ id, version_number: id === 'v2' ? 2 : 1,
    source_text: id === 'v2' ? 'Current script' : 'Previous script', scenes: [], suggestions: [], status: 'review',
  } as ScriptVersion))
  vi.mocked(api.restoreScriptVersion).mockResolvedValue({ id: 'v3' } as ScriptVersion)
  const wrapper = mount(ScriptHistoryModal, { props: { show: true, versionId: 'v2', episodeTitle: 'Counting adventure' } })
  await flushPromises()
  const selects = wrapper.findAll('select')
  await selects[0]!.setValue('v1')
  await selects[1]!.setValue('source')
  await flushPromises()
  expect(wrapper.text()).toContain('Previous script')
  expect(api.restoreScriptVersion).not.toHaveBeenCalled()
  await wrapper.get('footer button').trigger('click')
  await flushPromises()
  expect(api.restoreScriptVersion).toHaveBeenCalledWith('v1', history)
  expect(wrapper.emitted('restored')).toHaveLength(1)
  expect(wrapper.emitted('close')).toHaveLength(1)
  wrapper.unmount()
})
