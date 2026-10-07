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
  const snapshotCharacters = Array.isArray(snapshot?.characters) ? snapshot.characters : []
  const snapshotVoices = Array.isArray(snapshot?.character_voices) ? snapshot.character_voices : []
  const selected = new Set(
    (Array.isArray(snapshot?.character_ids) && snapshot.character_ids.length
      ? snapshot.character_ids
      : shot.character_ids) || [],
  )
  const cast = snapshotCharacters.length
    ? snapshotCharacters.map((character) => {
      const current = characters.find((item) => item.id === character.id)
      const notes = (current?.notes || '').trim()
      return `${character.name}${notes ? ` — ${notes}` : ''}`
    })
    : characters
      .filter((character) => selected.has(character.id))
      .map((character) => {
        const notes = (character.notes || '').trim()
        return `${character.name}${notes ? ` — ${notes}` : ''}`
      })
  const continuityScene = snapshot?.scene ?? scene
  const framing = shot.framing?.trim() || snapshot?.framing
  const action = shot.action?.trim() || snapshot?.action
  const combinedText = `${action || ''}\n${shot.video_prompt || ''}\n${framing || ''}\n${shot.sound || ''}`
  const isPhoneShot = /\b(?:phone|cellphone|smartphone|handset|receiver|caller|on the line)\b/i.test(combinedText)

  const onCameraNameSet = new Set<string>()
  for (const item of snapshotCharacters) {
    const n = (item.name || '').trim().toUpperCase()
    if (n) {
      onCameraNameSet.add(n)
      onCameraNameSet.add(n.split(/\s+/)[0]!)
    }
  }
  if (!onCameraNameSet.size) {
    for (const c of characters) {
      if (selected.has(c.id) && c.name) {
        const n = c.name.trim().toUpperCase()
        onCameraNameSet.add(n)
        onCameraNameSet.add(n.split(/\s+/)[0]!)
      }
    }
  }

  const offscreenPhoneNames: string[] = []
  const offscreenNameSet = new Set<string>()
  if (isPhoneShot) {
    const searchText = `${action || ''}\n${shot.video_prompt || ''}\n${shot.sound || ''}`
    for (const c of characters) {
      const canon = (c.name || '').trim().toUpperCase()
      const first = canon.split(/\s+/)[0] || ''
      if (!canon || onCameraNameSet.has(canon) || (first && onCameraNameSet.has(first))) continue
      const escaped = canon.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      const escapedFirst = first.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      const pattern = new RegExp(`\\b(?:${escaped}${escapedFirst ? `|${escapedFirst}` : ''})\\b`, 'i')
      if (pattern.test(searchText) && !offscreenNameSet.has(canon)) {
        offscreenNameSet.add(canon)
        offscreenPhoneNames.push(canon)
      }
    }
  }

  const voiceEntries: string[] = []
  const seenVoiceNames = new Set<string>()
  const addVoiceForCharacter = (
    char: Character | undefined,
    fallbackName?: string,
    fallbackVoice?: string,
    isOffscreenPhone = false,
  ) => {
    const name = (char?.name || fallbackName || '').trim()
    const key = name.replace(/\s*\([^)]*\)\s*/g, ' ').trim().toUpperCase()
    const voice = (char?.voice_description || fallbackVoice || '').trim().replace(/\.$/, '')
    if (!key || seenVoiceNames.has(key) || !voice) return
    seenVoiceNames.add(key)
    if (isOffscreenPhone || offscreenNameSet.has(key)) {
      voiceEntries.push(
        `${key} (off-screen phone caller, unseen filtered telephone earpiece voice-over only — never lip-sync on-camera face to this voice): ${voice}`,
      )
    } else {
      voiceEntries.push(`${name}: ${voice}`)
    }
  }
  if (shot.dialogue && shot.dialogue.length > 0) {
    for (const line of shot.dialogue) {
      const rawSpeaker = (line.character || line.speaker || '').trim()
      if (!rawSpeaker) continue
      const isOffscreenCue = /\((?:O\.?S\.?|V\.?O\.?|ON PHONE|OVER PHONE|THROUGH PHONE|FILTERED)\)/i.test(rawSpeaker)
      const parts = rawSpeaker.toUpperCase().includes(' AND ')
        ? rawSpeaker.split(/\s+AND\s+/i).map((part) => part.trim()).filter(Boolean)
        : [rawSpeaker]
      for (const speaker of parts) {
        const speakerUpper = speaker.replace(/\s*\([^)]*\)\s*/g, ' ').trim().toUpperCase()
        const isUnselectedSpeaker = Boolean(
          isPhoneShot &&
          onCameraNameSet.size > 0 &&
          !onCameraNameSet.has(speakerUpper) &&
          !onCameraNameSet.has(speakerUpper.split(/\s+/)[0] || ''),
        )
        if ((isOffscreenCue || isUnselectedSpeaker) && speakerUpper && !offscreenNameSet.has(speakerUpper)) {
          offscreenNameSet.add(speakerUpper)
          offscreenPhoneNames.push(speakerUpper)
        }
        const current = characters.find(
          (c) => (c.name || '').toUpperCase() === speakerUpper || (c.name || '').toUpperCase().split(/\s+/)[0] === speakerUpper.split(/\s+/)[0],
        )
        const snapChar = snapshotCharacters.find((c) => (c.name || '').toUpperCase() === speakerUpper)
        const snapVoice = snapshotVoices.find((v) => (v.name || '').toUpperCase() === speakerUpper)?.voice_description
        addVoiceForCharacter(current, speaker, snapChar?.voice_description || snapVoice, isOffscreenCue || isUnselectedSpeaker)
      }
    }
  } else if (snapshotCharacters.length) {
    for (const item of snapshotCharacters) {
      const current = characters.find((c) => c.id === item.id || (c.name || '').toUpperCase() === (item.name || '').toUpperCase())
      const snapVoice = snapshotVoices.find((v) => v.id === item.id || v.name === item.name)?.voice_description
      addVoiceForCharacter(current, item.name, item.voice_description || snapVoice)
    }
  } else {
    for (const current of characters.filter((c) => selected.has(c.id))) {
      addVoiceForCharacter(current)
    }
  }
  for (const offName of offscreenPhoneNames) {
    const current = characters.find(
      (c) => (c.name || '').toUpperCase() === offName || (c.name || '').toUpperCase().split(/\s+/)[0] === offName.split(/\s+/)[0],
    )
    if (current) {
      addVoiceForCharacter(current, offName, current.voice_description, true)
    }
  }

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

  const onCameraSpeakers: string[] = []
  const onCameraLines: string[] = []
  for (const line of shot.dialogue || []) {
    const rawSpeaker = (line.character || line.speaker || '').trim()
    const text = (line.text || line.line || '').trim()
    if (!rawSpeaker || !text) continue
    if (/\((?:O\.?S\.?|V\.?O\.?|ON PHONE|OVER PHONE|THROUGH PHONE|FILTERED)\)/i.test(rawSpeaker)) continue
    const canon = rawSpeaker.replace(/\s*\([^)]*\)\s*/g, ' ').trim().toUpperCase()
    if (offscreenNameSet.has(canon)) continue
    if (!onCameraSpeakers.includes(rawSpeaker)) onCameraSpeakers.push(rawSpeaker)
    if (!onCameraLines.includes(text)) onCameraLines.push(text)
  }
  const hasOffscreenPhoneSpeech =
    offscreenPhoneNames.length > 0 ||
    /\b(?:filtered\s+(?:phone\s+|handset\s+)?(?:voice|line|audio)|handset\s+audio|off-screen\s+phone\s+voice|(?:assistant|caller)\s+says\s*,)/i.test(
      `${shot.video_prompt || ''}\n${shot.sound || ''}`,
    )

  const lines = [
    `Visual framing: ${value(framing, 'cinematic medium shot')}; ${aspectRatio} aspect ratio; ${location}; ${time}.`,
    `Style and starting image: ${value(snapshot?.effective_image_prompt || shot.image_prompt, 'cinematic live-action, naturalistic detail')}.`,
    `Character identity and appearance: ${identity}. Do not change faces, age, body, hair, voice, or wardrobe.`,
  ]
  if (voiceEntries.length) {
    lines.push(`Character voices: ${voiceEntries.join('; ')}.`)
  }
  if (isPhoneShot && onCameraSpeakers.length) {
    const speakersLabel = Array.from(new Set(onCameraSpeakers)).join(', ')
    const baseRouting = `Voice routing: ${speakersLabel} speaks on camera with their natural, direct, unfiltered chest voice in the local space; do not apply telephone, radio, or speakerphone filtering to on-camera speech.`
    if (hasOffscreenPhoneSpeech) {
      const offscreenLabel = offscreenPhoneNames.length ? offscreenPhoneNames.join(', ') : 'the off-screen phone caller'
      const linesClause = onCameraLines.length ? ` (${onCameraLines.map((l) => `"${l}"`).join(', ')})` : ''
      lines.push(
        `${baseRouting} Lip-sync lock: ${offscreenLabel} is an unseen off-screen telephone caller heard ONLY as filtered, band-limited earpiece voice-over — ${speakersLabel}'s lips and jaw MUST remain completely closed and motionless in silent listening while ${offscreenLabel}'s phone line plays, and ${speakersLabel}'s mouth must move ONLY when speaking ${speakersLabel}'s own on-camera dialogue${linesClause}.`,
      )
    } else {
      lines.push(baseRouting)
    }
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
