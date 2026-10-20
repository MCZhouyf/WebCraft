import mineflayer from 'mineflayer'

const targets = {
  '1.16.5': 25571,
  '1.19': 25572,
  '1.21.11': 25573
}

function connect(version, port) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`spawn timeout for ${version}`)), 30_000)
    const bot = mineflayer.createBot({
      host: '127.0.0.1',
      port,
      version,
      auth: 'offline',
      username: 'wc_probe',
      hideErrors: false
    })
    bot.once('spawn', () => {
      clearTimeout(timer)
      const position = bot.entity.position
      const inventoryCount = bot.inventory.items().reduce((sum, item) => sum + item.count, 0)
      console.log(JSON.stringify({
        connected: true,
        version,
        position: { x: position.x, y: position.y, z: position.z },
        inventory_empty: inventoryCount === 0
      }))
      bot.quit('stage0 smoke complete')
      resolve()
    })
    bot.once('error', (error) => {
      clearTimeout(timer)
      reject(error)
    })
    bot.once('kicked', (reason) => {
      clearTimeout(timer)
      reject(new Error(`kicked from ${version}: ${String(reason)}`))
    })
  })
}

const requested = process.argv[2]
const entries = requested ? [[requested, targets[requested]]] : Object.entries(targets)
for (const [version, port] of entries) {
  if (!port) throw new Error(`unknown target ${version}`)
  await connect(version, port)
}
