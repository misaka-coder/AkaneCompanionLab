# Process-local binding only. Source after instance env files, before launching Akane.
function Set-AkaneLocalRuntime {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Python
    )
    $runtimeRoot = [System.IO.Path]::GetFullPath($Root)
    if (-not [System.IO.Path]::IsPathRooted($Root) -or
        $runtimeRoot.TrimEnd('\', '/') -eq [System.IO.Path]::GetPathRoot($runtimeRoot).TrimEnd('\', '/')) {
        throw 'runtime_root_must_be_dedicated_absolute_directory'
    }
    $pythonPath = (Resolve-Path -LiteralPath $Python -ErrorAction Stop).Path
    if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) { throw 'runtime_python_not_file' }
    $version = & $pythonPath -c 'import sys; print(str(sys.version_info.major) + chr(46) + str(sys.version_info.minor)); sys.exit(0 if sys.version_info >= (3,11) else 2)'
    if ($LASTEXITCODE -ne 0 -or -not $version) { throw 'runtime_python_requires_3_11_or_newer' }
    $pythonBin = Split-Path -Parent $pythonPath
    $venvRoot = Split-Path -Parent $pythonBin
    $bindings = @{
        EXECUTION_WORKSPACE_ROOT = (Join-Path $runtimeRoot 'workspace')
        AKANE_DOCUMENT_PYTHON = $pythonPath
        TEMP = (Join-Path $runtimeRoot 'temp')
        TMP = (Join-Path $runtimeRoot 'temp')
        PIP_CACHE_DIR = (Join-Path $runtimeRoot 'cache/pip')
        NPM_CONFIG_CACHE = (Join-Path $runtimeRoot 'cache/npm')
        PNPM_HOME = (Join-Path $runtimeRoot 'tools/pnpm')
        COREPACK_HOME = (Join-Path $runtimeRoot 'cache/corepack')
        NPM_CONFIG_STORE_DIR = (Join-Path $runtimeRoot 'cache/pnpm-store')
    }
    # Validate/create all directories before altering this process's environment.
    foreach ($entry in $bindings.GetEnumerator()) {
        if ($entry.Key -ne 'AKANE_DOCUMENT_PYTHON') {
            $null = New-Item -ItemType Directory -Path $entry.Value -Force -ErrorAction Stop
        }
    }
    foreach ($entry in $bindings.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, 'Process')
    }
    $pathParts = @($env:PATH -split ';' | Where-Object { $_ -and $_.TrimEnd('\', '/') -ine $pythonBin.TrimEnd('\', '/') })
    $env:PATH = (@($pythonBin) + $pathParts) -join ';'
    if (Test-Path -LiteralPath (Join-Path $venvRoot 'pyvenv.cfg')) {
        $env:VIRTUAL_ENV = $venvRoot
    } else {
        [Environment]::SetEnvironmentVariable('VIRTUAL_ENV', $null, 'Process')
    }
    # These are task-operational locations, never a second runtime registry.
    [pscustomobject]@{ status = 'ready'; python = $pythonPath; version = "$version"; workspace = $bindings.EXECUTION_WORKSPACE_ROOT }
}
