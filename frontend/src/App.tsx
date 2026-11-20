import { useEffect, useRef, useState } from 'react'
import { checkHealth, sendChat, type Metadata } from './api'
import { createSessionId } from './session'

type Message = { id: number; text: string } & (
  | { role: 'user' | 'error' }
  | { role: 'assistant'; metadata: Metadata }
)

const starterPrompts = [
  { label: 'Explain a concept', description: 'What is response caching?', icon: 'book' },
  { label: 'Try a memory test', description: 'Remember that my project is named Orion.', icon: 'memory' },
  { label: 'Compare two ideas', description: 'How do retries and failover differ?', icon: 'compare' },
]

function MessageMeta({ metadata }: { metadata: Metadata }) {
  return (
    <dl className="metadata" aria-label="Reply metadata">
      <div>
        <dt>Source</dt>
        <dd
          className={metadata.resolution_source === 'LLM_CACHE' ? 'source source-cache' : 'source'}
          data-testid="meta-source"
        >
          {metadata.resolution_source}
        </dd>
      </div>
      <div><dt>Model</dt><dd data-testid="meta-model">{metadata.model}</dd></div>
      <div><dt>Latency</dt><dd data-testid="meta-latency">{metadata.latency_ms} ms</dd></div>
      <div><dt>Input tokens</dt><dd data-testid="meta-input-tokens">{metadata.input_tokens}</dd></div>
      <div><dt>Output tokens</dt><dd data-testid="meta-output-tokens">{metadata.output_tokens}</dd></div>
      <div><dt>Key</dt><dd data-testid="meta-key-id">{metadata.key_id ?? '—'}</dd></div>
      <div><dt>Fallbacks</dt><dd data-testid="meta-fallbacks">{metadata.fallback_attempts}</dd></div>
    </dl>
  )
}

function ConversationMessage({ message }: { message: Message }) {
  if (message.role === 'user') {
    return (
      <article className="message user-message" data-testid="user-message">
        <span className="speaker">You</span>
        <p className="message-text">{message.text}</p>
      </article>
    )
  }
  if (message.role === 'error') {
    return (
      <aside className="error-message" role="alert" data-testid="error-message">
        <span className="error-icon" aria-hidden="true">!</span>
        <span>{message.text}</span>
      </aside>
    )
  }
  if (message.role === 'assistant') {
    return (
      <article className="message assistant-message" data-testid="assistant-message">
        <header className="speaker"><span className="mini-mark" aria-hidden="true">✦</span> Lyftr</header>
        <p className="message-text">{message.text}</p>
        <MessageMeta metadata={message.metadata} />
      </article>
    )
  }
}

