[CmdletBinding()]
param(
    [ValidateSet("personal", "finance", "both")]
    [string]$Bot = "",
    [string]$SshHost = "akane-vps",
    [ValidateRange(30, 300)]
    [int]$TimeoutSeconds = 150,
    [string]$OutputDirectory = "",
    [switch]$StatusOnly,
    [switch]$NoOpen
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$global:OutputEncoding = New-Object System.Text.UTF8Encoding($false)

$botProfiles = @{
    personal = [pscustomobject]@{
        BotId = "personal"
        Label = "Personal"
        Container = "akane-napcat-personal"
        ExpectedQq = "2184046306"
        OneBotPort = 3001
        OneBotConfig = "/var/lib/akane/personal/napcat/config/onebot11_2184046306.json"
    }
    finance = [pscustomobject]@{
        BotId = "finance"
        Label = "Finance"
        Container = "akane-napcat-finance"
        ExpectedQq = "2483893575"
        OneBotPort = 3002
        OneBotConfig = "/var/lib/akane/finance/napcat/config/onebot11_2483893575.json"
    }
}

function Resolve-NativeCommand {
    param([Parameter(Mandatory = $true)][string]$Name)

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw "missing_command:$Name"
    }
    foreach ($candidate in @($command.Source, $command.Path, $command.Definition)) {
        if ($candidate) {
            return [string]$candidate
        }
    }
    throw "missing_command:$Name"
}

$script:SshCommand = Resolve-NativeCommand -Name "ssh"
$script:ScpCommand = Resolve-NativeCommand -Name "scp"

function Invoke-RemoteScript {
    param(
        [Parameter(Mandatory = $true)][string]$ScriptText,
        [string[]]$Arguments = @()
    )

    # Windows PowerShell writes a UTF-8 BOM and CRLF to native-command stdin on
    # some hosts. Normalize both before Bash parses the remotely supplied script.
    $remoteCommand = "sed '1s/^\xEF\xBB\xBF//' | tr -d '\r' | bash -s --"
    $sshArguments = @($SshHost, $remoteCommand) + @($Arguments)
    $output = @($ScriptText | & $script:SshCommand @sshArguments 2>&1)
    $exitCode = $LASTEXITCODE
    return [pscustomobject]@{
        ExitCode = $exitCode
        Lines = @($output | ForEach-Object { [string]$_ })
    }
}

function Get-ResultValue {
    param(
        [Parameter(Mandatory = $true)][string[]]$Lines,
        [Parameter(Mandatory = $true)][string]$Name
    )

    $prefix = "AKANE_$Name="
    $line = @($Lines | Where-Object { $_.StartsWith($prefix) } | Select-Object -Last 1)
    if ($line.Count -eq 0) {
        return ""
    }
    return $line[0].Substring($prefix.Length)
}

function Invoke-StatusCheck {
    param([Parameter(Mandatory = $true)]$Profile)

    $statusScript = @'
set -u
bot_id=$1
expected_qq=$2

admin_token=$(sudo -n bash -c '. /etc/akane/host.env; printf %s "$AKANE_ADMIN_TOKEN"') || {
    echo AKANE_RESULT=STATUS_FAILED
    echo AKANE_REASON=admin_token_unavailable
    exit 20
}
response=$(curl -sS --max-time 15 -X POST \
    -H "Authorization: Bearer $admin_token" \
    "http://127.0.0.1:10001/api/bots/$bot_id/qq/self-check") || {
    echo AKANE_RESULT=STATUS_FAILED
    echo AKANE_REASON=host_self_check_unreachable
    exit 21
}
connected=$(printf %s "$response" | jq -r '.data.status // empty')
actual_qq=$(printf %s "$response" | jq -r '.data.bot_qq // empty')
ok=$(printf %s "$response" | jq -r '.data.ok // false')

if [ "$ok" = true ] && [ "$connected" = connected ] && [ "$actual_qq" = "$expected_qq" ]; then
    echo AKANE_RESULT=STATUS_OK
    echo "AKANE_ACTUAL_QQ=$actual_qq"
    exit 0
fi

echo AKANE_RESULT=STATUS_FAILED
echo "AKANE_REASON=self_check_${connected:-unknown}_${actual_qq:-unknown}"
exit 22
'@

    return Invoke-RemoteScript -ScriptText $statusScript -Arguments @(
        $Profile.BotId,
        $Profile.ExpectedQq
    )
}

