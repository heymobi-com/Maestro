# Freno de llamadas por turno (remedio deterministico de Token Guard).
# PreToolUse: cuenta llamadas y DENIEGA a partir del tope -> el agente deja de
# encadenar round-trips y se ve obligado a cerrar el turno y responder.
# UserPromptSubmit: reinicia el contador (nuevo turno = presupuesto nuevo).
# Fail-open: ante cualquier error devuelve nada y exit 0 (permite la herramienta).

param([int]$Cap = 12)

$ErrorActionPreference = 'Continue'
$stateDir = Join-Path $env:USERPROFILE '.copilot\token-guard'

try {
  $raw = [Console]::In.ReadToEnd()
  if (-not $raw) { exit 0 }
  $ev = $raw | ConvertFrom-Json

  $eventName = $ev.hookEventName; if (-not $eventName) { $eventName = $ev.hook_event_name }
  $sessionId = $ev.sessionId;     if (-not $sessionId) { $sessionId = $ev.session_id }
  if (-not $sessionId) { $sessionId = 'unknown' }

  if (-not (Test-Path $stateDir)) { New-Item -ItemType Directory -Force -Path $stateDir | Out-Null }

  # Interruptor de emergencia: crear este archivo desactiva el freno.
  if (Test-Path (Join-Path $stateDir 'hook-off')) { exit 0 }

  $file = Join-Path $stateDir ("calls-{0}.json" -f $sessionId)
  $now = Get-Date
  $st = @{ count = 0; turn = $now.ToString('s') }
  if (Test-Path $file) {
    try { $st = Get-Content $file -Raw | ConvertFrom-Json } catch { }
  }
  # Si el contador es viejo, el turno ya murio: reiniciar evita bloquear el chat.
  $turnStart = $now
  try { $turnStart = [datetime]$st.turn } catch { }
  if (($now - $turnStart).TotalMinutes -gt 30) { $st = @{ count = 0; turn = $now.ToString('s') } }

  if ($eventName -eq 'UserPromptSubmit') {
    @{ count = 0; turn = $now.ToString('s') } | ConvertTo-Json | Set-Content $file -Encoding UTF8
    exit 0
  }

  $st.count = [int]$st.count + 1
  @{ count = $st.count; turn = $st.turn } | ConvertTo-Json | Set-Content $file -Encoding UTF8
  Add-Content (Join-Path $stateDir 'brake.log') ("{0} {1} {2} {3}" -f $now.ToString('HH:mm:ss'), $sessionId.Substring(0,8), $st.count, $eventName) -ErrorAction SilentlyContinue

  if ($st.count -gt $Cap) {
    $msg = "Freno de tokens: ya van $($st.count - 1) llamadas en este turno (tope $Cap). Cada llamada reenvia todo el contexto. DETENTE: no pidas mas herramientas, reporta lo que tengas y pregunta al usuario si continua."
    @{
      hookSpecificOutput = @{
        hookEventName            = 'PreToolUse'
        permissionDecision       = 'deny'
        permissionDecisionReason = $msg
      }
    } | ConvertTo-Json -Depth 4 -Compress
  }
} catch { }
exit 0
