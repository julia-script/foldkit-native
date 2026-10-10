// LOCAL PROTOTYPE: whether a mutation batch can keep already-read geometry.
// This neither suppresses a command nor promises native paint completion.
import type { StyleDesc } from '@gpuix/native/host'

// Pinned gpuix 0.10 properties. A future/unknown property fails closed.
const KNOWN = new Set(`display visibility flexDirection flexWrap flexGrow flexShrink flexBasis alignItems alignSelf alignContent justifyContent gap rowGap columnGap gridTemplateColumns gridTemplateRows gridColumnMin gridRowMin width height minWidth minHeight maxWidth maxHeight padding paddingTop paddingRight paddingBottom paddingLeft margin marginTop marginRight marginBottom marginLeft position top right bottom left background backgroundColor color opacity borderWidth borderTopWidth borderRightWidth borderBottomWidth borderLeftWidth borderColor borderRadius borderTopLeftRadius borderTopRightRadius borderBottomLeftRadius borderBottomRightRadius boxShadow fontSize fontFamily fontWeight textAlign lineHeight whiteSpace textOverflow lineClamp textDecoration overflow overflowX overflowY cursor pointerEvents userSelect selectionColor hover active`.split(' '))
const solid = (value: unknown): value is string => typeof value === 'string' && /^#[\da-f]{6}$/i.test(value)
const record = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value)

/** Only native state overrides whose keys cannot move geometry are accepted. */
export const autonomousStyle = (style: StyleDesc | undefined): boolean => {
  if (style === undefined) return true
  for (const [key, value] of Object.entries(style)) {
    if (!KNOWN.has(key)) return true
    if (key !== 'hover' && key !== 'active') continue
    if (!record(value)) return true
    for (const [property, override] of Object.entries(value)) {
      if (!['backgroundColor', 'color', 'borderColor', 'cursor'].includes(property) || typeof override !== 'string') return true
    }
  }
  return false
}

/** Compare the whole replacement style, including removed keys, before overwrite. */
export const paintStyle = (before: StyleDesc | undefined, after: StyleDesc): boolean => {
  if (before === undefined || autonomousStyle(before) || autonomousStyle(after)) return false
  const old = before as Record<string, unknown>
  const next = after as Record<string, unknown>
  for (const key of new Set([...Object.keys(old), ...Object.keys(next)])) {
    const a = old[key]
    const b = next[key]
    // No opaque values, native state overrides or transparent/gradient fills.
    if (key === 'background' || key === 'boxShadow' || key === 'hover' || key === 'active') return false
    if (a !== undefined && (typeof a !== 'string' && (typeof a !== 'number' || !Number.isFinite(a)))) return false
    if (b !== undefined && (typeof b !== 'string' && (typeof b !== 'number' || !Number.isFinite(b)))) return false
    if (a === b) continue
    if (key !== 'backgroundColor' || !solid(a) || !solid(b)) return false
  }
  return true
}

/** Unclassified commands stay counted in pending, so every mixed batch falls back. */
export const paintBatch = () => {
  let eligible = 0
  return {
    style: (safe: boolean) => { if (safe) eligible++ },
    moved: (pending: number, autonomous: boolean) => pending > 0 && (autonomous || pending !== eligible),
    // On failure the real queue remains pending: lose eligibility, never commands.
    reset: () => { eligible = 0 },
  }
}