function New-RemoteQr {
    param(
        [Parameter(Mandatory = $true)]$Profile,
        [Parameter(Mandatory = $true)][string]$RemoteQrPath
    )

    $prepareScript = @'
set -u
container=$1
expected_qq=$2
onebot_port=$3
onebot_config=$4
remote_qr=$5

if ! sudo -n docker inspect "$container" >/dev/null 2>&1; then
    echo AKANE_RESULT=PREPARE_FAILED
    echo AKANE_REASON=container_not_found
    exit 30
fi
if ! sudo -n test -r "$onebot_config"; then
    echo AKANE_RESULT=PREPARE_FAILED
    echo AKANE_REASON=onebot_config_missing
    exit 31
fi

sudo -n docker exec "$container" sh -c 'rm -f /app/napcat/cache/qrcode.png' >/dev/null 2>&1 || true
sudo -n rm -f -- "$remote_qr"
if ! sudo -n docker restart "$container" >/dev/null; then
    echo AKANE_RESULT=PREPARE_FAILED
    echo AKANE_REASON=container_restart_failed
    exit 32
fi

token=$(sudo -n jq -r '.network.httpServers[0].token // empty' "$onebot_config")
if [ -z "$token" ]; then
    echo AKANE_RESULT=PREPARE_FAILED
    echo AKANE_REASON=onebot_token_missing
    exit 33
fi

deadline=$(( $(date +%s) + 45 ))
while [ "$(date +%s)" -lt "$deadline" ]; do
    response=$(curl -sS --max-time 2 \
        -H "Authorization: Bearer $token" \
        "http://127.0.0.1:$onebot_port/get_login_info" 2>/dev/null || true)
    actual_qq=$(printf %s "$response" | jq -r '.data.user_id // empty' 2>/dev/null || true)
    if [ "$actual_qq" = "$expected_qq" ]; then
        echo AKANE_RESULT=LOGIN_OK_NO_QR
        echo "AKANE_ACTUAL_QQ=$actual_qq"
        exit 0
    fi
    if [ -n "$actual_qq" ] && [ "$actual_qq" != "$expected_qq" ]; then
        echo AKANE_RESULT=ACCOUNT_MISMATCH
        echo "AKANE_ACTUAL_QQ=$actual_qq"
        exit 34
    fi
    if sudo -n docker exec "$container" test -s /app/napcat/cache/qrcode.png >/dev/null 2>&1; then
        sleep 1
        if ! sudo -n docker cp "$container:/app/napcat/cache/qrcode.png" "$remote_qr" >/dev/null; then
            echo AKANE_RESULT=PREPARE_FAILED
            echo AKANE_REASON=qr_copy_failed
            exit 35
        fi
        sudo -n chmod 0644 "$remote_qr"
        qr_size=$(sudo -n stat -c %s "$remote_qr" 2>/dev/null || printf 0)
        if [ "$qr_size" -lt 100 ]; then
            echo AKANE_RESULT=PREPARE_FAILED
            echo AKANE_REASON=qr_file_invalid
            exit 36
        fi
        echo AKANE_RESULT=QR_READY
        echo "AKANE_REMOTE_QR=$remote_qr"
        echo "AKANE_QR_SIZE=$qr_size"
        exit 0
    fi
    sleep 1
done

echo AKANE_RESULT=PREPARE_FAILED
echo AKANE_REASON=qr_not_generated
exit 37
'@

    return Invoke-RemoteScript -ScriptText $prepareScript -Arguments @(
        $Profile.Container,
        $Profile.ExpectedQq,
        [string]$Profile.OneBotPort,
        $Profile.OneBotConfig,
        $RemoteQrPath
    )
}

