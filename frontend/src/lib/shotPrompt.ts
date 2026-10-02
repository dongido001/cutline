import type { AspectRatio, Character, Scene, Shot } from './api'

function value(text: string | null | undefined, fallback: string): string {
  const normalized = (text || '').trim()
  return normalized || fallback
}

function dialogueText(shot: Shot): string {
  const lines = (shot.dialogue || [])
    .map((line: any) => `${value(line.character || line.speaker, "Character")}: "${value(line.text || line.line || line.dialogue, "")}"`)
    .filter((line) => !line.endsWith('""'))
  return lines.length ? lines.join(" ") : "No spoken dialogue in this shot."
}

/**
 * Produces a stable, copy-ready prompt without changing the prompts used by
 * image/video generation. Keep this composed from persisted screenplay data.
 */
export function buildShotVideoPrompt(
  shot: Shot,
  scene: Scene | null | undefined,
  characters: Character[],
  aspectRatio: AspectRatio,
): string {
  const snapshot = shot.continuity_snapshot
  const selected = new Set(snapshot?.character_ids || shot.character_ids || [])
  const cast = snapshot?.characters.length
    ? snapshot.characters.map((character) => {
      const current = characters.find((item) => item.id === character.id)
      return `${character.name}${current?.notes.trim() ? ` — ${current.notes.trim()}` : ''}`
    })
    : characters
      .filter((character) => selected.has(character.id))
      .map((character) => `${character.name}${character.notes.trim() ? ` — ${character.notes.trim()}` : ''}`)
  const continuityScene = snapshot?.scene ?? scene
  const framing = snapshot?.framing || shot.framing
  const action = snapshot?.action || shot.action
  const identity = cast.length
    ? cast.join('; ')
    : 'Preserve the exact identity and appearance established by the shot description.'
  const location = value(continuityScene?.location, 'the established location')
  const time = value(continuityScene?.time_of_day, 'the established time of day')
  const continuity = [
    continuityScene?.set_notes?.trim() ? `Set: ${continuityScene.set_notes.trim()}` : '',
    continuityScene?.lighting_notes?.trim() ? `Lighting: ${continuityScene.lighting_notes.trim()}` : '',
    continuityScene?.wardrobe_notes?.trim() ? `Wardrobe: ${continuityScene.wardrobe_notes.trim()}` : '',
    continuityScene?.props_notes?.trim() ? `Props: ${continuityScene.props_notes.trim()}` : '',
  ].filter(Boolean)

  return [
    `Visual framing: ${value(framing, 'cinematic medium shot')}; ${aspectRatio} aspect ratio; ${location}; ${time}.`,
    `Style and starting image: ${value(snapshot?.effective_image_prompt || shot.image_prompt, 'cinematic live-action, naturalistic detail')}.`,
    `Character identity and appearance: ${identity}. Do not change faces, age, body, hair, or wardrobe.`,
    `Shot action and micro-acting: ${value(action, value(continuityScene?.action, 'subtle, motivated performance'))}.`,
    `Camera motion: ${value(shot.video_prompt, 'controlled cinematic movement that follows the action without abrupt cuts')}.`,
    `Lighting continuity: ${continuity.find((item) => item.startsWith('Lighting:'))?.slice(10).trim() || `match the ${time} lighting established in the scene`}.`,
    `Set continuity: ${continuity.find((item) => item.startsWith('Set:'))?.slice(5).trim() || `maintain the established ${location} layout`}.`,
    `Wardrobe continuity: ${continuity.find((item) => item.startsWith('Wardrobe:'))?.slice(9).trim() || 'keep every selected character in the same established clothing and accessories'}.`,
    `Prop continuity: ${continuity.find((item) => item.startsWith('Props:'))?.slice(6).trim() || 'keep all established props in their correct positions and states'}.`,
    `Sound: ${value(shot.sound, 'natural scene ambience')}.`,
    `Spoken dialogue: ${dialogueText(shot)}`,
    'Realistic motion guidance: physically plausible weight, timing, eye-lines, hand interaction, cloth and hair movement; natural lip sync when dialogue is present; no frozen poses or rubbery motion.',
    'Negative constraints: no subtitles, captions, text, logos, watermark, extra characters, duplicate people, face changes, wardrobe changes, geometry glitches, or unexplained object changes.',
  ].join('\n')
}
