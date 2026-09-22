import { describe, expect, it } from 'vitest'
import { buildShotVideoPrompt } from './shotPrompt'
import type { Character, Scene, Shot } from './api'

const shot = {
  id: 'shot-1',
  framing: 'Close-up',
  image_prompt: '35mm film, warm practical light',
  video_prompt: 'A slow push-in as she looks toward the door.',
  action: 'Mara tightens her grip and holds her breath.',
  sound: 'Rain and a distant train horn.',
  dialogue: [{ character: 'Mara', text: 'We have to go.' }],
  character_ids: ['mara'],
} as Shot

const scene = {
  location: 'INT. KITCHEN',
  time_of_day: 'NIGHT',
  action: '',
  set_notes: 'Rain-streaked windows and a chipped wooden counter.',
  lighting_notes: 'Only cool refrigerator light and a dim tungsten practical.',
  wardrobe_notes: 'Faded green cardigan, damp hair.',
  props_notes: 'Red mug remains beside the sink.',
} as Scene

const character = { id: 'mara', name: 'Mara', notes: 'early 30s, freckled, athletic build' } as Character

describe('buildShotVideoPrompt', () => {
  it('combines shot, cast, and continuity into a copy-ready prompt', () => {
    const prompt = buildShotVideoPrompt(shot, scene, [character], '16:9')
    expect(prompt).toContain('Close-up; 16:9 aspect ratio; INT. KITCHEN; NIGHT')
    expect(prompt).toContain('Mara — early 30s, freckled, athletic build')
    expect(prompt).toContain('Mara: "We have to go."')
    expect(prompt).toContain('Set continuity: Rain-streaked windows')
    expect(prompt).toContain('no subtitles, captions, text, logos, watermark')
  })

  it('states when a shot has no spoken dialogue', () => {
    const prompt = buildShotVideoPrompt({ ...shot, dialogue: [] } as Shot, scene, [], '9:16')
    expect(prompt).toContain('Spoken dialogue: No spoken dialogue in this shot.')
  })
})
