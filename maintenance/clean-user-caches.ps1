[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidateRange(0, 3650)]
    [int]$TempRetentionDays = 7,
    [switch]$Apply,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function ConvertTo-NormalizedPath {
    param(
        [AllowEmptyString()]
        [string]$Path
    )

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return ''
    }

    $expanded = [Environment]::ExpandEnvironmentVariables($Path.Trim())
    $fullPath = [System.IO.Path]::GetFullPath($expanded)
    $root = [System.IO.Path]::GetPathRoot($fullPath)
    if ($root -and $fullPath.Equals($root, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $root
    }

    return $fullPath.TrimEnd(
        [char[]]@(
            [System.IO.Path]::DirectorySeparatorChar,
            [System.IO.Path]::AltDirectorySeparatorChar
        )
    )
}

function Test-PathEqual {
    param(
        [string]$Left,
        [string]$Right
    )

    if (-not $Left -or -not $Right) {
        return $false
    }

    return $Left.Equals($Right, [System.StringComparison]::OrdinalIgnoreCase)
}

function Test-PathWithin {
    param(
        [string]$Path,
        [string]$Root,
        [switch]$AllowEqual
    )

    if (-not $Path -or -not $Root) {
        return $false
    }

    if (Test-PathEqual -Left $Path -Right $Root) {
        return [bool]$AllowEqual
    }

    $separator = [System.IO.Path]::DirectorySeparatorChar
    $rootPrefix = if ($Root.EndsWith([string]$separator)) {
        $Root
    } else {
        "$Root$separator"
    }
    return $Path.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)
}

function New-CleanupContext {
    param(
        [string]$ScriptRoot = $PSScriptRoot
    )

    $userHome = ConvertTo-NormalizedPath ([Environment]::GetFolderPath('UserProfile'))
    $localAppData = ConvertTo-NormalizedPath ([Environment]::GetFolderPath('LocalApplicationData'))
    $roamingAppData = ConvertTo-NormalizedPath ([Environment]::GetFolderPath('ApplicationData'))
    $repositoryRoot = ConvertTo-NormalizedPath (Split-Path -Parent $ScriptRoot)

    if (-not $userHome -or -not $localAppData -or -not $roamingAppData -or -not $repositoryRoot) {
        throw 'required_runtime_path_unavailable'
    }

    $targets = @(
        [pscustomobject]@{
            Label        = 'user-temp'
            Path         = Join-Path $localAppData 'Temp'
            UseAgeFilter = $true
            ProcessNames = @()
        },
        [pscustomobject]@{
            Label        = 'runtime-temp'
            Path         = [System.IO.Path]::GetTempPath()
            UseAgeFilter = $true
            ProcessNames = @()
        },
        [pscustomobject]@{
            Label        = 'vscode-extension-cache'
            Path         = Join-Path $roamingAppData 'Code\CachedExtensionVSIXs'
            UseAgeFilter = $false
            ProcessNames = @('Code')
        },
        [pscustomobject]@{
            Label        = 'vscode-cache'
            Path         = Join-Path $roamingAppData 'Code\Cache'
            UseAgeFilter = $false
            ProcessNames = @('Code')
        },
        [pscustomobject]@{
            Label        = 'vscode-data-cache'
            Path         = Join-Path $roamingAppData 'Code\CachedData'
            UseAgeFilter = $false
            ProcessNames = @('Code')
        },
        [pscustomobject]@{
            Label        = 'vscode-crash-cache'
            Path         = Join-Path $roamingAppData 'Code\Crashpad'
            UseAgeFilter = $false
            ProcessNames = @('Code')
        },
        [pscustomobject]@{
            Label        = 'vscode-gpu-cache'
            Path         = Join-Path $roamingAppData 'Code\GPUCache'
            UseAgeFilter = $false
            ProcessNames = @('Code')
        },
        [pscustomobject]@{
            Label        = 'edge-crash-cache'
            Path         = Join-Path $localAppData 'Microsoft\Edge\User Data\Crashpad'
            UseAgeFilter = $false
            ProcessNames = @('msedge', 'msedgewebview2')
        },
        [pscustomobject]@{
            Label        = 'edge-graphics-cache'
            Path         = Join-Path $localAppData 'Microsoft\Edge\User Data\GrShaderCache'
            UseAgeFilter = $false
            ProcessNames = @('msedge', 'msedgewebview2')
        },
        [pscustomobject]@{
            Label        = 'edge-shader-cache'
            Path         = Join-Path $localAppData 'Microsoft\Edge\User Data\ShaderCache'
            UseAgeFilter = $false
            ProcessNames = @('msedge', 'msedgewebview2')
        },
        [pscustomobject]@{
            Label        = 'edge-component-cache'
            Path         = Join-Path $localAppData 'Microsoft\Edge\User Data\component_crx_cache'
            UseAgeFilter = $false
            ProcessNames = @('msedge', 'msedgewebview2')
        },
        [pscustomobject]@{
            Label        = 'edge-extension-cache'
            Path         = Join-Path $localAppData 'Microsoft\Edge\User Data\extensions_crx_cache'
            UseAgeFilter = $false
            ProcessNames = @('msedge', 'msedgewebview2')
        },
        [pscustomobject]@{
            Label        = 'edge-optimization-cache'
            Path         = Join-Path $localAppData 'Microsoft\Edge\User Data\optimization_guide_model_store'
            UseAgeFilter = $false
            ProcessNames = @('msedge', 'msedgewebview2')
        },
        [pscustomobject]@{
            Label        = 'edge-browser-metrics'
            Path         = Join-Path $localAppData 'Microsoft\Edge\User Data\BrowserMetrics'
            UseAgeFilter = $false
            ProcessNames = @('msedge', 'msedgewebview2')
        }
    )

    $normalizedTargets = @()
    $seenPaths = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::OrdinalIgnoreCase
    )
    foreach ($target in $targets) {
        $normalizedPath = ConvertTo-NormalizedPath $target.Path
        if (-not $normalizedPath -or -not $seenPaths.Add($normalizedPath)) {
            continue
        }
        $normalizedTargets += [pscustomobject]@{
            Label        = $target.Label
            Path         = $normalizedPath
            UseAgeFilter = [bool]$target.UseAgeFilter
            ProcessNames = @($target.ProcessNames)
        }
    }

    return [pscustomobject]@{
        UserHome       = $userHome
        RepositoryRoot = $repositoryRoot
        Targets        = $normalizedTargets
        AllowedRoots   = @($normalizedTargets | ForEach-Object { $_.Path })
    }
}