export function App() {
  const [sessionId, setSessionId] = useState(createSessionId)
  const [messages, setMessages] = useState<Message[]>([])
  const [draft, setDraft] = useState('')
  const [systemPrompt, setSystemPrompt] = useState('')
  const [simulation, setSimulation] = useState('')
  const [pending, setPending] = useState(false)
  const [keyCount, setKeyCount] = useState<number | null | undefined>(undefined)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [sessionCopied, setSessionCopied] = useState(false)
  const editor = useRef<HTMLTextAreaElement>(null)
  const end = useRef<HTMLDivElement>(null)
  const inFlight = useRef(false)
  const messageId = useRef(0)
  const characterCount = Array.from(draft.trim()).length

  useEffect(() => {
    const controller = new AbortController()
    checkHealth(controller.signal).then(setKeyCount).catch(() => {
      if (!controller.signal.aborted) setKeyCount(null)
    })
    return () => controller.abort()
  }, [])

  useEffect(() => {
    if (messages.length) end.current?.scrollIntoView({ block: 'nearest' })
  }, [messages, pending])

  function startNewChat() {
    if (inFlight.current) return
    setSessionId(createSessionId())
    setMessages([])
    setDraft('')
    setSystemPrompt('')
    setSimulation('')
    setSessionCopied(false)
    editor.current?.focus()
  }

  async function copySession() {
    try {
      await navigator.clipboard.writeText(sessionId)
      setSessionCopied(true)
    } catch {
      window.prompt('Copy the session ID', sessionId)
    }
  }

  function showError(text: string) {
    const id = ++messageId.current
    setMessages((current) => [...current, { id, role: 'error', text }])
  }

  async function submit() {
    if (inFlight.current) return
    const text = draft.trim()
    setDraft('')
    if (!text) {
      showError('Enter a message to start chatting.')
      editor.current?.focus()
      return
    }

    inFlight.current = true
    setPending(true)
    setSimulation('')
    const id = ++messageId.current
    setMessages((current) => [...current, { id, role: 'user', text }])

    try {
      const request = {
        session_id: sessionId,
        message: text,
        ...(systemPrompt.trim() ? { system_prompt: systemPrompt } : {}),
      }
      const response = await sendChat(request, simulation)
      const replyId = ++messageId.current
      setMessages((current) => [...current, {
        id: replyId,
        role: 'assistant',
        text: response.reply,
        metadata: response.metadata,
      }])
    } catch (error) {
      showError(error instanceof Error ? error.message : 'The message could not be sent.')
    } finally {
      inFlight.current = false
      setPending(false)
    }
  }

  return (
    <div className="app-shell">
      <aside aria-label="Chat settings" className="sidebar">
        <div className="sidebar-top">
          <div className="wordmark">
            <span className="brand-mark" aria-hidden="true">✦</span>
            <span>Lyftr<span className="wordmark-light"> Chat</span></span>
          </div>
          <button className="new-chat" type="button" onClick={startNewChat} disabled={pending}>
            <span aria-hidden="true">+</span> New chat
          </button>
          <button
            className="settings-toggle"
            type="button"
            onClick={() => setSettingsOpen(!settingsOpen)}
            aria-controls="session-settings"
            aria-expanded={settingsOpen}
          >
            Settings
          </button>
        </div>

        <div id="session-settings" className={settingsOpen ? 'sidebar-settings open' : 'sidebar-settings'}>
          <h2>Session settings</h2>
          <label htmlFor="system-prompt">System prompt</label>
          <textarea
            id="system-prompt"
            data-testid="system-prompt-input"
            rows={4}
            value={systemPrompt}
            onChange={(event) => setSystemPrompt(event.target.value)}
            placeholder="Default assistant instructions"
            aria-describedby="system-prompt-hint"
          />
          <p id="system-prompt-hint" className="field-hint">Saved for this chat after a successful reply.</p>

          <label htmlFor="failure-mode">Simulate failure</label>
          <div className="select-field">
            <select id="failure-mode" value={simulation} onChange={(event) => setSimulation(event.target.value)}>
              <option value="">Off</option>
              <option value="key_1=429">Key 1 · Rate limit (429)</option>
              <option value="key_1=500">Key 1 · Server error (500)</option>
              <option value="*=429">All keys · Rate limit (429)</option>
            </select>
          </div>
          <p className="field-hint">Applies to the next message only.</p>
        </div>

        <div className="sidebar-footer">
          <span className={typeof keyCount === 'number' ? 'status-dot ready' : 'status-dot'} aria-hidden="true" />
          <span>
            {keyCount === undefined ? 'Checking API' : keyCount === null ? 'API unavailable' : 'API connected'}
          </span>
          {typeof keyCount === 'number' && <span className="key-count">{keyCount} {keyCount === 1 ? 'key' : 'keys'}</span>}
        </div>
      </aside>

      <main className="workspace">
        <header className="workspace-header">
          <h1>Conversation</h1>
          <button
            type="button"
            className="session-id"
            aria-label={sessionCopied ? 'Session ID copied' : 'Copy full session ID'}
            title={sessionId}
            onClick={() => void copySession()}
          >
            {sessionCopied ? 'Copied' : 'Session'} <code>{sessionId.slice(0, 10)}</code>
          </button>
        </header>

        <div className="scroll-area">
          {messages.length === 0 ? (
            <section className="empty-state" aria-labelledby="welcome-heading">
              <div className="welcome-mark" aria-hidden="true">✦</div>
              <h2 id="welcome-heading">What would you like to explore?</h2>
              <p>Start a conversation. Each reply shows its source, speed, and token usage.</p>
              <div className="starter-prompts">
                {starterPrompts.map(({ label, description, icon }) => (
                  <button key={label} type="button" className="starter-prompt" onClick={() => {
                    setDraft(description)
                    editor.current?.focus()
                  }}>
                    <span className={`starter-icon ${icon}`} aria-hidden="true" />
                    <strong>{label}</strong>
                    <span className="starter-description">{description}</span>
                  </button>
                ))}
              </div>
            </section>
          ) : (
            <section className="conversation" role="log" aria-label="Conversation messages" aria-live="polite">
              {messages.map((message) => <ConversationMessage key={message.id} message={message} />)}
              {pending && <p className="typing" data-testid="typing-indicator">Waiting for a reply…</p>}
              <div ref={end} />
            </section>
          )}
        </div>

        <form className="composer" onSubmit={(event) => { event.preventDefault(); void submit() }}>
          <label className="sr-only" htmlFor="chat-message">Message</label>
          <div className="composer-box">
            <textarea
              id="chat-message"
              data-testid="chat-input"
              ref={editor}
              rows={2}
              placeholder="Type a message…"
              aria-describedby="composer-hint character-count"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing
                  && event.nativeEvent.keyCode !== 229) {
                  event.preventDefault()
                  void submit()
                }
              }}
            />
            <button className="primary-button" data-testid="send-button" type="submit" disabled={pending}>Send</button>
          </div>
          <div className="composer-hint">
            <span id="composer-hint">Enter to send <span aria-hidden="true">·</span> Shift + Enter for a new line</span>
            <span id="character-count" className={characterCount > 4000 ? 'over-limit' : ''}>
              {characterCount.toLocaleString('en-US')} / 4,000
            </span>
          </div>
        </form>
      </main>
    </div>
  )
}
