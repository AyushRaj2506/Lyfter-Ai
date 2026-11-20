import { useEffect, useState } from 'react'
import { checkHealth } from './api'

export function useApiHealth() {
  const [keyCount, setKeyCount] = useState<number | null | undefined>(undefined)

  useEffect(() => {
    let active: AbortController | undefined
    let retry: number | undefined

    function cancelCheck() {
      window.clearTimeout(retry)
      active?.abort()
      active = undefined
    }

    function markOffline() {
      cancelCheck()
      setKeyCount(null)
    }

    async function refreshHealth() {
      if (document.hidden || active) return
      if (!navigator.onLine) {
        markOffline()
        return
      }

      window.clearTimeout(retry)
      const controller = new AbortController()
      active = controller
      const timeout = window.setTimeout(() => controller.abort(), 75000)
      const count = await checkHealth(controller.signal).catch(() => null)
      window.clearTimeout(timeout)
      if (active !== controller) return
      active = undefined
      setKeyCount(count)
      if (count === null && !document.hidden) retry = window.setTimeout(refreshHealth, 15000)
    }

    window.addEventListener('focus', refreshHealth)
    window.addEventListener('online', refreshHealth)
    window.addEventListener('offline', markOffline)
    document.addEventListener('visibilitychange', refreshHealth)
    void refreshHealth()

    return () => {
      cancelCheck()
      window.removeEventListener('focus', refreshHealth)
      window.removeEventListener('online', refreshHealth)
      window.removeEventListener('offline', markOffline)
      document.removeEventListener('visibilitychange', refreshHealth)
    }
  }, [])

  return keyCount
}
