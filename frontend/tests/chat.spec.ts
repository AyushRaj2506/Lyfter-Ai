import { expect, test, type Route } from '@playwright/test'

const health = { status: 'ok', keys_configured: 2, cache_enabled: true }
const metadata = {
  resolution_source: 'LIVE_INFERENCE',
  model: 'gemini/gemini-3.5-flash-lite',
  latency_ms: 325,
  input_tokens: 14,
  output_tokens: 5,
  key_id: 'key_1',
  fallback_attempts: 0,
}

function reply(route: Route) {
  return route.fulfill({
    json: {
      session_id: route.request().postDataJSON().session_id,
      reply: 'A **formatted** reply.',
      metadata,
    },
  })
}

test('keeps required metadata below the reply and remembers dark mode', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light' })
  await page.route('**/api/health', route => route.fulfill({ json: health }))
  await page.route('**/api/chat', reply)
  await page.goto('/')

  const input = page.getByTestId('chat-input')
  await input.fill('Explain caching.')
  await input.press('Enter')
  await expect(input).toHaveValue('')
  const message = page.getByTestId('assistant-message')
  await expect(message.locator('.reply-body strong')).toHaveText('formatted')
  await expect(message.getByTestId('meta-source')).toHaveText('LIVE_INFERENCE')
  await expect(message.getByTestId('meta-model')).toHaveText(metadata.model)
  await expect(message.getByTestId('meta-latency')).toHaveText('325 ms')
  await expect(message.getByTestId('meta-input-tokens')).toHaveText('14')
  await expect(message.getByTestId('meta-output-tokens')).toHaveText('5')
  const bubble = await message.locator('.assistant-bubble').boundingBox()
  const diagnostics = await message.locator('.metadata').boundingBox()
  expect(diagnostics!.y).toBeGreaterThanOrEqual(bubble!.y + bubble!.height)

  const theme = page.getByRole('switch', { name: 'Dark mode' })
  await expect(theme).toHaveAttribute('aria-checked', 'false')
  await theme.press('Space')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await expect(message).toBeVisible()
  await page.reload()
  await expect(theme).toHaveAttribute('aria-checked', 'true')
  await expect(message).toHaveCount(0)
})

test('fits chat controls and diagnostics on a narrow phone', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 568 })
  await page.route('**/api/health', route => route.fulfill({ json: health }))
  await page.route('**/api/chat', reply)
  await page.goto('/')

  const input = page.getByTestId('chat-input')
  const send = page.getByTestId('send-button')
  await expect(send).toBeDisabled()
  await input.fill('Line one')
  const singleLine = await input.boundingBox()
  await input.press('Shift+Enter')
  await input.press('o')
  await expect(input).toHaveValue('Line one\no')
  expect((await input.boundingBox())!.height).toBeGreaterThan(singleLine!.height)
  await send.click()
  await expect(page.getByTestId('meta-model')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(320)
  await expect(send).toBeInViewport()
  await expect(page.getByRole('switch', { name: 'Dark mode' })).toBeInViewport()
})

test('refreshes API status after regaining focus or network access', async ({ page, context }) => {
  let available = true
  await page.route('**/api/health', route => available
    ? route.fulfill({ json: health })
    : route.fulfill({ status: 503, json: { status: 'degraded' } }))
  await page.goto('/')
  const status = page.getByRole('status', { name: '' }).filter({ hasText: /API/ })
  await expect(status).toHaveText('API connected')

  available = false
  await page.evaluate(() => window.dispatchEvent(new Event('focus')))
  await expect(status).toHaveText('API unavailable')

  available = true
  await page.evaluate(() => window.dispatchEvent(new Event('focus')))
  await expect(status).toHaveText('API connected')

  await context.setOffline(true)
  await expect(status).toHaveText('API unavailable')
  await context.setOffline(false)
  await expect(status).toHaveText('API connected')
})

test('retries an unconfirmed send with the same ID and payload', async ({ page }) => {
  const attempts: { id?: string; body: unknown; simulation?: string }[] = []
  await page.route('**/api/health', route => route.fulfill({ json: health }))
  await page.route('**/api/chat', async route => {
    const request = route.request()
    const headers = request.headers()
    attempts.push({
      id: headers['idempotency-key'],
      body: request.postDataJSON(),
      simulation: headers['x-simulate-failure'],
    })
    if (attempts.length === 1) await route.abort('connectionreset')
    else await reply(route)
  })
  await page.goto('/')

  const input = page.getByTestId('chat-input')
  const systemPrompt = page.getByTestId('system-prompt-input')
  await systemPrompt.fill('Be concise.')
  await page.getByLabel('Simulate failure').selectOption('key_1=429')
  await input.fill('Explain caching.')
  await input.press('Enter')
  const retry = page.getByRole('button', { name: 'Retry', exact: true })
  await expect(retry).toBeVisible()
  await systemPrompt.fill('Use a list.')
  await input.fill('My next question')
  await expect(page.getByTestId('send-button')).toBeDisabled()
  await retry.click()
  await expect(page.getByTestId('assistant-message')).toHaveCount(1)
  await expect(page.getByTestId('user-message')).toHaveCount(1)
  await expect(page.getByTestId('error-message')).toHaveCount(0)
  await expect(input).toHaveValue('My next question')
  expect(attempts[0].id).toMatch(/^r-/)
  expect(attempts[1]).toEqual(attempts[0])

  await input.press('Enter')
  await expect(page.getByTestId('assistant-message')).toHaveCount(2)
  expect(attempts[2].id).not.toBe(attempts[1].id)
})
