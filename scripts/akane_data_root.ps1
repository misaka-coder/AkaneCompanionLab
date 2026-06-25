function Resolve-AkaneDataRoot {
    $explicit = [string]$env:AKANE_DATA_ROOT
    if ($explicit.Trim()) {
        return [System.IO.Path]::GetFullPath($explicit.Trim())
    }

    $base = [string]$env:LOCALAPPDATA
    if (-not $base.Trim()) {
        $profile = [string]$env:USERPROFILE
        if (-not $profile.Trim()) {
            throw "akane_data_root_unavailable"
        }
        $base = Join-Path $profile "AppData\Local"
    }
    return [System.IO.Path]::GetFullPath((Join-Path $base "Akane"))
}

function Copy-AkaneMissingTree {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Source,
        [Parameter(Mandatory = $true)]
        [string]$Destination
    )

    $sourcePath = [System.IO.Path]::GetFullPath($Source)
    $destinationPath = [System.IO.Path]::GetFullPath($Destination)
    if (
        -not (Test-Path -LiteralPath $sourcePath -PathType Container) -or
        $sourcePath.TrimEnd("\") -eq $destinationPath.TrimEnd("\")
    ) {
        return [pscustomobject]@{ Copied = 0; Skipped = 0; Failed = 0 }
    }

    New-Item -ItemType Directory -Force -Path $destinationPath | Out-Null
    $copied = 0
    $skipped = 0
    $failed = 0
    foreach ($item in Get-ChildItem -LiteralPath $sourcePath -Force) {
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            $skipped += 1
            continue
        }

        $target = Join-Path $destinationPath $item.Name
        if ($item.PSIsContainer) {
            $nested = Copy-AkaneMissingTree -Source $item.FullName -Destination $target
            $copied += $nested.Copied
            $skipped += $nested.Skipped
            $failed += $nested.Failed
            continue
        }

        if (Test-Path -LiteralPath $target) {
            $skipped += 1
            continue
        }
        try {
            Copy-Item -LiteralPath $item.FullName -Destination $target -ErrorAction Stop
            $copied += 1
        } catch {
            $failed += 1
        }
    }

    return [pscustomobject]@{ Copied = $copied; Skipped = $skipped; Failed = $failed }
}

function Initialize-AkaneDataRoot {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot,
        [switch]$ReadOnly
    )

    $root = Resolve-AkaneDataRoot
    $usersData = Join-Path $root "users_data"
    $characters = Join-Path $root "characters"
    $state = Join-Path $root "state"
    $logs = Join-Path $root "logs"

    if ($ReadOnly) {
        return [pscustomobject]@{
            Root = $root
            UsersData = $usersData
            Characters = $characters
            State = $state
            Logs = $logs
            Copied = 0
            Skipped = 0
            Failed = 0
        }
    }

    foreach ($directory in @($root, $usersData, $characters, $state, $logs)) {
        New-Item -ItemType Directory -Force -Path $directory | Out-Null
    }

    $legacyUsersData = Join-Path $ProjectRoot "users_data"
    $legacyCharacters = Join-Path $ProjectRoot "desktop_pet_creator_kit\characters"
    $userResult = Copy-AkaneMissingTree -Source $legacyUsersData -Destination $usersData
    $characterResult = Copy-AkaneMissingTree -Source $legacyCharacters -Destination $characters

    return [pscustomobject]@{
        Root = $root
        UsersData = $usersData
        Characters = $characters
        State = $state
        Logs = $logs
        Copied = $userResult.Copied + $characterResult.Copied
        Skipped = $userResult.Skipped + $characterResult.Skipped
        Failed = $userResult.Failed + $characterResult.Failed
    }
}
