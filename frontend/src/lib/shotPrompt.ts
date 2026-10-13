import type { AspectRatio, Character, Scene, Shot } from './api'

function value(text: string | null | undefined, fallback: string): string {
  const normalized = (text || '').trim()
  return normalized || fallback
}

function stripContradictoryAspectTerms(text: string | null | undefined, aspectRatio: AspectRatio): string {
  let cleaned = (text || '').trim()
  if (!cleaned) return ''
  if (aspectRatio === '16:9') {
    cleaned = cleaned.replace(
      /\b(?:Vertical\s+9\s*:\s*16\s+composition(?:\s+for\s+Shorts)?\.?(?:\s*Keep\s+the\s+main\s+subject\s+inside\s+the\s+central\s+safe\s+area\.?)?|9\s*:\s*16\s+(?:vertical|portrait)(?:\s+(?:aspect\s+ratio|frame|framing|composition))?(?:\s+for\s+Shorts)?|vertical\s+9\s*:\s*16(?:\s+(?:aspect\s+ratio|frame|framing|composition))?(?:\s+for\s+Shorts)?|portrait\s+9\s*:\s*16(?:\s+(?:aspect\s+ratio|frame|framing|composition))?|9\s*:\s*16\s+aspect\s+ratio|for\s+Shorts)\b[,.]?/gi,
      ' ',
    )
  } else if (aspectRatio === '9:16') {
    cleaned = cleaned.replace(
      /\b(?:Horizontal\s+16\s*:\s*9\s+widescreen\s+composition\.?|16\s*:\s*9\s+(?:widescreen|landscape|horizontal)(?:\s+(?:aspect\s+ratio|frame|framing|composition))?|widescreen\s+16\s*:\s*9(?:\s+(?:aspect\s+ratio|frame|framing|composition))?|landscape\s+16\s*:\s*9(?:\s+(?:aspect\s+ratio|frame|framing|composition))?|16\s*:\s*9\s+aspect\s+ratio)\b[,.]?/gi,
      ' ',
    )
  }
  cleaned = cleaned.replace(/\s{2,}/g, ' ').trim()
  return cleaned.replace(/^[,\s;.]+|[,\s]+$/g, '').trim()
}

function cleanDialogueLineText(raw: string | null | undefined): string {
  let cleaned = (raw || '').trim()
  if (!cleaned) return ''
  cleaned = cleaned.replace(/\s*\(\s*Alternative\b[^)]*\)\s*$/i, '').trim()
  const leadParen = cleaned.match(/^\([^)]+\)\s*["'\u201c\u2018]+(.+?)["'\u201d\u2019]*$/)
  if (leadParen?.[1]) {
    cleaned = leadParen[1].trim()
  }
  const splitQuote = cleaned.match(/^(?:\([^)]+\)\s*)?["\u201c]?(.+?)["\u201d]\s+(.+)$/)
  if (splitQuote?.[1]) {
    cleaned = splitQuote[1].trim()
  }
  return cleaned.replace(/^["'\u201c\u201d\u2018\u2019]+|["'\u201c\u201d\u2018\u2019]+$/g, '').trim()
}

function sanitizeSceneNote(raw: string | null | undefined): string {
  let cleaned = (raw || '').trim()
  if (!cleaned) return ''
  cleaned = cleaned.replace(/\s*Pasted text\.?\s*$/i, '')
  cleaned = cleaned.replace(/\s*Every image prompt is the exact starting state[^.]*\.?/gi, '')
  cleaned = cleaned.replace(/\s*\([^)]*(?:->|→|\blater\b)[^)]*\)/gi, '')
  return cleaned.replace(/\s{2,}/g, ' ').trim()
}

