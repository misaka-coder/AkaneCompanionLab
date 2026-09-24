param()
$ErrorActionPreference = 'Stop'
# Prime npm's cache explicitly. Runtime uses --offline and never installs on a model request.
$nodeCommand = Get-Command node.exe -ErrorAction Stop
$nodeDirectory = Split-Path -Parent $nodeCommand.Source
$npmEntry = Join-Path $nodeDirectory 'node_modules/npm/bin/npx-cli.js'
if (-not (Test-Path -LiteralPath $npmEntry -PathType Leaf)) { throw 'Node.js npm/npx is required on this desktop.' }
& $nodeCommand.Source $npmEntry --yes --package=chrome-devtools-mcp@1.9.0 chrome-devtools-mcp --version
if ($LASTEXITCODE -ne 0) { throw "Chrome adapter installation failed (exit $LASTEXITCODE)." }
Write-Output 'Chrome adapter cache is ready. Enable personal Chrome in the desktop control center, then accept Chrome connection permission.'