function Wait-RemoteLogin {
    param([Parameter(Mandatory = $true)]$Profile)

    $waitScript = @'
set -u
expected_qq=$1
onebot_port=$2
onebot_config=$3
timeout_seconds=$4

token=$(sudo -n jq -r '.network.httpServers[0].token // empty' "$onebot_config")
if [ -z "$token" ]; then
    echo AKANE_RESULT=LOGIN_FAILED
    echo AKANE_REASON=onebot_token_missing
    exit 40
fi

deadline=$(( $(date +%s) + timeout_seconds ))
while [ "$(date +%s)" -lt "$deadline" ]; do
    response=$(curl -sS --max-time 2 \
        -H "Authorization: Bearer $token" \
        "http://127.0.0.1:$onebot_port/get_login_info" 2>/dev/null || true)
    actual_qq=$(printf %s "$response" | jq -r '.data.user_id // empty' 2>/dev/null || true)
    if [ "$actual_qq" = "$expected_qq" ]; then
        echo AKANE_RESULT=LOGIN_OK
        echo "AKANE_ACTUAL_QQ=$actual_qq"
        exit 0
    fi
    if [ -n "$actual_qq" ] && [ "$actual_qq" != "$expected_qq" ]; then
        echo AKANE_RESULT=ACCOUNT_MISMATCH
        echo "AKANE_ACTUAL_QQ=$actual_qq"
        exit 41
    fi
    sleep 2
done

echo AKANE_RESULT=LOGIN_TIMEOUT
echo AKANE_REASON=scan_not_confirmed
exit 42
'@

    return Invoke-RemoteScript -ScriptText $waitScript -Arguments @(
        $Profile.ExpectedQq,
        [string]$Profile.OneBotPort,
        $Profile.OneBotConfig,
        [string]$TimeoutSeconds
    )
}

function Invoke-FinalVerification {
    param([Parameter(Mandatory = $true)]$Profile)

    $verifyScript = @'
set -u
bot_id=$1
label=$2
expected_qq=$3
onebot_port=$4
onebot_config=$5

admin_token=$(sudo -n bash -c '. /etc/akane/host.env; printf %s "$AKANE_ADMIN_TOKEN"') || {
    echo AKANE_RESULT=VERIFY_FAILED
    echo AKANE_REASON=admin_token_unavailable
    exit 50
}
self_check=$(curl -sS --max-time 15 -X POST \
    -H "Authorization: Bearer $admin_token" \
    "http://127.0.0.1:10001/api/bots/$bot_id/qq/self-check") || {
    echo AKANE_RESULT=VERIFY_FAILED
    echo AKANE_REASON=host_self_check_unreachable
    exit 51
}
self_ok=$(printf %s "$self_check" | jq -r '.data.ok // false')
self_status=$(printf %s "$self_check" | jq -r '.data.status // empty')
self_qq=$(printf %s "$self_check" | jq -r '.data.bot_qq // empty')
if [ "$self_ok" != true ] || [ "$self_status" != connected ] || [ "$self_qq" != "$expected_qq" ]; then
    echo AKANE_RESULT=VERIFY_FAILED
    echo "AKANE_REASON=self_check_${self_status:-unknown}_${self_qq:-unknown}"
    exit 52
fi

onebot_token=$(sudo -n jq -r '.network.httpServers[0].token // empty' "$onebot_config")
master_qq=$(sudo -n bash -c '. /etc/akane/host.env; printf %s "$MASTER_QQ"') || true
if [ -z "$onebot_token" ] || [ -z "$master_qq" ]; then
    echo AKANE_RESULT=VERIFY_FAILED
    echo AKANE_REASON=send_test_config_missing
    exit 53
fi

payload=$(jq -nc \
    --arg user_id "$master_qq" \
    --arg message "Akane cloud relogin: $label bot is online." \
    '{user_id: ($user_id | tonumber), message: $message}')
send_result=$(curl -sS --max-time 15 -X POST \
    -H "Authorization: Bearer $onebot_token" \
    -H 'Content-Type: application/json' \
    --data-binary "$payload" \
    "http://127.0.0.1:$onebot_port/send_private_msg") || {
    echo AKANE_RESULT=VERIFY_FAILED
    echo AKANE_REASON=send_test_unreachable
    exit 54
}
send_status=$(printf %s "$send_result" | jq -r '.status // empty')
send_retcode=$(printf %s "$send_result" | jq -r '.retcode // -1')
if [ "$send_status" != ok ] || [ "$send_retcode" != 0 ]; then
    echo AKANE_RESULT=VERIFY_FAILED
    echo "AKANE_REASON=send_test_${send_status:-unknown}_$send_retcode"
    exit 55
fi

echo AKANE_RESULT=VERIFY_OK
echo "AKANE_ACTUAL_QQ=$self_qq"
echo AKANE_SEND_STATUS=ok
'@

    return Invoke-RemoteScript -ScriptText $verifyScript -Arguments @(
        $Profile.BotId,
        $Profile.Label,
        $Profile.ExpectedQq,
        [string]$Profile.OneBotPort,
        $Profile.OneBotConfig
    )
}

