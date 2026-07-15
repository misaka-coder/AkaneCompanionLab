Set-StrictMode -Version Latest

function Test-AkaneSafeInstanceId {
    param([string]$InstanceId)

    return [bool]($InstanceId -match '^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')
}

function Get-AkaneSafeInstanceLogId {
    param([string]$InstanceId)

    if (-not (Test-AkaneSafeInstanceId -InstanceId $InstanceId)) {
        throw "invalid_instance_id"
    }
    return $InstanceId.ToLowerInvariant()
}

function Import-AkaneEnvFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $resolved = [System.IO.Path]::GetFullPath($Path)
    if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
        throw "env_file_not_found"
    }
    foreach ($rawLine in [System.IO.File]::ReadAllLines($resolved)) {
        $line = ([string]$rawLine).Trim()
        if (-not $line -or $line.StartsWith("#")) {
            continue
        }
        if ($line.StartsWith("export ")) {
            $line = $line.Substring(7).Trim()
        }
        $separator = $line.IndexOf("=")
        if ($separator -le 0) {
            throw "invalid_env_file_line"
        }
        $name = $line.Substring(0, $separator).Trim()
        if ($name -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
            throw "invalid_env_file_key"
        }
        $value = $line.Substring($separator + 1).Trim()
        if (
            $value.Length -ge 2 -and
            (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'")))
        ) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        [System.Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
    return $resolved
}

function Test-AkaneInstanceHealth {
    param(
        [object]$Health,
        [string]$ExpectedInstanceId
    )

    if ($null -eq $Health) {
        return $false
    }
    return [bool](
        [string]$Health.status -eq "ok" -and
        [string]$Health.root_binding -eq "valid" -and
        [string]$Health.instance_id -eq $ExpectedInstanceId
    )
}

function Get-AkaneBackendPortDecision {
    param(
        [bool]$PortInUse,
        [object]$Health,
        [string]$ExpectedInstanceId,
        [bool]$ReuseBackend,
        [bool]$ManagedProcess
    )

    if (-not $PortInUse) {
        return "start"
    }
    if (-not (Test-AkaneInstanceHealth -Health $Health -ExpectedInstanceId $ExpectedInstanceId)) {
        return "reject"
    }
    if ($ReuseBackend) {
        return "reuse"
    }
    if ($ManagedProcess) {
        return "stop"
    }
    return "reject"
}
