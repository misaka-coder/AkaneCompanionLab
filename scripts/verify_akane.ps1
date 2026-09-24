[CmdletBinding()]
param(
    [ValidateSet("Quick", "Full", "Acceptance")]
    [string]$Tier = "Quick",
    [switch]$CheckOnly,
    [switch]$Json
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$script:JsonOutput = [bool]$Json
$script:VerificationEnvFile = ""
$script:VerificationDataRoot = ""

function Write-AkaneVerificationMessage {
    param([Parameter(Mandatory = $true)][string]$Message)

    if (-not $script:JsonOutput) {
        Write-Host $Message
    }
}

function New-AkaneCommand {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @()
    )

    return [pscustomobject]@{
        FilePath = $FilePath
        Arguments = @($Arguments)
    }
}

function Resolve-AkanePython {
    $candidates = [System.Collections.Generic.List[object]]::new()
    $venvCandidates = @(
        (Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
        (Join-Path $ProjectRoot "venv\Scripts\python.exe")
    )
    foreach ($candidate in $venvCandidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $candidates.Add((New-AkaneCommand -FilePath $candidate))
        }
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($null -ne $python) {
        $candidates.Add((New-AkaneCommand -FilePath $python.Source))
    }

    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($null -ne $py) {
        $candidates.Add((New-AkaneCommand -FilePath $py.Source -Arguments @("-3")))
    }

    foreach ($candidate in $candidates) {
        if (Test-AkanePythonRuntimeContract -PythonCommand $candidate) {
            return $candidate
        }
    }

    return $null
}

function Test-AkanePythonRuntimeContract {
    param([Parameter(Mandatory = $true)]$PythonCommand)

    $probe = @"
import inspect
import fastapi
import pydantic_settings
from capcore import CapabilityToolSpec
assert "output_schema" in inspect.signature(CapabilityToolSpec).parameters
"@
    $arguments = @($PythonCommand.Arguments) + @("-c", $probe)
    try {
        & $PythonCommand.FilePath @arguments *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Test-AkanePythonModule {
    param(
        [Parameter(Mandatory = $true)]$PythonCommand,
        [Parameter(Mandatory = $true)][string]$ModuleName
    )

    $arguments = @($PythonCommand.Arguments) + @("-m", $ModuleName, "--version")
    try {
        & $PythonCommand.FilePath @arguments *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Resolve-AkaneRuff {
    param($PrimaryPython)

    $pythonCandidates = [System.Collections.Generic.List[object]]::new()
    if ($null -ne $PrimaryPython) {
        $pythonCandidates.Add($PrimaryPython)
    }

    $systemPython = Get-Command python -ErrorAction SilentlyContinue
    if ($null -ne $systemPython) {
        $alreadyPresent = $false
        foreach ($candidate in $pythonCandidates) {
            if ($candidate.FilePath -eq $systemPython.Source -and @($candidate.Arguments).Count -eq 0) {
                $alreadyPresent = $true
                break
            }
        }
        if (-not $alreadyPresent) {
            $pythonCandidates.Add((New-AkaneCommand -FilePath $systemPython.Source))
        }
    }

    foreach ($candidate in $pythonCandidates) {
        if (Test-AkanePythonModule -PythonCommand $candidate -ModuleName "ruff") {
            return New-AkaneCommand `
                -FilePath $candidate.FilePath `
                -Arguments (@($candidate.Arguments) + @("-m", "ruff"))
        }
    }

    $ruff = Get-Command ruff -ErrorAction SilentlyContinue
    if ($null -ne $ruff) {
        return New-AkaneCommand -FilePath $ruff.Source
    }

    return $null
}

function Resolve-AkaneExecutable {
    param([Parameter(Mandatory = $true)][string]$Name)

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        return $null
    }
    foreach ($candidate in @($command.Source, $command.Path, $command.Definition)) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return New-AkaneCommand -FilePath $candidate
        }
    }
    return $null
}

function Resolve-AkaneNpm {
    $node = Resolve-AkaneExecutable -Name "node"
    if ($null -eq $node) {
        return $null
    }

    $nodeRoot = Split-Path -Parent $node.FilePath
    $npmCli = Join-Path $nodeRoot "node_modules\npm\bin\npm-cli.js"
    if (-not (Test-Path -LiteralPath $npmCli -PathType Leaf)) {
        return $null
    }
    return New-AkaneCommand -FilePath $node.FilePath -Arguments @($npmCli)
}

function Resolve-CurrentPowerShell {
    try {
        $process = Get-Process -Id $PID
        if ($process.Path) {
            return New-AkaneCommand -FilePath $process.Path
        }
    } catch {
        # Fall through to explicit executable discovery.
    }

    foreach ($name in @("pwsh", "powershell")) {
        $command = Resolve-AkaneExecutable -Name $name
        if ($null -ne $command) {
            return $command
        }
    }
    return $null
}

function Get-AkaneVerificationContext {
    param([Parameter(Mandatory = $true)][string]$RequestedTier)

    $missing = [System.Collections.Generic.List[string]]::new()
    $notices = [System.Collections.Generic.List[string]]::new()
    $python = Resolve-AkanePython
    if ($null -eq $python) {
        $missing.Add("python runtime: install requirements and a capcore build with the M66 execution contract")
    } else {
        $preferredVenv = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
        if (
            (Test-Path -LiteralPath $preferredVenv -PathType Leaf) -and
            -not $python.FilePath.Equals($preferredVenv, [System.StringComparison]::OrdinalIgnoreCase)
        ) {
            $notices.Add("ignored incompatible .venv Python and selected a compatible PATH Python")
        }
    }

    $ruff = Resolve-AkaneRuff -PrimaryPython $python
    if ($null -eq $ruff) {
        $missing.Add("ruff: install with 'python -m pip install ruff'")
    }

    $git = Resolve-AkaneExecutable -Name "git"
    if ($null -eq $git) {
        $missing.Add("git: install Git and expose it on PATH")
    }

    $powerShell = Resolve-CurrentPowerShell
    if ($null -eq $powerShell) {
        $missing.Add("powershell: install PowerShell 7 or use Windows PowerShell")
    }

    $npm = $null
    $cargo = $null
    if ($RequestedTier -ne "Quick") {
        $npm = Resolve-AkaneNpm
        if ($null -eq $npm) {
            $missing.Add("npm: install Node.js 22 and expose npm on PATH")
        }

        $cargo = Resolve-AkaneExecutable -Name "cargo"
        if ($null -eq $cargo) {
            $missing.Add("cargo: install the Rust stable toolchain")
        }

        $nodeModules = Join-Path $ProjectRoot "desktop_pet_next\node_modules"
        if (-not (Test-Path -LiteralPath $nodeModules -PathType Container)) {
            $missing.Add("desktop dependencies: run 'npm --prefix desktop_pet_next ci'")
        }
    }

    return [pscustomobject]@{
        Python = $python
        Ruff = $ruff
        Git = $git
        PowerShell = $powerShell
        Npm = $npm
        Cargo = $cargo
        Missing = @($missing)
        Notices = @($notices)
    }
}

function New-AkaneStage {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Description,
        [Parameter(Mandatory = $true)]$Command,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds
    )

    return [pscustomobject]@{
        Name = $Name
        Description = $Description
        FilePath = $Command.FilePath
        Arguments = @($Command.Arguments)
        TimeoutSeconds = $TimeoutSeconds
    }
}

function Add-AkaneCommandArguments {
    param(
        [Parameter(Mandatory = $true)]$Command,
        [string[]]$Arguments = @()
    )

    return New-AkaneCommand `
        -FilePath $Command.FilePath `
        -Arguments (@($Command.Arguments) + @($Arguments))
}

function Get-AkaneVerificationStages {
    param(
        [Parameter(Mandatory = $true)][string]$RequestedTier,
        [Parameter(Mandatory = $true)]$Context,
        [Parameter(Mandatory = $true)][string]$TemporaryRoot
    )

    $stages = [System.Collections.Generic.List[object]]::new()
    $stages.Add((New-AkaneStage `
        -Name "python-lint" `
        -Description "Python correctness lint" `
        -Command (Add-AkaneCommandArguments -Command $Context.Ruff -Arguments @("check", ".")) `
        -TimeoutSeconds 300))

    if ($RequestedTier -eq "Quick") {
        $stages.Add((New-AkaneStage `
            -Name "python-quick-regression" `
            -Description "Quick Python regression" `
            -Command (Add-AkaneCommandArguments -Command $Context.Python -Arguments @("-m", "unittest", "tests.quick_regression_suite")) `
            -TimeoutSeconds 600))
    } else {
        $verificationEnvFile = Join-Path $TemporaryRoot "empty.env"
        $stages.Add((New-AkaneStage `
            -Name "python-full-regression" `
            -Description "Full Python regression" `
            -Command (Add-AkaneCommandArguments -Command $Context.Python -Arguments @("-m", "unittest", "discover", "tests")) `
            -TimeoutSeconds 1200))
        $stages.Add((New-AkaneStage `
            -Name "windows-bootstrap-contract" `
            -Description "Windows bootstrap contract" `
            -Command (Add-AkaneCommandArguments -Command $Context.PowerShell -Arguments @(
                "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                (Join-Path $ProjectRoot "scripts\bootstrap_akane_windows.ps1"),
                "-CheckOnly", "-Mode", "Web", "-EnvFile", $verificationEnvFile
            )) `
            -TimeoutSeconds 300))
        $stages.Add((New-AkaneStage `
            -Name "desktop-control-center" `
            -Description "Control center verification and Vite build" `
            -Command (Add-AkaneCommandArguments -Command $Context.Npm -Arguments @(
                "--prefix", "desktop_pet_next", "run", "verify:control-center"
            )) `
            -TimeoutSeconds 600))
        $stages.Add((New-AkaneStage `
            -Name "desktop-instance-storage" `
            -Description "Desktop instance storage smoke" `
            -Command (Add-AkaneCommandArguments -Command $Context.Npm -Arguments @(
                "--prefix", "desktop_pet_next", "run", "smoke:instance-storage"
            )) `
            -TimeoutSeconds 300))
        $stages.Add((New-AkaneStage `
            -Name "tauri-tests" `
            -Description "Tauri Rust tests" `
            -Command (Add-AkaneCommandArguments -Command $Context.Cargo -Arguments @(
                "test", "--manifest-path", "desktop_pet_next/src-tauri/Cargo.toml"
            )) `
            -TimeoutSeconds 900))
        $stages.Add((New-AkaneStage `
            -Name "tauri-check" `
            -Description "Tauri Rust compile check" `
            -Command (Add-AkaneCommandArguments -Command $Context.Cargo -Arguments @(
                "check", "--manifest-path", "desktop_pet_next/src-tauri/Cargo.toml"
            )) `
            -TimeoutSeconds 900))
        $stages.Add((New-AkaneStage `
            -Name "tauri-format" `
            -Description "Tauri Rust format check" `
            -Command (Add-AkaneCommandArguments -Command $Context.Cargo -Arguments @(
                "fmt", "--manifest-path", "desktop_pet_next/src-tauri/Cargo.toml", "--", "--check"
            )) `
            -TimeoutSeconds 300))

        if ($RequestedTier -eq "Acceptance") {
            $stages.Add((New-AkaneStage `
                -Name "m65-e5-two-instance" `
                -Description "M65-E5 real two-instance acceptance" `
                -Command (Add-AkaneCommandArguments -Command $Context.Python -Arguments @(
                    (Join-Path $ProjectRoot "scripts\smoke_m65_e5_two_instance.py")
                )) `
                -TimeoutSeconds 900))
        }

        $releaseRoot = Join-Path $TemporaryRoot "public-alpha"
        $stages.Add((New-AkaneStage `
            -Name "public-export" `
            -Description "Public Alpha export" `
            -Command (Add-AkaneCommandArguments -Command $Context.PowerShell -Arguments @(
                "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                (Join-Path $ProjectRoot "scripts\export_public_alpha.ps1"),
                "-OutputPath", $releaseRoot
            )) `
            -TimeoutSeconds 600))
        $stages.Add((New-AkaneStage `
            -Name "public-audit" `
            -Description "Public Alpha audit" `
            -Command (Add-AkaneCommandArguments -Command $Context.PowerShell -Arguments @(
                "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                (Join-Path $ProjectRoot "scripts\audit_public_release.ps1"),
                "-ReleaseRoot", $releaseRoot
            )) `
            -TimeoutSeconds 600))
    }

    $stages.Add((New-AkaneStage `
        -Name "diff-hygiene" `
        -Description "Git diff hygiene" `
        -Command (Add-AkaneCommandArguments -Command $Context.Git -Arguments @("diff", "--check")) `
        -TimeoutSeconds 120))
    return @($stages)
}

function ConvertTo-AkaneNativeArgument {
    param([AllowEmptyString()][string]$Value)

    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') {
        return $Value
    }

    $builder = [System.Text.StringBuilder]::new()
    [void]$builder.Append([char]34)
    $backslashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq [char]92) {
            $backslashes += 1
            continue
        }
        if ($character -eq [char]34) {
            if ($backslashes -gt 0) {
                [void]$builder.Append(('\' * ($backslashes * 2)))
            }
            [void]$builder.Append('\')
            [void]$builder.Append([char]34)
            $backslashes = 0
            continue
        }
        if ($backslashes -gt 0) {
            [void]$builder.Append(('\' * $backslashes))
            $backslashes = 0
        }
        [void]$builder.Append($character)
    }
    if ($backslashes -gt 0) {
        [void]$builder.Append(('\' * ($backslashes * 2)))
    }
    [void]$builder.Append([char]34)
    return $builder.ToString()
}

function Join-AkaneNativeArguments {
    param([string[]]$ArgumentList = @())

    return (@($ArgumentList | ForEach-Object { ConvertTo-AkaneNativeArgument -Value $_ }) -join " ")
}

function Set-AkaneVerificationEnvironment {
    param([Parameter(Mandatory = $true)]$StartInfo)

    $StartInfo.EnvironmentVariables["AKANE_ENV_FILE"] = $script:VerificationEnvFile
    $StartInfo.EnvironmentVariables["AKANE_DATA_ROOT"] = $script:VerificationDataRoot
    $StartInfo.EnvironmentVariables["AKANE_INSTANCE_ID"] = "local-default"
    $StartInfo.EnvironmentVariables["PYTHONUTF8"] = "1"
    $StartInfo.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"] = "1"

    foreach ($name in @($StartInfo.EnvironmentVariables.Keys)) {
        if ($name -match '(?i)(_API_KEY|_TOKEN|_SECRET|_PASSWORD)$') {
            $StartInfo.EnvironmentVariables[$name] = ""
        }
    }
}

function Stop-AkaneVerificationProcess {
    param([Parameter(Mandatory = $true)]$Process)

    if ($Process.HasExited) {
        return
    }

    $taskkill = Get-Command taskkill.exe -ErrorAction SilentlyContinue
    if ($null -ne $taskkill) {
        try {
            & $taskkill.Source /PID $Process.Id /T /F *> $null
            return
        } catch {
            # Fall through to the direct process kill.
        }
    }
    try {
        $Process.Kill()
    } catch {
        # The process may have exited between the checks.
    }
}

function Invoke-AkaneVerificationStage {
    param([Parameter(Mandatory = $true)]$Stage)

    Write-AkaneVerificationMessage ("[RUN] {0} (timeout {1}s)" -f $Stage.Description, $Stage.TimeoutSeconds)
    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    $process = $null
    $stdoutTask = $null
    $stderrTask = $null
    try {
        $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
        $startInfo.FileName = $Stage.FilePath
        $startInfo.Arguments = Join-AkaneNativeArguments -ArgumentList $Stage.Arguments
        $startInfo.WorkingDirectory = $ProjectRoot
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = $true
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true
        Set-AkaneVerificationEnvironment -StartInfo $startInfo

        $process = [System.Diagnostics.Process]::new()
        $process.StartInfo = $startInfo
        if (-not $process.Start()) {
            throw "process_start_returned_false"
        }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()

        $nextHeartbeat = 15
        $timedOut = $false
        while (-not $process.WaitForExit(1000)) {
            if ($watch.Elapsed.TotalSeconds -ge $Stage.TimeoutSeconds) {
                $timedOut = $true
                Stop-AkaneVerificationProcess -Process $process
                [void]$process.WaitForExit(5000)
                break
            }
            if ($watch.Elapsed.TotalSeconds -ge $nextHeartbeat) {
                Write-AkaneVerificationMessage ("[WAIT] {0}: {1:n0}s elapsed" -f $Stage.Description, $watch.Elapsed.TotalSeconds)
                $nextHeartbeat += 15
            }
        }

        $stdout = if ($null -ne $stdoutTask) { $stdoutTask.Result.TrimEnd() } else { "" }
        $stderr = if ($null -ne $stderrTask) { $stderrTask.Result.TrimEnd() } else { "" }
        if ($stdout) {
            Write-AkaneVerificationMessage $stdout
        }
        if ($stderr) {
            Write-AkaneVerificationMessage $stderr
        }

        $watch.Stop()
        if ($timedOut) {
            Write-AkaneVerificationMessage ("[FAIL] {0}: timeout after {1:n1}s" -f $Stage.Description, $watch.Elapsed.TotalSeconds)
            return [pscustomobject]@{
                name = $Stage.Name
                status = "failed"
                reason = "timeout"
                elapsed_seconds = [Math]::Round($watch.Elapsed.TotalSeconds, 2)
            }
        }

        $exitCode = $process.ExitCode
        if ($exitCode -ne 0) {
            Write-AkaneVerificationMessage ("[FAIL] {0}: exit code {1}" -f $Stage.Description, $exitCode)
            return [pscustomobject]@{
                name = $Stage.Name
                status = "failed"
                reason = "exit_code:$exitCode"
                elapsed_seconds = [Math]::Round($watch.Elapsed.TotalSeconds, 2)
            }
        }

        Write-AkaneVerificationMessage ("[OK] {0}: {1:n1}s" -f $Stage.Description, $watch.Elapsed.TotalSeconds)
        return [pscustomobject]@{
            name = $Stage.Name
            status = "ok"
            reason = ""
            elapsed_seconds = [Math]::Round($watch.Elapsed.TotalSeconds, 2)
        }
    } catch {
        $watch.Stop()
        if ($null -ne $process) {
            Stop-AkaneVerificationProcess -Process $process
        }
        Write-AkaneVerificationMessage ("[FAIL] {0}: process launch failed" -f $Stage.Description)
        return [pscustomobject]@{
            name = $Stage.Name
            status = "failed"
            reason = "launch_failed"
            elapsed_seconds = [Math]::Round($watch.Elapsed.TotalSeconds, 2)
        }
    } finally {
        if ($null -ne $process) {
            $process.Dispose()
        }
    }
}

function New-AkaneVerificationTemporaryRoot {
    $systemTemp = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\', '/')
    $root = [System.IO.Path]::GetFullPath(
        (Join-Path $systemTemp ("akane-verification-{0}" -f [Guid]::NewGuid().ToString("N")))
    )
    $requiredPrefix = $systemTemp + [System.IO.Path]::DirectorySeparatorChar
    if (-not $root.StartsWith($requiredPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "unsafe_verification_temp_root"
    }

    [void][System.IO.Directory]::CreateDirectory($root)
    $script:VerificationEnvFile = Join-Path $root "empty.env"
    $script:VerificationDataRoot = Join-Path $root "data"
    [System.IO.File]::WriteAllText($script:VerificationEnvFile, "", [System.Text.UTF8Encoding]::new($false))
    [void][System.IO.Directory]::CreateDirectory($script:VerificationDataRoot)
    return $root
}

function Remove-AkaneVerificationTemporaryRoot {
    param([Parameter(Mandatory = $true)][string]$Path)

    $systemTemp = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\', '/')
    $resolved = [System.IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
    $requiredPrefix = $systemTemp + [System.IO.Path]::DirectorySeparatorChar + "akane-verification-"
    if (-not $resolved.StartsWith($requiredPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "unsafe_verification_cleanup_root"
    }
    if (Test-Path -LiteralPath $resolved) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}

function Write-AkaneVerificationSummary {
    param([Parameter(Mandatory = $true)]$Summary)

    if ($script:JsonOutput) {
        Write-Output ($Summary | ConvertTo-Json -Depth 6 -Compress)
        return
    }

    Write-Host ""
    Write-Host ("Akane verification: {0} ({1})" -f $Summary.status, $Summary.tier)
    if ($Summary.reason) {
        Write-Host ("Reason: {0}" -f $Summary.reason)
    }
    foreach ($stage in @($Summary.stages)) {
        $detail = if ($stage.reason) { " - $($stage.reason)" } else { "" }
        Write-Host ("  {0}: {1} ({2}s){3}" -f $stage.name, $stage.status, $stage.elapsed_seconds, $detail)
    }
    foreach ($missing in @($Summary.missing)) {
        Write-Host ("  missing: {0}" -f $missing)
    }
    foreach ($notice in @($Summary.notices)) {
        Write-Host ("  notice: {0}" -f $notice)
    }
}

$overallWatch = [System.Diagnostics.Stopwatch]::StartNew()
$temporaryRoot = ""
$stageResults = [System.Collections.Generic.List[object]]::new()
$summaryStatus = "ok"
$summaryReason = ""
$exitCode = 0
$context = $null
$stages = @()

try {
    $context = Get-AkaneVerificationContext -RequestedTier $Tier
    if (@($context.Missing).Count -gt 0) {
        $summaryStatus = "failed"
        $summaryReason = "preflight_failed"
        $exitCode = 2
    } else {
        $planRoot = if ($CheckOnly) { "<verification-temp>" } else { New-AkaneVerificationTemporaryRoot }
        if (-not $CheckOnly) {
            $temporaryRoot = $planRoot
        }
        $stages = @(Get-AkaneVerificationStages -RequestedTier $Tier -Context $context -TemporaryRoot $planRoot)

        if ($CheckOnly) {
            foreach ($stage in $stages) {
                $stageResults.Add([pscustomobject]@{
                    name = $stage.Name
                    status = "planned"
                    reason = ""
                    elapsed_seconds = 0
                    timeout_seconds = $stage.TimeoutSeconds
                })
            }
        } else {
            for ($index = 0; $index -lt $stages.Count; $index += 1) {
                $stage = $stages[$index]
                $result = Invoke-AkaneVerificationStage -Stage $stage
                $stageResults.Add($result)
                if ($result.status -ne "ok") {
                    $summaryStatus = "failed"
                    $summaryReason = "stage_failed:$($stage.Name)"
                    $exitCode = 1
                    for ($remaining = $index + 1; $remaining -lt $stages.Count; $remaining += 1) {
                        $stageResults.Add([pscustomobject]@{
                            name = $stages[$remaining].Name
                            status = "not_run"
                            reason = "previous_stage_failed"
                            elapsed_seconds = 0
                        })
                    }
                    break
                }
            }
        }
    }
} catch {
    $summaryStatus = "failed"
    $summaryReason = "verification_internal_error"
    $exitCode = 1
    Write-AkaneVerificationMessage ("[FAIL] Verification internal error: {0}" -f $_.Exception.Message)
} finally {
    if ($temporaryRoot) {
        try {
            Remove-AkaneVerificationTemporaryRoot -Path $temporaryRoot
        } catch {
            $summaryStatus = "failed"
            $summaryReason = "temporary_cleanup_failed"
            $exitCode = 1
        }
    }
}

$overallWatch.Stop()
$missingItems = [System.Collections.Generic.List[string]]::new()
$noticeItems = [System.Collections.Generic.List[string]]::new()
if ($null -ne $context) {
    foreach ($missingItem in @($context.Missing)) {
        $missingItems.Add($missingItem)
    }
    foreach ($noticeItem in @($context.Notices)) {
        $noticeItems.Add($noticeItem)
    }
}
$summary = [pscustomobject]@{
    status = $summaryStatus
    reason = $summaryReason
    tier = $Tier
    check_only = [bool]$CheckOnly
    elapsed_seconds = [Math]::Round($overallWatch.Elapsed.TotalSeconds, 2)
    stages = @($stageResults)
    missing = @($missingItems)
    notices = @($noticeItems)
}
Write-AkaneVerificationSummary -Summary $summary
exit $exitCode
