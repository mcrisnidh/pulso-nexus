<#
 PULSO NEXUS v0.3.2 — Live Data Connectors (laboratorio ASMXC1200, PREVIEW / NO-GO COMERCIAL)
 Comando ÚNICO: la primera vez instala y arranca; después solo arranca.
   .\NexusJunta.ps1 -R3 <matriz_R3.csv> -R1 <correspondencias_R1.csv> [-Asignaciones <csv>]   (primera vez)
   .\NexusJunta.ps1                                                                          (siguientes)
   .\NexusJunta.ps1 -Desinstalar [-BorrarDatos]                                              (rollback)
   .\NexusJunta.ps1 -Preflight fg_odbc [-Nivel entorno|conexion|consulta] [-Autorizo]          (un conector, solo lectura)
   .\NexusJunta.ps1 -Conciliar fg -Manual <INV_STA*.TXT> -Directa <preflight\fg_odbc\*.csv> -Alcance FG [-Acreditar]
                                                                                             (backlog | fg | embarques -Evidencia)
   .\NexusJunta.ps1 -AcreditarEmbarques <preflight\embarques\*.csv> -Aprobador <nombre> -AusenciaEsCero si|no -ReferenciaDecision <folio>
                                                                                             (FIX5: acreditación /2, regla CFDI emitida = embarque)
 Código de salida: el de la herramienta ejecutada (0 = PASS); cualquier error del lanzador = 1.
 La acreditación NO activa ningún conector: «activo» se cambia a mano en conectores.json.
 No modifica el repositorio, PULSO Web (ASMXC1012), AMFT, MAGNUM, SQL, Python compartido ni los originales.
#>
param(
  [string]$Repo = 'C:\Users\ucbaa\pulso-nexus',
  [string]$Lab  = (Join-Path $env:LOCALAPPDATA 'PULSO_NEXUS_LAB'),
  [string]$R3 = '', [string]$R1 = '', [string]$Asignaciones = '',
  [string]$Originales = '', [string]$Escenario = 'A', [int]$Puerto = 8877,
  [switch]$Desinstalar, [switch]$BorrarDatos,
  [ValidateSet('', 'backlog_ssrs', 'fg_odbc', 'embarques')][string]$Preflight = '',
  [ValidateSet('entorno', 'conexion', 'consulta')][string]$Nivel = 'entorno', [switch]$Autorizo,
  [ValidateSet('', 'backlog', 'fg', 'embarques')][string]$Conciliar = '',
  [string]$Manual = '', [string]$Directa = '', [string]$Evidencia = '', [string]$Alcance = '', [switch]$Acreditar,
  [string]$AcreditarEmbarques = '', [string]$Aprobador = '', [ValidateSet('', 'si', 'no')][string]$AusenciaEsCero = '',
  [string]$ReferenciaDecision = '',
  [switch]$ActivarFIX6, [string]$ConfirmarSSRS = '', [string]$ShaSSRS = '', [string]$ArchivoSSRS = '',
  [string]$ReferenciaLote = '', [string]$RefreshERP = '', [string]$MotivoERP = ''
)
$ErrorActionPreference = 'Stop'
# v0.3.2: consola y Python en UTF-8 (sin esto Windows usa cp850/cp1252 y la salida se corrompe o falla)
$utf8 = New-Object System.Text.UTF8Encoding $false
try { [Console]::OutputEncoding = $utf8; [Console]::InputEncoding = $utf8 } catch { }
$OutputEncoding = $utf8
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$rc = 0
$Here    = Split-Path -Parent $MyInvocation.MyCommand.Path
$App     = Join-Path $Lab 'nexus_junta_v02'
$Data    = Join-Path $Lab 'junta-data-2026-11'
$Fuentes = Join-Path $Lab 'junta-inputs\2026-11'
if (-not $Originales) { $Originales = Join-Path $Lab 'inputs\2026-11' }
$env:PYTHONDONTWRITEBYTECODE = '1'

