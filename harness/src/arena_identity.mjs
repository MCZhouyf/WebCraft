const supported = new Set(['1.16.5', '1.19', '1.21.11'])

export function resetUsername(version) {
  if (!supported.has(version)) throw new Error(`unsupported target ${version}`)
  return `wc_reset_${version.replaceAll('.', '')}`.slice(0, 16)
}
