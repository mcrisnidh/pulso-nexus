<#
 PULSO NEXUS v0.3 — descarga SOLO LECTURA del reporte SSRS con la sesión Windows del usuario.
 - Sin contraseñas, cookies ni tokens: -UseDefaultCredentials (Negotiate/NTLM del servidor).
 - Sin redirecciones; salida = XML en Base64 por stdout (no se guarda en disco aquí).
 - Código 3 = el servidor pide autenticación (401/403): usar la importación interactiva.
#>
param(
  [Parameter(Mandatory = $true)][string]$Url,
  [int]$TimeoutSec = 150
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try {
  $r = Invoke-WebRequest -Uri $Url -UseDefaultCredentials -UseBasicParsing -MaximumRedirection 0 -TimeoutSec $TimeoutSec -Method Get
  if ($r.StatusCode -ne 200) { [Console]::Error.Write("HTTP_$($r.StatusCode)"); exit 2 }
  $bytes = $r.RawContentStream.ToArray()
  [Console]::Out.Write([Convert]::ToBase64String($bytes))
  exit 0
} catch {
  $code = 0
  if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
  [Console]::Error.Write("HTTP_$code")
  if ($code -eq 401 -or $code -eq 403) { exit 3 }
  exit 2
}