if ($Desinstalar) {
  $cfgPath = Join-Path $Data 'preview.json'
  if (Test-Path $cfgPath) {
    $cfg = Get-Content -LiteralPath $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($cfg.ssrs_automatico -eq $true -and (Test-Path (Join-Path $Data 'frozen_2026-12.json'))) {
      throw 'NO-GO: downgrade de FIX6 con Frozen SSRS diciembre sellado; no ejecutar rollback legacy sin plan de migración.'
    }
  }
  if (Test-Path $App) { Remove-Item -Recurse -Force $App; Write-Host "Aplicación retirada: $App" }
  $prev = Get-ChildItem $Lab -Directory -Filter 'nexus_junta_v02.anterior_*' -ErrorAction SilentlyContinue | Sort-Object Name | Select-Object -Last 1
  if ($prev) { Move-Item $prev.FullName $App; Write-Host "Restaurada la instalación anterior: $($prev.Name)" }
  if ($BorrarDatos) {
    foreach ($d in @($Data, (Join-Path $Lab 'junta-inputs'))) {
      if (Test-Path $d) { Get-ChildItem -Recurse -File $d | ForEach-Object { $_.IsReadOnly = $false }; Remove-Item -Recurse -Force $d; Write-Host "Borrado: $d" }
    }
  }
  Write-Host 'Los originales de inputs\2026-11 nunca se tocan.'
  exit 0
}

