export function createSessionId() {
  if (globalThis.crypto?.randomUUID) return `s-${globalThis.crypto.randomUUID()}`

  const bytes = new Uint8Array(16)
  if (globalThis.crypto?.getRandomValues) {
    globalThis.crypto.getRandomValues(bytes)
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256)
    }
  }
  return `s-${Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')}`
}
