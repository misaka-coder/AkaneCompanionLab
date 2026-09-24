param(
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]] $TauriArgs
)

$ErrorActionPreference = "Stop"

$cargoBin = Join-Path $env:USERPROFILE ".cargo\bin"
if (Test-Path -LiteralPath $cargoBin) {
  $pathItems = @($env:Path -split ";" | Where-Object { $_ })
  if ($pathItems -notcontains $cargoBin) {
    $env:Path = "$cargoBin;$env:Path"
  }
}

$tauriCmd = Join-Path $PSScriptRoot "..\node_modules\.bin\tauri.cmd"
if (-not (Test-Path -LiteralPath $tauriCmd)) {
  throw "Tauri CLI not found. Run npm install in desktop_pet_next first."
}

& $tauriCmd @TauriArgs
exit $LASTEXITCODE
