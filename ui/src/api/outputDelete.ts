/**
 * Deleting one take, including the take a Director shot is using.
 *
 * `client.ts` hands its own `deleteOutput` here, so the whole decision -- which
 * endpoint a file belongs to, whether the delete is forced, and which reason is
 * shown -- stays ours and keeps working when upstream edits that file.
 *
 * The server refuses to remove a take a shot is using and says which shot; the
 * user's answer comes back as `force`.
 */

/** Uploads and outputs are removed by different endpoints. */
const UPLOADS = '/api/v1/uploads'
const OUTPUTS = '/api/v1/outputs'

export async function deleteOutputFile(
  origin: string,
  name: string,
  workspace?: string,
  force = false,
): Promise<void> {
  const isUpload = workspace === '__uploads__'
  const params = new URLSearchParams()
  if (force) params.set('force', 'true')
  // The workspace is encoded the way the rest of this client encodes it: spaces
  // stay %20 instead of turning into a "+" inside the query.
  const scope = !isUpload && workspace ? `workspace=${encodeURIComponent(workspace)}` : ''
  const search = [scope, params.toString()].filter(Boolean).join('&')
  const endpoint = `${origin}${isUpload ? UPLOADS : OUTPUTS}/${encodeURIComponent(name)}`
    + (search ? `?${search}` : '')

  const res = await fetch(endpoint, { method: 'DELETE' })
  if (!res.ok) {
    // Surface the server's own reason instead of a generic failure: it is the
    // text that tells the user which shot holds the take.
    const error = await res.json().catch(() => null) as { detail?: unknown; error?: unknown } | null
    const detail = typeof error?.detail === 'string'
      ? error.detail
      : typeof error?.error === 'string' ? error.error : null
    throw new Error(detail || `Failed to delete ${isUpload ? 'upload' : 'output'}`)
  }
}
