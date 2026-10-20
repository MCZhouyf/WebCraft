import assert from 'node:assert/strict'
import { resetUsername } from './arena_identity.mjs'

assert.equal(resetUsername('1.16.5'), 'wc_reset_1165')
assert.equal(resetUsername('1.19'), 'wc_reset_119')
assert.equal(resetUsername('1.21.11'), 'wc_reset_12111')
assert.throws(() => resetUsername('invalid'), /unsupported target/)
for (const version of ['1.16.5', '1.19', '1.21.11']) {
  assert.ok(resetUsername(version).length <= 16)
}
console.log('arena identity tests passed')