function dialogueText(shot: Shot): string {
  const lines = (shot.dialogue || [])
    .map((line: any) => `${value(line.character || line.speaker, "Character")}: "${cleanDialogueLineText(line.text || line.line || line.dialogue)}"`)
    .filter((line) => !line.endsWith('""'))
  return lines.length
    ? lines.join(" ")
    : "No spoken dialogue in this shot. All characters remain completely silent with lips closed — do not generate any speech."
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
  const framing = stripContradictoryAspectTerms(shot.framing?.trim() || snapshot?.framing, aspectRatio)
  const action = stripContradictoryAspectTerms(shot.action?.trim() || snapshot?.action, aspectRatio)
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
    const rawSearchText = `${action || ''}\n${shot.video_prompt || ''}\n${shot.sound || ''}`
    // Strip quoted dialogue strings so names mentioned within dialogue (e.g. "Isabel, send a car...") are not treated as offscreen speakers
    const cleanSearchText = rawSearchText.replace(/["\u201c\u201d][^"\u201c\u201d]*["\u201c\u201d]|'[^']*'/g, ' ')
    for (const c of characters) {
      const canon = (c.name || '').trim().toUpperCase()
      const first = canon.split(/\s+/)[0] || ''
      if (!canon || onCameraNameSet.has(canon) || (first && onCameraNameSet.has(first))) continue
      const escaped = canon.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      const escapedFirst = first.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      const cPattern = `(?:${escaped}${escapedFirst ? `|${escapedFirst}` : ''})`
      const cuePattern = new RegExp(
        `(?:` +
        `\\b${cPattern}(?:'s)?\\s+(?:filtered|phone|handset|earpiece|voice|line|report|speech|audio|call|speaks|says|replies|talking)\\b|` +
        `\\b(?:listens to|hears|answering|on the (?:phone|line|call) with)\\s+${cPattern}\\b|` +
        `\\b(?:filtered|phone|handset|earpiece)\\s+(?:voice|line|speech|audio)\\s+(?:of|from)\\s+${cPattern}\\b|` +
        `\\b${cPattern}\\s+on the (?:phone|line|other end|call)\\b` +
        `)`,
        'i',
      )
      if (cuePattern.test(cleanSearchText) && !offscreenNameSet.has(canon)) {
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
  const location = value(sanitizeSceneNote(continuityScene?.location), 'the established location')
  const time = value(continuityScene?.time_of_day, 'the established time of day')
  const setNote = sanitizeSceneNote(continuityScene?.set_notes)
  const lightingNote = sanitizeSceneNote(continuityScene?.lighting_notes)
  const wardrobeNote = sanitizeSceneNote(continuityScene?.wardrobe_notes)
  const propsNote = sanitizeSceneNote(continuityScene?.props_notes)
  const continuity = [
    setNote ? `Set: ${setNote}` : '',
    lightingNote ? `Lighting: ${lightingNote}` : '',
    wardrobeNote ? `Wardrobe: ${wardrobeNote}` : '',
    propsNote ? `Props: ${propsNote}` : '',
  ].filter(Boolean)

  const onCameraSpeakers: string[] = []
  const onCameraLines: string[] = []
  for (const line of shot.dialogue || []) {
    const rawSpeaker = (line.character || line.speaker || '').trim()
    const text = cleanDialogueLineText(line.text || line.line)
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
    `Style and starting image: ${value(stripContradictoryAspectTerms(snapshot?.effective_image_prompt || shot.image_prompt, aspectRatio), 'cinematic live-action, naturalistic detail')}.`,
    `Character identity and appearance: ${identity}. Do not change faces, age, body, hair, voice, or wardrobe.`,
  ]
  if (voiceEntries.length) {
    lines.push(`Character voices: ${voiceEntries.join('; ')}.`)
  }
  if (isPhoneShot && onCameraSpeakers.length) {
    const speakersLabel = Array.from(new Set(onCameraSpeakers)).join(', ')
    const baseRouting = `Voice routing: ${speakersLabel} speaks on camera with their natural, direct, unfiltered chest voice in the local space; do not apply telephone, radio, or speakerphone filtering to on-camera speech.`
    const silentSpeakers = Array.from(onCameraNameSet).filter((name) => !onCameraSpeakers.some((s) => s.toUpperCase() === name || s.toUpperCase().split(/\s+/)[0] === name))
    const dedupedSilent: string[] = []
    for (const s of silentSpeakers) {
      if (!dedupedSilent.some((existing) => existing === s || existing.split(/\s+/)[0] === s || s.split(/\s+/)[0] === existing)) {
        dedupedSilent.push(s)
      }
    }
    const silentClause = dedupedSilent.length
      ? ` Silent character lock: ${dedupedSilent.join(', ')}'s lips and jaw MUST remain completely closed and motionless at all times — do NOT lip-sync ${dedupedSilent.join(', ')} to any speech or audio.`
      : ''
    if (hasOffscreenPhoneSpeech) {
      const offscreenLabel = offscreenPhoneNames.length ? offscreenPhoneNames.join(', ') : 'the off-screen phone caller'
      const linesClause = onCameraLines.length ? ` (${onCameraLines.map((l) => `"${l}"`).join(', ')})` : ''
      lines.push(
        `${baseRouting} Lip-sync lock: ${offscreenLabel} is an unseen off-screen telephone caller heard ONLY as filtered, band-limited earpiece voice-over — ${speakersLabel}'s lips and jaw MUST remain completely closed and motionless in silent listening while ${offscreenLabel}'s phone line plays, and ${speakersLabel}'s mouth must move ONLY when speaking ${speakersLabel}'s own on-camera dialogue${linesClause}.${silentClause}`,
      )
    } else {
      lines.push(`${baseRouting}${silentClause}`)
    }
  }
  const rawCameraMotion = value(stripContradictoryAspectTerms(shot.video_prompt, aspectRatio), 'controlled cinematic movement that follows the action without abrupt cuts')
  const sanitizedCameraMotion = isPhoneShot
    ? rawCameraMotion.replace(
        /(?:Over the (?:filtered\s+)?telephone\s+earpiece,\s+)?(?:unseen\s+caller\s+)?([A-Z][a-zA-Z0-9_\s'-]+?)(?:'s\s+[^,\n]+?)?\s+(?:speaks\s+through\s+the\s+[^:\n]+?:|says,|delivers[^,\n]*,)\s*["\u201c][^"\u201d]+["\u201d]/gi,
        (_match, caller) =>
          `Over the filtered telephone earpiece, unseen caller ${caller.trim()} speaks while the listener remains completely silent with mouth closed and jaw motionless.`,
      )
    : rawCameraMotion

  lines.push(
    `Shot action and micro-acting: ${value(action, value(stripContradictoryAspectTerms(continuityScene?.action, aspectRatio), 'subtle, motivated performance'))}.`,
    `Camera motion: ${sanitizedCameraMotion}.`,
    `Lighting continuity: ${continuity.find((item) => item.startsWith('Lighting:'))?.slice(10).trim() || `match the ${time} lighting established in the scene`}.`,
    `Set continuity: ${continuity.find((item) => item.startsWith('Set:'))?.slice(5).trim() || `maintain the established ${location} layout`}.`,
    `Wardrobe continuity: ${continuity.find((item) => item.startsWith('Wardrobe:'))?.slice(9).trim() || 'keep every selected character in the same established clothing and accessories'}.`,
    `Prop continuity: ${continuity.find((item) => item.startsWith('Props:'))?.slice(6).trim() || 'keep all established props in their correct positions and states'}.`,
    `Sound: ${value(shot.sound, 'natural scene ambience')}.`,
    `Spoken dialogue: ${dialogueText(shot)}`,
    isPhoneShot
      ? 'Realistic motion guidance: physically plausible weight, timing, eye-lines, hand interaction, cloth and hair movement; natural lip sync exclusively when speaking on-camera dialogue lines, with lips and jaw remaining completely closed and still when listening or silent; no frozen poses or rubbery motion.'
      : 'Realistic motion guidance: physically plausible weight, timing, eye-lines, hand interaction, cloth and hair movement; natural lip sync when dialogue is present; no frozen poses or rubbery motion.',
    'Negative constraints: no subtitles, captions, text, logos, watermark, extra characters, duplicate people, face changes, wardrobe changes, geometry glitches, or unexplained object changes.',
  )
  return lines.join('\n')
}
