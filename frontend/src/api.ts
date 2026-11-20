export type Metadata = {
  resolution_source: 'LLM_CACHE' | 'LIVE_INFERENCE'
  model: string
  latency_ms: number
  input_tokens: number
  output_tokens: number
  key_id: string | null
  fallback_attempts: number
}

export type ChatResponse = {
  session_id: string
  reply: string
  metadata: Metadata
}

type ChatRequest = {
  session_id: string
  message: string
  system_prompt?: string
}

export type ChatTurn = {
  request: ChatRequest
  requestId: string
  simulation: string
}

export class UnconfirmedDeliveryError extends Error {}

const API_BASE = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000')
  .trim()
  .replace(/\/+$/, '')

export async function checkHealth(signal: AbortSignal) {
  const response = await fetch(`${API_BASE}/health`, { signal, cache: 'no-store' })
  if (!response.ok) return null
  const health = await response.json()
  return health.status === 'ok' && typeof health.keys_configured === 'number'
    ? health.keys_configured as number
    : null
}

export async function sendChat({ request, requestId, simulation }: ChatTurn): Promise<ChatResponse> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    'Idempotency-Key': requestId,
  }
  if (simulation) headers['X-Simulate-Failure'] = simulation

  let response: Response
  try {
    response = await fetch(`${API_BASE}/chat`, {
      method: 'POST',
      headers,
      body: JSON.stringify(request),
    })
  } catch {
    throw new UnconfirmedDeliveryError('The connection dropped before the reply arrived.')
  }

  const result = await response.json().catch(() => null)
  if (response.status === 503 && result?.detail === 'all providers unavailable') {
    throw new Error('All providers are unavailable. Please try again in a moment.')
  }

  if (response.status === 422) {
    const field = Array.isArray(result?.detail) ? result.detail[0]?.loc?.at(-1) : null
    if (field === 'message') throw new Error('Enter a message of 1 to 4,000 characters.')
    if (field === 'system_prompt') throw new Error('Use up to 10,000 characters in the system prompt.')
    if (field === 'session_id') throw new Error('Start a new chat and try again.')
    throw new Error('The server could not read this message. Please try again.')
  }

  if (response.status >= 500) throw new UnconfirmedDeliveryError('The server did not confirm your reply.')
  if (!response.ok) throw new Error('The chat server could not send a reply. Please try again.')
  if (result?.session_id !== request.session_id || !result.metadata || typeof result.reply !== 'string') {
    throw new UnconfirmedDeliveryError('The chat server returned an incomplete reply.')
  }
  return result
}
