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

  it('uses the persisted snapshot instead of silently adopting later scene edits', () => {
    const saved = {
      ...shot,
      continuity_snapshot: {
        version: 1,
        character_ids: ['mara'],
        characters: [{ id: 'mara', name: 'Mara', reference_ids: ['ref-1'] }],
        scene: {
          title: 'INT. OLD KITCHEN', location: 'Old kitchen', time_of_day: 'NIGHT',
          set_notes: 'Rainy window', lighting_notes: 'Blue practical',
          wardrobe_notes: 'Green cardigan', props_notes: 'Red mug',
        },
        framing: 'Close-up', action: 'Mara waits.', video_prompt: 'Slow push-in.',
        aspect_ratio: '16:9', image_prompt_base: 'Original frame prompt',
        effective_image_prompt: 'Original frame prompt with saved continuity',
      },
    } as Shot
    const changedScene = { ...scene, location: 'Different location', wardrobe_notes: 'Black coat' }
    const prompt = buildShotVideoPrompt(saved, changedScene, [character], '9:16')
    expect(prompt).toContain('Old kitchen; NIGHT')
    expect(prompt).toContain('Original frame prompt with saved continuity')
    expect(prompt).toContain('Wardrobe continuity: Green cardigan')
    expect(prompt).not.toContain('Different location')
  })

  it('states when a shot has no spoken dialogue', () => {
    const prompt = buildShotVideoPrompt({ ...shot, dialogue: [] } as Shot, scene, [], '9:16')
    expect(prompt).toContain('Spoken dialogue: No spoken dialogue in this shot.')
  })

  it('includes FlowKit-style character voices and direct phone-call voice routing', () => {
    const gabe = {
      id: 'gabe',
      name: 'GABE',
      notes: '29 years old. Filipino man.',
      voice_description: 'Steady, resonant 29-year-old Filipino baritone, composed, unhurried delivery',
    } as Character
    const phoneShot = {
      ...shot,
      action: 'Gabe lowers his phone slightly inside the SUV and speaks.',
      video_prompt: 'Tight interior close-up as Gabe lowers the phone slightly and speaks.',
      dialogue: [{ character: 'GABE', text: 'Four minutes. Wait for the horn.' }],
      character_ids: ['gabe'],
    } as Shot
    const prompt = buildShotVideoPrompt(phoneShot, scene, [gabe], '16:9')
    expect(prompt).toContain('Character voices: GABE: Steady, resonant 29-year-old Filipino baritone, composed, unhurried delivery.')
    expect(prompt).toContain('Voice routing: GABE speaks on camera with their natural, direct, unfiltered chest voice')
  })
})