function Test-CleanupTargetPath {
    param(
        [AllowEmptyString()]
        [string]$Path,
        [string[]]$AllowedRoots,
        [string]$UserHome,
        [string]$RepositoryRoot,
        [switch]$AllowAllowedRoot
    )

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return [pscustomobject]@{
            Allowed      = $false
            CanDelete    = $false
            ResolvedPath = ''
            AllowedRoot  = ''
            Reason       = 'empty_path'
        }
    }

    try {
        $resolvedPath = ConvertTo-NormalizedPath $Path
    } catch {
        return [pscustomobject]@{
            Allowed      = $false
            CanDelete    = $false
            ResolvedPath = ''
            AllowedRoot  = ''
            Reason       = 'invalid_path'
        }
    }

    if (-not $resolvedPath) {
        return [pscustomobject]@{
            Allowed      = $false
            CanDelete    = $false
            ResolvedPath = ''
            AllowedRoot  = ''
            Reason       = 'empty_path'
        }
    }

    $driveRoot = ConvertTo-NormalizedPath ([System.IO.Path]::GetPathRoot($resolvedPath))
    if ($driveRoot -and (Test-PathEqual -Left $resolvedPath -Right $driveRoot)) {
        return [pscustomobject]@{
            Allowed      = $false
            CanDelete    = $false
            ResolvedPath = $resolvedPath
            AllowedRoot  = ''
            Reason       = 'drive_root'
        }
    }

    if ($UserHome -and (Test-PathEqual -Left $resolvedPath -Right $UserHome)) {
        return [pscustomobject]@{
            Allowed      = $false
            CanDelete    = $false
            ResolvedPath = $resolvedPath
            AllowedRoot  = ''
            Reason       = 'user_home'
        }
    }

    if (
        $RepositoryRoot -and
        (Test-PathWithin -Path $resolvedPath -Root $RepositoryRoot -AllowEqual)
    ) {
        return [pscustomobject]@{
            Allowed      = $false
            CanDelete    = $false
            ResolvedPath = $resolvedPath
            AllowedRoot  = ''
            Reason       = 'repository_path'
        }
    }

    foreach ($rootValue in @($AllowedRoots)) {
        if ([string]::IsNullOrWhiteSpace($rootValue)) {
            continue
        }
        $allowedRoot = ConvertTo-NormalizedPath $rootValue
        if (Test-PathEqual -Left $resolvedPath -Right $allowedRoot) {
            return [pscustomobject]@{
                Allowed      = [bool]$AllowAllowedRoot
                CanDelete    = $false
                ResolvedPath = $resolvedPath
                AllowedRoot  = $allowedRoot
                Reason       = if ($AllowAllowedRoot) {
                    'allowed_root_enumeration_only'
                } else {
                    'allowed_root_delete_forbidden'
                }
            }
        }
        if (Test-PathWithin -Path $resolvedPath -Root $allowedRoot) {
            return [pscustomobject]@{
                Allowed      = $true
                CanDelete    = $true
                ResolvedPath = $resolvedPath
                AllowedRoot  = $allowedRoot
                Reason       = 'allowed_cache_subpath'
            }
        }
    }

    return [pscustomobject]@{
        Allowed      = $false
        CanDelete    = $false
        ResolvedPath = $resolvedPath
        AllowedRoot  = ''
        Reason       = 'outside_allowed_roots'
    }
}

