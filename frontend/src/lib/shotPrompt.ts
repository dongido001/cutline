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
  const voiceEntries: string[] = []
  const seenVoiceNames = new Set<string>()
  const addVoiceForCharacter = (char: Character | undefined, fallbackName?: string, fallbackVoice?: string) => {
    const name = (char?.name || fallbackName || '').trim()
    const key = name.toUpperCase()
    const voice = (char?.voice_description || fallbackVoice || '').trim().replace(/\.$/, '')
    if (!key || seenVoiceNames.has(key) || !voice) return
    seenVoiceNames.add(key)
    voiceEntries.push(`${name}: ${voice}`)
  }
  if (shot.dialogue && shot.dialogue.length > 0) {
    for (const line of shot.dialogue) {
      const speaker = (line.character || line.speaker || '').trim()
      if (!speaker) continue
      const speakerUpper = speaker.toUpperCase()
      const current = characters.find(
        (c) => c.name.toUpperCase() === speakerUpper || c.name.toUpperCase().split(/\s+/)[0] === speakerUpper.split(/\s+/)[0],
      )
      const snapChar = snapshot?.characters.find((c) => c.name.toUpperCase() === speakerUpper)
      const snapVoice = snapshot?.character_voices?.find((v) => v.name.toUpperCase() === speakerUpper)?.voice_description
      addVoiceForCharacter(current, speaker, snapChar?.voice_description || snapVoice)
    }
  } else if (snapshot?.characters.length) {
    for (const item of snapshot.characters) {
      const current = characters.find((c) => c.id === item.id || c.name.toUpperCase() === item.name.toUpperCase())
      const snapVoice = snapshot.character_voices?.find((v) => v.id === item.id || v.name === item.name)?.voice_description
      addVoiceForCharacter(current, item.name, item.voice_description || snapVoice)
    }
  } else {
    for (const current of characters.filter((c) => selected.has(c.id))) {
      addVoiceForCharacter(current)
    }
  }

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

  const combinedText = `${action || ''}\n${shot.video_prompt || ''}\n${framing || ''}`
  const isPhoneShot = /\b(?:phone|cellphone|smartphone|handset|receiver|caller|on the line)\b/i.test(combinedText)
  const onCameraSpeakers = (shot.dialogue || [])
    .map((line) => (line.character || line.speaker || '').trim())
    .filter((speaker) => speaker && !/\((?:O\.?S\.?|V\.?O\.?|ON PHONE|OVER PHONE|THROUGH PHONE|FILTERED)\)/i.test(speaker))
  const lines = [
    `Visual framing: ${value(framing, 'cinematic medium shot')}; ${aspectRatio} aspect ratio; ${location}; ${time}.`,
    `Style and starting image: ${value(snapshot?.effective_image_prompt || shot.image_prompt, 'cinematic live-action, naturalistic detail')}.`,
    `Character identity and appearance: ${identity}. Do not change faces, age, body, hair, voice, or wardrobe.`,
  ]
  if (voiceEntries.length) {
    lines.push(`Character voices: ${voiceEntries.join('; ')}.`)
  }
  if (isPhoneShot && onCameraSpeakers.length) {
    lines.push(
      `Voice routing: ${Array.from(new Set(onCameraSpeakers)).join(', ')} speaks on camera with their natural, direct, unfiltered chest voice in the local space; do not apply telephone, radio, or speakerphone filtering to on-camera speech.`,
    )
  }
  lines.push(
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
  )
  return lines.join('\n')
}