function Remove-RemoteQr {
    param([Parameter(Mandatory = $true)][string]$RemoteQrPath)

    $cleanupScript = @'
set -u
remote_qr=$1
case "$remote_qr" in
    /tmp/akane-qq-login-*.png) sudo -n rm -f -- "$remote_qr" ;;
    *) exit 60 ;;
esac
'@
    return Invoke-RemoteScript -ScriptText $cleanupScript -Arguments @($RemoteQrPath)
}

function Invoke-BotRelogin {
    param([Parameter(Mandatory = $true)]$Profile)

    Write-Host ""
    Write-Host ("[{0}] QQ {1}" -f $Profile.Label, $Profile.ExpectedQq)

    if ($StatusOnly) {
        $status = Invoke-StatusCheck -Profile $Profile
        if ($status.ExitCode -eq 0 -and (Get-ResultValue -Lines $status.Lines -Name "RESULT") -eq "STATUS_OK") {
            Write-Host "Status: connected"
            return
        }
        $reason = Get-ResultValue -Lines $status.Lines -Name "REASON"
        throw "status_check_failed:$reason"
    }

    $stamp = [DateTime]::Now.ToString("yyyyMMdd-HHmmss-fff")
    $fileName = "akane-qq-login-$($Profile.BotId)-$stamp.png"
    $remoteQrPath = "/tmp/$fileName"
    $localQrPath = Join-Path $script:QrOutputDirectory $fileName

    Write-Host "Restarting only this Bot and requesting a fresh QR..."
    $prepared = New-RemoteQr -Profile $Profile -RemoteQrPath $remoteQrPath
    $prepareResult = Get-ResultValue -Lines $prepared.Lines -Name "RESULT"
    if ($prepared.ExitCode -ne 0) {
        $reason = Get-ResultValue -Lines $prepared.Lines -Name "REASON"
        if ($prepareResult -eq "ACCOUNT_MISMATCH") {
            $actual = Get-ResultValue -Lines $prepared.Lines -Name "ACTUAL_QQ"
            throw "account_mismatch:expected=$($Profile.ExpectedQq),actual=$actual"
        }
        throw "qr_prepare_failed:$reason"
    }

    if ($prepareResult -eq "QR_READY") {
        $remoteQr = Get-ResultValue -Lines $prepared.Lines -Name "REMOTE_QR"
        if ($remoteQr -ne $remoteQrPath) {
            throw "qr_prepare_failed:unexpected_remote_path"
        }

        & $script:ScpCommand "${SshHost}:$remoteQrPath" $localQrPath
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $localQrPath -PathType Leaf)) {
            throw "qr_download_failed"
        }
        $null = Remove-RemoteQr -RemoteQrPath $remoteQrPath

        Write-Host ("Fresh QR: {0}" -f $localQrPath)
        if (-not $NoOpen) {
            try {
                Start-Process -FilePath $localQrPath
            } catch {
                Write-Warning "Could not open the QR automatically. Open the path printed above."
            }
        }
        Write-Host ("Scan now. Waiting up to {0} seconds for QQ {1}..." -f $TimeoutSeconds, $Profile.ExpectedQq)

        $login = Wait-RemoteLogin -Profile $Profile
        $loginResult = Get-ResultValue -Lines $login.Lines -Name "RESULT"
        if ($login.ExitCode -ne 0 -or $loginResult -ne "LOGIN_OK") {
            if ($loginResult -eq "ACCOUNT_MISMATCH") {
                $actual = Get-ResultValue -Lines $login.Lines -Name "ACTUAL_QQ"
                throw "account_mismatch:expected=$($Profile.ExpectedQq),actual=$actual"
            }
            $reason = Get-ResultValue -Lines $login.Lines -Name "REASON"
            throw "login_not_confirmed:$reason"
        }
    } elseif ($prepareResult -eq "LOGIN_OK_NO_QR") {
        Write-Host "Fast login succeeded; no scan was needed."
    } else {
        throw "qr_prepare_failed:unexpected_result_$prepareResult"
    }

    Write-Host "Running Host self-check and a real private-message test..."
    $verified = Invoke-FinalVerification -Profile $Profile
    $verifyResult = Get-ResultValue -Lines $verified.Lines -Name "RESULT"
    if ($verified.ExitCode -ne 0 -or $verifyResult -ne "VERIFY_OK") {
        $reason = Get-ResultValue -Lines $verified.Lines -Name "REASON"
        throw "final_verification_failed:$reason"
    }

    Write-Host ("SUCCESS: {0} is connected as QQ {1}; outbound message status is ok." -f $Profile.Label, $Profile.ExpectedQq)
}

