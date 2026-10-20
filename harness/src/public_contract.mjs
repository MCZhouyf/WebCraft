import fs from 'node:fs'

const model = JSON.parse(fs.readFileSync(new URL('../../config/cost_model.json', import.meta.url)))
const secretKeys = /^(?:x|y|z|position|coordinates?|height|world.*|dimension.*|time_?ticks?|entity_?id|protocol.*|registry.*|version|server_brand)$/i
const versionText = /\b(?:1\.16\.5|1\.19(?:\.\d+)?|1\.21\.11)\b/g
const coordinateText = /\b(?:x|y|z|height|world_boundary|entity_id|dimension_height|time_ticks)\s*[:=]\s*-?\d+(?:\.\d+)?/gi
const triplet = /\(\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*\)/g

export function publicValue(value) {
  if (typeof value === 'string') return value.replace(versionText, '[redacted]').replace(coordinateText, '[redacted]').replace(triplet, '[redacted]').replace(/\b(?:protocol|registry)\b[^\n,;]*/gi, '[redacted]')
  if (Array.isArray(value)) return value.map(publicValue)
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).filter(([key]) => !secretKeys.test(key)).map(([key, val]) => [key, publicValue(val)]))
  return value
}

export function primaryCost(method, params) {
  if (method === 'smelt') return Number.isSafeInteger(params.n) && params.n > 0 ? params.n : model.invalid_smelt_n_charge
  if (!(method in model.charges)) throw new Error('unknown semantic method')
  return model.charges[method]
}

export function itemCounts(items) {
  const counts = {}
  for (const item of items) counts[item.name] = (counts[item.name] ?? 0) + item.count
  return counts
}

export function itemFlow(before, after) {
  const consumed = [], produced = []
  for (const name of new Set([...Object.keys(before), ...Object.keys(after)])) {
    const delta = (after[name] ?? 0) - (before[name] ?? 0)
    if (delta < 0) consumed.push({ name, count: -delta })
    if (delta > 0) produced.push({ name, count: delta })
  }
  return { consumed, produced }
}

export function safeError(error) {
  const message = String(error?.message ?? '')
  if (message.startsWith('missing ingredient:')) return 'missing ingredient'
  const allowed = ['unknown resource node', 'resource node is empty', 'layout must contain exactly 9 cells', 'crafting table unavailable', 'furnace unavailable', 'missing smelting input or fuel', 'smelting timeout', 'n must be a positive integer', 'episode closed', 'invalid item identifier']
  return allowed.includes(message) ? message : 'action could not be completed'
}

export function evaluationGate(answerProvider) {
  let finished = false
  return Object.freeze({
    finish_episode() { finished = true },
    async answer_claims(claims) {
      if (!finished) throw new Error('evaluation requires a finished episode')
      if (!answerProvider) throw new Error('agent answer provider is not configured')
      const answers = await answerProvider(structuredClone(claims))
      if (!Array.isArray(answers) || answers.length !== claims.length || answers.some(v => v !== true && v !== false && v !== 'unknown')) throw new Error('invalid claim answers')
      return answers
    }
  })
}