Push-Location $Repo
try {
  $head = (git rev-parse HEAD).Trim()
  & git merge-base --is-ancestor 'b4ec5be32cf7552c1e9fae0086b038861b8f40c1' $head
  if ($LASTEXITCODE -ne 0) { throw 'NEXUS CORE: base aprobada b4ec5be no es ancestro de HEAD.' }
  if ((git status --porcelain) -ne $null) { throw 'El repositorio tiene cambios locales: no se instala ni se arranca.' }

  # Instalación (o actualización de la aplicación; datos y notas se conservan)
  $pkg = Join-Path $Here 'app'
  $installed = Join-Path $App 'VERSION.sha256'
  $wanted = (Get-FileHash -Algorithm SHA256 (Join-Path $Here 'SHA256SUMS')).Hash
  if (-not (Test-Path $installed) -or ((Get-Content $installed) -ne $wanted)) {
    if (Test-Path $App) { $bak = "$App.anterior_$(Get-Date -Format yyyyMMdd_HHmmss)"; Move-Item $App $bak; Write-Host "Instalación anterior conservada en $bak" }
    New-Item -ItemType Directory -Force $App | Out-Null
    Copy-Item -Recurse (Join-Path $pkg '*') $App
    Set-Content $installed $wanted
  }
  $plantilla = Join-Path $Data 'conectores.json'
  if ((Test-Path $Data) -and -not (Test-Path $plantilla)) { Copy-Item (Join-Path $App 'conectores.plantilla.json') $plantilla; Write-Host "Plantilla de conectores (todo inactivo): $plantilla" }
  if ($ActivarFIX6) {
    $a = @('run','--frozen','--extra','web','--with','tzdata==2025.2','python',(Join-Path $App 'preview.py'),'activar-fix6','--data',$Data)
    & uv @a
    $rc = $LASTEXITCODE
  } elseif ($ConfirmarSSRS) {
    if (-not $ShaSSRS -or -not $ArchivoSSRS -or -not $Aprobador -or -not $ReferenciaLote.Trim()) { throw 'Confirme SSRS con -ConfirmarSSRS AAAA-MM -ArchivoSSRS ID -ShaSSRS 64HEX -Aprobador nombre -ReferenciaLote evidencia_ETL.' }
    $a = @('run','--frozen','--extra','web','--with','tzdata==2025.2','python',(Join-Path $App 'preview.py'),'confirmar-ssrs-mensual','--data',$Data,'--mes',$ConfirmarSSRS,'--archivo',$ArchivoSSRS,'--sha256',$ShaSSRS,'--aprobador',$Aprobador,'--referencia-lote',$ReferenciaLote)
    & uv @a
    $rc = $LASTEXITCODE
  } elseif ($RefreshERP) {
    if (-not $Aprobador -or -not $MotivoERP.Trim()) { throw 'ERP excepcional requiere -RefreshERP ruta -Aprobador nombre -MotivoERP motivo; no afecta Frozen.' }
    $a = @('run','--frozen','--extra','web','--with','tzdata==2025.2','python',(Join-Path $App 'preview.py'),'refresh','--data',$Data,'--erp-urgente',$RefreshERP,'--aprobador',$Aprobador,'--motivo',$MotivoERP)
    & uv @a
    $rc = $LASTEXITCODE
  } elseif ($Preflight) {
    $a = @('run','--frozen','--extra','web','--with','tzdata==2025.2','python',(Join-Path $App 'preflight.py'),'--data',$Data,'--conector',$Preflight,'--nivel',$Nivel)
    if ($Autorizo) { $a += '--autorizo' }
    & uv @a
    $rc = $LASTEXITCODE
  } elseif ($AcreditarEmbarques) {
    if (-not $Aprobador -or -not $AusenciaEsCero -or -not $ReferenciaDecision.Trim()) { throw '-AcreditarEmbarques exige -Aprobador, -AusenciaEsCero si|no y -ReferenciaDecision de la decisión comercial aprobada (sin valores por omisión).' }
    $a = @('run','--frozen','--extra','web','--with','tzdata==2025.2','python',(Join-Path $App 'acreditar_embarques.py'),'--data',$Data,'--captura',$AcreditarEmbarques,'--aprobador',$Aprobador,'--ausencia-es-cero',$AusenciaEsCero,'--referencia-decision',$ReferenciaDecision)
    & uv @a
    $rc = $LASTEXITCODE
  } elseif ($Conciliar) {
    $a = @('run','--frozen','--extra','web','--with','tzdata==2025.2','python',(Join-Path $App 'conciliar.py'),$Conciliar,'--data',$Data,'--directa',$Directa)
    if ($Manual) { $a += @('--manual', $Manual) }
    if ($Evidencia) { $a += @('--evidencia', $Evidencia) }
    if ($Alcance) { $a += @('--alcance', $Alcance) }
    if ($Acreditar) { $a += '--acreditar' }
    & uv @a
    $rc = $LASTEXITCODE
  } else {
    if (-not (Test-Path (Join-Path $Data 'preview.json'))) {
      if (-not $R3 -or -not $R1) { throw 'Primera ejecución: indique -R3 y -R1 (matrices privadas provisionales ya existentes).' }
      $esperado = @{
        'R_20261101.TXT' = '2B59EBACD873DD7D97EADC0E64715D93831F3BECA45679817513D3F59D70D0D6';
        'Portal_CAT_2026-11-03_0955.csv' = '0592178D604A15F9507A4F8A0B24CA0BE55FC94EA612590C0A14E75F8260EFD9';
        'INV_STA289.TXT' = '779F63FC4D5B80683E99F6E171C8F150B1075FC9BB5D68B3770B9B06E6AD3639'
      }
      New-Item -ItemType Directory -Force $Fuentes | Out-Null
      foreach ($f in $esperado.Keys) {
        $o = Join-Path $Originales $f
        if ((Get-FileHash -Algorithm SHA256 $o).Hash -ne $esperado[$f]) { throw "$f no coincide con la huella de referencia" }
        $d = Join-Path $Fuentes $f
        if (-not (Test-Path $d)) { Copy-Item $o $d }
      }
      $uvArgs = @('run','--frozen','--extra','web','--with','tzdata==2025.2','python',(Join-Path $App 'preview.py'),'init','--data',$Data,'--src',$Fuentes,'--r3',$R3,'--r1',$R1,'--escenario',$Escenario)
      if ($Asignaciones) { $uvArgs += @('--asignaciones', $Asignaciones) }
      & uv @uvArgs
      if ($LASTEXITCODE -ne 0) { throw "init falló (código $LASTEXITCODE)" }
    }
    if (-not (Test-Path (Join-Path $Data 'vigente.json'))) {
      Write-Host 'Calculando la generación inicial con el motor NEXUS...'
      & uv run --frozen --extra web --with tzdata==2025.2 python (Join-Path $App 'preview.py') refresh --data $Data
      if ($LASTEXITCODE -ne 0) { throw "el Refresh inicial falló (código $LASTEXITCODE): revise refresh_estado.json; no se arranca la Junta" }
    }
    Write-Host "Fuentes de la Junta (deje aquí los cortes nuevos): $Fuentes"
    Start-Job -ScriptBlock { param($p) Start-Sleep 5; Start-Process "http://127.0.0.1:$p/junta" } -ArgumentList $Puerto | Out-Null
    & uv run --frozen --extra web --with tzdata==2025.2 python (Join-Path $App 'preview.py') serve --data $Data --port $Puerto
    $rc = $LASTEXITCODE
  }
} catch {
  Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
  $rc = 1
} finally { Pop-Location }
exit $rc