if (-not $Bot) {
    Write-Host "Akane cloud Bot self-service relogin"
    Write-Host "  1. Personal (QQ 2184046306)"
    Write-Host "  2. Finance  (QQ 2483893575)"
    Write-Host "  3. Both (Personal first, then Finance)"
    $choice = Read-Host "Choose 1, 2, or 3"
    $Bot = switch ($choice) {
        "1" { "personal" }
        "2" { "finance" }
        "3" { "both" }
        default { throw "invalid_choice" }
    }
}

if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path ([System.IO.Path]::GetTempPath()) "AkaneBotLogin"
}
$script:QrOutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
if (-not (Test-Path -LiteralPath $script:QrOutputDirectory -PathType Container)) {
    $null = New-Item -ItemType Directory -Path $script:QrOutputDirectory -Force
}

try {
    $preflight = Invoke-RemoteScript -ScriptText "sudo -n true && command -v docker jq curl >/dev/null" -Arguments @()
    if ($preflight.ExitCode -ne 0) {
        throw "remote_preflight_failed:ssh_or_passwordless_sudo"
    }

    $selectedBots = if ($Bot -eq "both") { @("personal", "finance") } else { @($Bot) }
    foreach ($selectedBot in $selectedBots) {
        Invoke-BotRelogin -Profile $botProfiles[$selectedBot]
    }
    Write-Host ""
    Write-Host "All requested checks completed successfully."
    exit 0
} catch {
    Write-Host ""
    Write-Error ("Akane cloud Bot relogin failed: {0}" -f $_.Exception.Message)
    exit 1
}
