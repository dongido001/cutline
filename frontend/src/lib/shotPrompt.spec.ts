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

  it('handles partial continuity_snapshot objects without characters array (e.g. demo project shots)', () => {
    const partialSnapshotShot = {
      ...shot,
      continuity_snapshot: {
        action: 'Jasmine makes a pretend searching visor with one hand. Skylar copies her.',
      } as unknown as Shot['continuity_snapshot'],
    } as Shot
    const prompt = buildShotVideoPrompt(partialSnapshotShot, scene, [character], '16:9')
    expect(prompt).toContain('Mara — early 30s, freckled, athletic build')
    expect(prompt).toContain('Mara: "We have to go."')
  })

  it('includes off-screen phone caller voice profile and lip-sync lock when caller speaks through handset', () => {
    const gabe = {
      id: 'gabe',
      name: 'GABE',
      notes: '29 years old. Filipino man.',
      voice_description: 'Steady, resonant 29-year-old Filipino baritone, composed, unhurried delivery',
    } as Character
    const mateo = {
      id: 'mateo',
      name: 'MATEO',
      notes: 'Older Filipino field coordinator.',
      voice_description: 'Deep, weathered older Filipino baritone, low-key coastal delivery',
    } as Character
    const phoneCallShot = {
      ...shot,
      action: "Gabe answers the phone and listens to Mateo's report from the handset before responding.",
      video_prompt: 'Gabe listens silently to Mateo on the phone, then replies: "No. My father can wait."',
      sound: 'Phone connection tone, Mateo filtered phone voice.',
      dialogue: [{ character: 'GABE', text: 'No. My father can wait.' }],
      character_ids: ['gabe'],
    } as Shot
    const prompt = buildShotVideoPrompt(phoneCallShot, scene, [gabe, mateo], '9:16')
    expect(prompt).toContain('MATEO (off-screen phone caller, unseen filtered telephone earpiece voice-over only — never lip-sync on-camera face to this voice): Deep, weathered older Filipino baritone')
    expect(prompt).toContain('Lip-sync lock: MATEO is an unseen off-screen telephone caller heard ONLY as filtered, band-limited earpiece voice-over')
    expect(prompt).toContain('("No. My father can wait.")')
  })

  it('does not treat characters addressed in spoken dialogue as off-screen callers and locks silent on-camera characters', () => {
    const gabe = {
      id: 'gabe',
      name: 'GABE',
      notes: '29 years old. Filipino man.',
      voice_description: 'Steady, resonant 29-year-old Filipino baritone',
    } as Character
    const rosa = {
      id: 'rosa',
      name: 'ROSA',
      notes: '52 years old. Filipina woman.',
      voice_description: 'Gentle, weathered Filipina woman',
    } as Character
    const isabel = {
      id: 'isabel',
      name: 'ISABEL',
      notes: '30-year-old assistant.',
      voice_description: 'Clear, grounded 30-year-old speaking voice',
    } as Character
    const phoneCallShot = {
      ...shot,
      action: 'Gabe holds phone to ear. Rosa watches silently.',
      video_prompt: 'Gabe says, "Isabel, send a car to San Aurelio. I\'m coming to see my father." Rosa watches silently.',
      sound: 'Gabe dialogue, ocean waves.',
      dialogue: [{ character: 'GABE', text: "Isabel, send a car to San Aurelio. I'm coming to see my father." }],
      character_ids: ['gabe', 'rosa'],
    } as Shot
    const prompt = buildShotVideoPrompt(phoneCallShot, scene, [gabe, rosa, isabel], '16:9')
    expect(prompt).not.toContain('ISABEL (off-screen phone caller')
    expect(prompt).not.toContain('Lip-sync lock: ISABEL')
    expect(prompt).toContain("Silent character lock: ROSA's lips and jaw MUST remain completely closed and motionless at all times — do NOT lip-sync ROSA to any speech or audio.")
  })
})
