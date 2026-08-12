Set-StrictMode -Version Latest

function Get-AkaneLocalPackageFingerprint {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot,
        [Parameter(Mandatory = $true)]
        [string]$SourceRoot
    )

    $requirements = Join-Path $ProjectRoot "requirements-packages.txt"
    if (-not (Test-Path -LiteralPath $requirements -PathType Leaf)) {
        throw "local_package_requirements_missing"
    }

    $entries = New-Object System.Collections.Generic.List[string]
    $entries.Add([System.IO.File]::ReadAllText($requirements))
    $builder = Join-Path $ProjectRoot "scripts\build_extracted_package_wheelhouse.py"
    if (-not (Test-Path -LiteralPath $builder -PathType Leaf)) {
        throw "local_package_builder_missing"
    }
    $entries.Add([System.IO.File]::ReadAllText($builder))
    $packageNames = New-Object System.Collections.Generic.List[string]
    foreach ($rawLine in [System.IO.File]::ReadAllLines($requirements)) {
        $line = ([string]$rawLine).Trim()
        if (-not $line -or $line.StartsWith("#")) {
            continue
        }
        $packageName = ($line -split "==", 2)[0].Trim()
        if (-not $packageName) {
            continue
        }
        $packageNames.Add($packageName)
    }
    # The canonical builder also validates this internal helper wheel even
    # though the Akane host does not install it as a direct requirement.
    if (-not $packageNames.Contains("capcore-host-utils")) {
        $packageNames.Add("capcore-host-utils")
    }
    foreach ($packageName in $packageNames) {
        $packageRoot = Join-Path $SourceRoot $packageName
        if (-not (Test-Path -LiteralPath (Join-Path $packageRoot ".git") -PathType Container)) {
            throw "local_package_source_missing:$packageName"
        }
        $commitOutput = @(& git -C $packageRoot rev-parse HEAD 2>$null)
        $commit = $commitOutput | Select-Object -First 1
        if ([string]::IsNullOrWhiteSpace([string]$commit)) {
            throw "local_package_revision_unavailable:$packageName"
        }
        $trackedChanges = @(& git -C $packageRoot status --porcelain --untracked-files=no 2>$null)
        if ($trackedChanges.Count -gt 0) {
            throw "local_package_source_dirty:$packageName"
        }
        $entries.Add("$packageName=$(([string]$commit).Trim())")
    }

    $payload = [System.Text.Encoding]::UTF8.GetBytes(($entries -join "`n"))
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($payload)) -replace "-", "").ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

function Test-AkaneLocalPackageContracts {
    param([Parameter(Mandatory = $true)][string]$PythonPath)

    & $PythonPath -c @"
from memcore import build_native_memory_tool_specs
names = {str(item.get('name') or '') for item in build_native_memory_tool_specs(tool_format='plain', include_material_tool=False)}
required = {'retrieve_for_turn', 'read_timeline', 'browse_memory', 'open_memory'}
raise SystemExit(0 if required <= names else 1)
"@ 2>$null
    return ($LASTEXITCODE -eq 0)
}

function Sync-AkaneLocalPackages {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot
    )

    $resolvedProject = [System.IO.Path]::GetFullPath($ProjectRoot)
    $sourceRoot = Split-Path -Parent $resolvedProject
    $python = Join-Path $resolvedProject ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "local_python_environment_missing"
    }

    $fingerprint = Get-AkaneLocalPackageFingerprint -ProjectRoot $resolvedProject -SourceRoot $sourceRoot
    $stamp = Join-Path $resolvedProject ".venv\.akane-local-packages.sha256"
    $installedFingerprint = if (Test-Path -LiteralPath $stamp -PathType Leaf) {
        ([System.IO.File]::ReadAllText($stamp)).Trim()
    } else {
        ""
    }
    $contractsReady = Test-AkaneLocalPackageContracts -PythonPath $python
    if ($installedFingerprint -eq $fingerprint -and $contractsReady) {
        return [pscustomobject]@{ Status = "ready"; Fingerprint = $fingerprint }
    }

    $cacheBase = [Environment]::GetFolderPath("LocalApplicationData")
    if ([string]::IsNullOrWhiteSpace($cacheBase)) {
        throw "local_package_cache_unavailable"
    }
    $wheelhouse = Join-Path $cacheBase "Akane\local-package-wheelhouse\$fingerprint"
    New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null

    Write-Host "[INFO] Internal package revisions changed; rebuilding the local wheelhouse once..."
    & $python (Join-Path $resolvedProject "scripts\build_extracted_package_wheelhouse.py") `
        --source-root $sourceRoot `
        --output-dir $wheelhouse `
        --internal-only
    if ($LASTEXITCODE -ne 0) {
        throw "local_package_wheelhouse_build_failed"
    }

    & $python -m pip install `
        --disable-pip-version-check `
        --force-reinstall `
        --no-deps `
        --no-index `
        --find-links $wheelhouse `
        -r (Join-Path $resolvedProject "requirements-packages.txt")
    if ($LASTEXITCODE -ne 0) {
        throw "local_package_install_failed"
    }
    if (-not (Test-AkaneLocalPackageContracts -PythonPath $python)) {
        throw "local_package_contract_validation_failed"
    }

    $stampTemp = "$stamp.$([Guid]::NewGuid().ToString('N')).tmp"
    try {
        [System.IO.File]::WriteAllText($stampTemp, "$fingerprint`n", [System.Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $stampTemp -Destination $stamp -Force
    } finally {
        Remove-Item -LiteralPath $stampTemp -Force -ErrorAction SilentlyContinue
    }
    return [pscustomobject]@{ Status = "rebuilt"; Fingerprint = $fingerprint }
}