function Get-ItemBytes {
    param(
        [Parameter(Mandatory = $true)]
        [System.IO.FileSystemInfo]$Item
    )

    if (-not $Item.Exists) {
        return 0
    }
    if (-not $Item.PSIsContainer) {
        return $Item.Length
    }
    if (($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw 'reparse_point'
    }

    $sum = Get-ChildItem `
        -LiteralPath $Item.FullName `
        -Force `
        -Recurse `
        -File `
        -ErrorAction Stop |
        Measure-Object -Property Length -Sum
    if ($null -eq $sum.Sum) {
        return 0
    }
    return [long]$sum.Sum
}

function Test-AnyProcessRunning {
    param(
        [string[]]$Names
    )

    foreach ($name in @($Names)) {
        if ([string]::IsNullOrWhiteSpace($name)) {
            continue
        }
        try {
            if (@(Get-Process -Name $name -ErrorAction Stop).Count -gt 0) {
                return $true
            }
        } catch [Microsoft.PowerShell.Commands.ProcessCommandException] {
            continue
        }
    }
    return $false
}

function New-CleanupResult {
    param(
        [string]$Target,
        [string]$Status,
        [string]$Reason,
        [int]$Planned = 0,
        [int]$Removed = 0,
        [int]$Failed = 0,
        [long]$FreedBytes = 0,
        [bool]$IsDryRun = $true
    )

    return [pscustomobject]@{
        Target  = $Target
        Status  = $Status
        Reason  = $Reason
        Planned = $Planned
        Removed = $Removed
        Failed  = $Failed
        FreedMB = [math]::Round(($FreedBytes / 1MB), 2)
        DryRun  = $IsDryRun
    }
}

function Remove-ValidatedChildItems {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Label,
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string[]]$AllowedRoots,
        [Parameter(Mandatory = $true)]
        [string]$UserHome,
        [Parameter(Mandatory = $true)]
        [string]$RepositoryRoot,
        [datetime]$OlderThan,
        [switch]$UseAgeFilter,
        [switch]$DryRun
    )

    $rootCheck = Test-CleanupTargetPath `
        -Path $Path `
        -AllowedRoots $AllowedRoots `
        -UserHome $UserHome `
        -RepositoryRoot $RepositoryRoot `
        -AllowAllowedRoot
    if (-not $rootCheck.Allowed) {
        return New-CleanupResult `
            -Target $Label `
            -Status 'refused' `
            -Reason $rootCheck.Reason `
            -Failed 1 `
            -IsDryRun ([bool]$DryRun)
    }

    if (-not (Test-Path -LiteralPath $rootCheck.ResolvedPath -PathType Container)) {
        return New-CleanupResult `
            -Target $Label `
            -Status 'missing' `
            -Reason 'target_missing' `
            -IsDryRun ([bool]$DryRun)
    }

    try {
        $rootItem = Get-Item -LiteralPath $rootCheck.ResolvedPath -Force -ErrorAction Stop
        if (($rootItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            return New-CleanupResult `
                -Target $Label `
                -Status 'refused' `
                -Reason 'target_reparse_point' `
                -Failed 1 `
                -IsDryRun ([bool]$DryRun)
        }
        $children = @(Get-ChildItem -LiteralPath $rootCheck.ResolvedPath -Force -ErrorAction Stop)
    } catch {
        return New-CleanupResult `
            -Target $Label `
            -Status 'failed' `
            -Reason 'enumeration_failed' `
            -Failed 1 `
            -IsDryRun ([bool]$DryRun)
    }

    $planned = 0
    $removed = 0
    $failed = 0
    $freedBytes = 0L
    $reasons = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::OrdinalIgnoreCase
    )

    foreach ($child in $children) {
        if ($UseAgeFilter -and $child.LastWriteTime -gt $OlderThan) {
            continue
        }

        if (($child.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            $failed += 1
            $null = $reasons.Add('reparse_point_refused')
            continue
        }

        $childCheck = Test-CleanupTargetPath `
            -Path $child.FullName `
            -AllowedRoots $AllowedRoots `
            -UserHome $UserHome `
            -RepositoryRoot $RepositoryRoot
        if (-not $childCheck.CanDelete) {
            $failed += 1
            $null = $reasons.Add($childCheck.Reason)
            continue
        }

        $planned += 1
        if ($DryRun) {
            continue
        }

        if (-not $PSCmdlet.ShouldProcess($Label, 'remove validated cache child recursively')) {
            continue
        }

        try {
            $bytes = Get-ItemBytes -Item $child
            Remove-Item `
                -LiteralPath $childCheck.ResolvedPath `
                -Force `
                -Recurse `
                -ErrorAction Stop
            $removed += 1
            $freedBytes += $bytes
        } catch {
            $failed += 1
            $null = $reasons.Add('delete_failed')
        }
    }

    $status = if ($DryRun) {
        'preview'
    } elseif ($failed -gt 0) {
        'partial'
    } else {
        'ok'
    }
    $reason = if ($reasons.Count -gt 0) {
        (@($reasons) | Sort-Object) -join ','
    } elseif ($DryRun) {
        'dry_run'
    } else {
        'completed'
    }

    return New-CleanupResult `
        -Target $Label `
        -Status $status `
        -Reason $reason `
        -Planned $planned `
        -Removed $removed `
        -Failed $failed `
        -FreedBytes $freedBytes `
        -IsDryRun ([bool]$DryRun)
}

function Invoke-UserCacheCleanup {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param(
        [ValidateRange(0, 3650)]
        [int]$TempRetentionDays = 7,
        [switch]$Apply,
        [switch]$DryRun
    )

    $effectiveDryRun = [bool]($DryRun -or -not $Apply -or $WhatIfPreference)
    $context = New-CleanupContext
    $cutoff = (Get-Date).AddDays(-$TempRetentionDays)
    $results = @()

    foreach ($target in $context.Targets) {
        if (Test-AnyProcessRunning -Names $target.ProcessNames) {
            $results += New-CleanupResult `
                -Target $target.Label `
                -Status 'skipped' `
                -Reason 'related_process_running' `
                -IsDryRun $effectiveDryRun
            continue
        }

        $results += Remove-ValidatedChildItems `
            -Label $target.Label `
            -Path $target.Path `
            -AllowedRoots $context.AllowedRoots `
            -UserHome $context.UserHome `
            -RepositoryRoot $context.RepositoryRoot `
            -OlderThan $cutoff `
            -UseAgeFilter:$target.UseAgeFilter `
            -DryRun:$effectiveDryRun `
            -WhatIf:$WhatIfPreference
    }

    return $results
}

if ($MyInvocation.InvocationName -ne '.') {
    $isPreview = [bool]($DryRun -or -not $Apply -or $WhatIfPreference)
    if ($isPreview -and -not $DryRun -and -not $WhatIfPreference) {
        Write-Warning 'Preview mode is the default. Pass -Apply to enable validated deletion.'
    }

    try {
        $results = Invoke-UserCacheCleanup `
            -TempRetentionDays $TempRetentionDays `
            -Apply:$Apply `
            -DryRun:$isPreview `
            -WhatIf:$WhatIfPreference
        $results |
            Sort-Object Target |
            Format-Table Target, Status, Reason, Planned, Removed, Failed, FreedMB, DryRun -AutoSize
    } catch {
        [pscustomobject]@{
            Target  = 'cleanup'
            Status  = 'failed'
            Reason  = $_.Exception.GetType().Name
            Planned = 0
            Removed = 0
            Failed  = 1
            FreedMB = 0
            DryRun  = $true
        } | Format-Table -AutoSize
        exit 1
    }
}
