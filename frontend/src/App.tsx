import { memo, useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { checkHealth, sendChat, type Metadata } from './api'
import { createSessionId } from './session'

type Message = { id: number; text: string } & (
  | { role: 'user' }
  | { role: 'error'; failedDraft?: string }
  | { role: 'assistant'; metadata: Metadata }
)

const starterPrompts = [
  { label: 'Explain a concept', description: 'What is response caching?', icon: 'book' },
  { label: 'Test chat memory', description: 'Remember that my project is named Orion.', icon: 'memory' },
  { label: 'Compare two ideas', description: 'How do retries and failover differ?', icon: 'compare' },
]

function ThemeToggle() {
  const [dark, setDark] = useState(() => document.documentElement.dataset.theme === 'dark')

  function toggleTheme() {
    const theme = dark ? 'light' : 'dark'
    document.documentElement.dataset.theme = theme
    setDark(!dark)
    try { localStorage.setItem('lyftr-theme', theme) } catch {
      // Keep the theme active for this page when storage is unavailable.
    }
  }

  return (
    <button type="button" className="theme-toggle" role="switch" aria-checked={dark} onClick={toggleTheme}>
      <span className="theme-track" aria-hidden="true" />
      Dark mode
    </button>
  )
}

function MessageMeta({ metadata }: { metadata: Metadata }) {
  return (
    <dl className="metadata" aria-label="Reply metadata">
      <div>
        <dt className="sr-only">Source</dt>
        <dd
          className={metadata.resolution_source === 'LLM_CACHE' ? 'source source-cache' : 'source'}
          data-testid="meta-source"
        >
          {metadata.resolution_source}
        </dd>
      </div>
      <div><dt className="sr-only">Model</dt><dd data-testid="meta-model">{metadata.model}</dd></div>
      <div><dt className="sr-only">Latency</dt><dd data-testid="meta-latency">{metadata.latency_ms} ms</dd></div>
      <div className="token-usage">
        <dt>Tokens</dt>
        <dd>
          <span className="sr-only">Input </span><span data-testid="meta-input-tokens">{metadata.input_tokens}</span>
          <span aria-hidden="true"> in · </span>
          <span className="sr-only">, output </span><span data-testid="meta-output-tokens">{metadata.output_tokens}</span>
          <span aria-hidden="true"> out</span>
        </dd>
      </div>
      <div><dt>Key</dt><dd data-testid="meta-key-id">{metadata.key_id ?? '—'}</dd></div>
      <div><dt>Fallbacks</dt><dd data-testid="meta-fallbacks">{metadata.fallback_attempts}</dd></div>
    </dl>
  )
}

const ReplyBody = memo(function ReplyBody({ text }: { text: string }) {
  return (
    <div className="reply-body">
      <Markdown remarkPlugins={[remarkGfm]} components={{
        a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
        img: ({ alt }) => <span>{alt || 'Image'}</span>,
        table: ({ children }) => <div className="table-scroll"><table>{children}</table></div>,
      }}>{text}</Markdown>
    </div>
  )
})

function ConversationMessage({ message, editDraft }: { message: Message; editDraft?: (text: string) => void }) {
  if (message.role === 'user') {
    return (
      <article className="message user-message" aria-label="Your message" data-testid="user-message">
        <p className="message-text">{message.text}</p>
      </article>
    )
  }
  if (message.role === 'error') {
    const failedDraft = message.failedDraft
    return (
      <aside className="error-message" role="alert" data-testid="error-message">
        <span className="error-icon" aria-hidden="true">!</span>
        <span className="error-text">{message.text}</span>
        {failedDraft && editDraft && (
          <button type="button" className="edit-message" onClick={() => editDraft(failedDraft)}>
            Edit message
          </button>
        )}
      </aside>
    )
  }
  if (message.role === 'assistant') {
    return (
      <article className="assistant-message" data-testid="assistant-message">
        <div className="message assistant-bubble">
          <header className="speaker"><span className="mini-mark" aria-hidden="true">✦</span> Lyftr</header>
          <ReplyBody text={message.text} />
        </div>
        <MessageMeta metadata={message.metadata} />
      </article>
    )
  }
}

export function App() {
  const [sessionId, setSessionId] = useState(createSessionId)
  const [messages, setMessages] = useState<Message[]>([])
  const [draft, setDraft] = useState('')
  const [emptyHint, setEmptyHint] = useState(false)
  const [systemPrompt, setSystemPrompt] = useState('')
  const [savedPrompt, setSavedPrompt] = useState('')
  const [simulation, setSimulation] = useState('')
  const [pending, setPending] = useState(false)
  const [keyCount, setKeyCount] = useState<number | null | undefined>(undefined)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [sessionCopied, setSessionCopied] = useState(false)
  const editor = useRef<HTMLTextAreaElement>(null)
  const scrollArea = useRef<HTMLDivElement>(null)
  const following = useRef(true)
  const inFlight = useRef(false)
  const messageId = useRef(0)
  const characterCount = Array.from(draft.trim()).length

  useEffect(() => {
    const controller = new AbortController()
    let retry: number
    async function refreshHealth() {
      const count = await checkHealth(controller.signal).catch(() => null)
      if (controller.signal.aborted) return
      setKeyCount(count)
      if (count === null) retry = window.setTimeout(refreshHealth, 15000)
    }
    void refreshHealth()
    return () => { controller.abort(); window.clearTimeout(retry) }
  }, [])

  useEffect(() => {
    const area = scrollArea.current
    if (!area || !following.current || !messages.length) return
    const last = area.querySelector('.conversation')?.lastElementChild
    last?.scrollIntoView({ block: last.clientHeight > area.clientHeight ? 'start' : 'nearest' })
  }, [messages, pending])

  function startNewChat() {
    if (inFlight.current) return
    setSessionId(createSessionId())
    setMessages([])
    setDraft('')
    setEmptyHint(false)
    setSystemPrompt('')
    setSavedPrompt('')
    setSimulation('')
    setSessionCopied(false)
    setSettingsOpen(false)
    following.current = true
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

  function editDraft(text: string) {
    setDraft(text)
    setEmptyHint(false)
    editor.current?.focus()
  }

  function showError(text: string, failedDraft?: string) {
    const id = ++messageId.current
    setMessages((current) => [...current, { id, role: 'error', text, failedDraft }])
  }

  async function submit() {
    if (inFlight.current) return
    const text = draft.trim()
    if (!text) {
      setEmptyHint(true)
      editor.current?.focus()
      return
    }

    following.current = true
    setDraft('')
    setEmptyHint(false)
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
      if (systemPrompt.trim()) setSavedPrompt(systemPrompt)
      const replyId = ++messageId.current
      setMessages((current) => [...current, {
        id: replyId,
        role: 'assistant',
        text: response.reply,
        metadata: response.metadata,
      }])
    } catch (error) {
      showError(error instanceof Error ? error.message : 'The message could not be sent.', text)
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
            placeholder={savedPrompt || 'e.g. Keep replies brief'}
            aria-describedby="system-prompt-hint"
          />
          <p id="system-prompt-hint" className="field-hint">
            {savedPrompt && !systemPrompt.trim() ? 'Using the saved prompt.' : 'Saved when the assistant replies.'}
          </p>

          <label htmlFor="failure-mode">Simulate failure</label>
          <div className="select-field">
            <select id="failure-mode" value={simulation} onChange={(event) => setSimulation(event.target.value)}>
              <option value="">Off</option>
              <option value="key_1=429">Key 1 · Rate limit (429)</option>
              <option value="key_1=500">Key 1 · Server error (500)</option>
              <option value="*=429">All keys · Rate limit (429)</option>
            </select>
          </div>
          <p className="field-hint">Only affects your next message.</p>
        </div>

        <div className="sidebar-footer">
          <span className={typeof keyCount === 'number' ? 'status-dot ready' : 'status-dot'} aria-hidden="true" />
          <span role="status">
            {keyCount === undefined ? 'Checking API' : keyCount === null ? 'API unavailable' : 'API connected'}
          </span>
          {typeof keyCount === 'number' && <span className="key-count">{keyCount} {keyCount === 1 ? 'key' : 'keys'}</span>}
        </div>
      </aside>

      <main className="workspace">
        <header className="workspace-header">
          <h1>Conversation</h1>
          <div className="workspace-actions">
            <button
              type="button"
              className="session-id"
              aria-label={sessionCopied ? 'Session ID copied' : 'Copy full session ID'}
              title={sessionId}
              onClick={() => void copySession()}
            >
              {sessionCopied ? 'Copied' : 'Copy ID'} <code>{sessionId.slice(0, 10)}</code>
            </button>
            <ThemeToggle />
          </div>
        </header>

        <div className="scroll-area" ref={scrollArea} onScroll={(event) => {
          const area = event.currentTarget
          following.current = area.scrollHeight - area.scrollTop - area.clientHeight < 48
        }}>
          {messages.length === 0 ? (
            <section className="empty-state" aria-labelledby="welcome-heading">
              <div className="welcome-panel">
                <div className="welcome-copy">
                  <h2 id="welcome-heading">What would you like to <em>explore?</em></h2>
                  <p>Ask a question or try a prompt.</p>
                </div>
                <div className="orbit" aria-hidden="true"><i /><i /><i /><b>✦</b></div>
              </div>
              <div className="starter-prompts">
                {starterPrompts.map(({ label, description, icon }) => (
                  <button key={label} type="button" className="starter-prompt" onClick={() => editDraft(description)}>
                    <span className={`starter-icon ${icon}`} aria-hidden="true" />
                    <strong>{label}</strong>
                    <span className="starter-description">{description}</span>
                  </button>
                ))}
              </div>
            </section>
          ) : (
            <section className="conversation" role="log" aria-label="Conversation messages" aria-live="polite">
              {messages.map((message) => (
                <ConversationMessage key={message.id} message={message} editDraft={!draft && !pending ? editDraft : undefined} />
              ))}
              {pending && <p className="typing" data-testid="typing-indicator">Waiting for a reply…</p>}
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
              rows={1}
              placeholder="Message Lyftr…"
              aria-describedby="composer-hint character-count"
              value={draft}
              onChange={(event) => {
                setDraft(event.target.value)
                if (emptyHint) setEmptyHint(false)
              }}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing
                  && event.nativeEvent.keyCode !== 229) {
                  event.preventDefault()
                  void submit()
                }
              }}
            />
            <button className="primary-button send-button" data-testid="send-button" type="submit" disabled={pending || characterCount === 0}>
              Send <span aria-hidden="true">↑</span>
            </button>
          </div>
          <div className="composer-hint">
            <span id="composer-hint" className={emptyHint ? 'over-limit' : undefined} aria-live="polite">
              {emptyHint ? 'Enter a message to send.' : 'Enter to send · Shift + Enter for a new line'}
            </span>
            <span id="character-count" className={characterCount > 4000 ? 'over-limit' : ''}>
              {characterCount.toLocaleString('en-US')} / 4,000
            </span>
          </div>
        </form>
      </main>
    </div>
  )
}
