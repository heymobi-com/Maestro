// Strata is a local, OpenAI-compatible server that can do more than the common
// denominator: it honours the OpenAI strict-JSON schema form and holds its reasoning
// back when asked, which is what makes structured planning work on it. The backend
// detects it from the server's own /health, so this file only carries what the settings
// menu needs to offer the preset -- a URL check, making no request of its own.

export const STRATA_DEFAULT_URL = 'http://127.0.0.1:8081'

/** Whether a saved remote URL is the address Strata is normally reached at. */
export function isStrataUrl(url: string | null | undefined): boolean {
  if (!url) return false
  try {
    const parsed = new URL(url.trim())
    const host = parsed.hostname.toLowerCase()
    const local = host === 'localhost' || host === '127.0.0.1' || host === '::1'
      || host.startsWith('192.168.') || host.startsWith('10.')
    return local && parsed.port === '8081'
  } catch {
    return false
  }
}
