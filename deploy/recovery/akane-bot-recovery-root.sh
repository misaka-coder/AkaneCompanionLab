#!/usr/bin/env bash
set -euo pipefail

# Root-only helper behind the restricted akane-recovery SSH account. The
# caller can select only a fixed Bot id; paths, container names and expected
# accounts never come from SSH input.

umask 077

readonly RECOVERY_USER="akane-recovery"
readonly RECOVERY_GROUP="akane-recovery"
readonly QR_DIR="/home/akane-recovery/qr"
readonly QR_LIFETIME="10m"

if [[ "${EUID}" -ne 0 ]]; then
    echo "AKANE_RESULT=DENIED"
    exit 77
fi

profile_for() {
    case "$1" in
        personal)
            PROFILE_CONTAINER="akane-napcat-personal"
            PROFILE_EXPECTED_QQ="2184046306"
            PROFILE_ONEBOT_PORT="3001"
            PROFILE_ONEBOT_CONFIG="/var/lib/akane/personal/napcat/config/onebot11_2184046306.json"
            ;;
        finance)
            PROFILE_CONTAINER="akane-napcat-finance"
            PROFILE_EXPECTED_QQ="2483893575"
            PROFILE_ONEBOT_PORT="3002"
            PROFILE_ONEBOT_CONFIG="/var/lib/akane/finance/napcat/config/onebot11_2483893575.json"
            ;;
        *)
            return 1
            ;;
    esac
}

onebot_token() {
    jq -r '.network.httpServers[0].token // empty' "$PROFILE_ONEBOT_CONFIG"
}

actual_login_qq() {
    local token response
    token="$(onebot_token 2>/dev/null || true)"
    if [[ -z "$token" ]]; then
        return 1
    fi
    response="$(curl -sS --max-time 3 \
        -H "Authorization: Bearer $token" \
        "http://127.0.0.1:${PROFILE_ONEBOT_PORT}/get_login_info" 2>/dev/null || true)"
    printf '%s' "$response" | jq -r '.data.user_id // empty' 2>/dev/null || true
}

remove_qr() {
    local bot_id="$1"
    rm -f -- "$QR_DIR/${bot_id}.png"
}

show_status() {
    local bot_id="$1" actual
    profile_for "$bot_id"
    actual="$(actual_login_qq || true)"
    echo "AKANE_BOT=$bot_id"
    if [[ "$actual" == "$PROFILE_EXPECTED_QQ" ]]; then
        remove_qr "$bot_id"
        echo "AKANE_STATE=connected"
        return 0
    fi
    if [[ -n "$actual" ]]; then
        echo "AKANE_STATE=account_mismatch"
        return 2
    fi
    echo "AKANE_STATE=offline"
    return 1
}

schedule_qr_expiry() {
    local bot_id="$1" target="$2" unit
    unit="akane-qr-expire-${bot_id}-$(date +%s)"
    systemd-run --quiet \
        --unit="$unit" \
        --on-active="$QR_LIFETIME" \
        /usr/bin/rm -f -- "$target" >/dev/null
}

prepare_qr() {
    local bot_id="$1" actual token deadline target temp_file qr_size
    profile_for "$bot_id"

    if ! docker inspect "$PROFILE_CONTAINER" >/dev/null 2>&1; then
        echo "AKANE_BOT=$bot_id"
        echo "AKANE_STATE=container_missing"
        return 30
    fi
    if [[ ! -r "$PROFILE_ONEBOT_CONFIG" ]]; then
        echo "AKANE_BOT=$bot_id"
        echo "AKANE_STATE=config_unavailable"
        return 31
    fi

    actual="$(actual_login_qq || true)"
    if [[ "$actual" == "$PROFILE_EXPECTED_QQ" ]]; then
        remove_qr "$bot_id"
        echo "AKANE_BOT=$bot_id"
        echo "AKANE_STATE=connected"
        return 0
    fi
    if [[ -n "$actual" ]]; then
        echo "AKANE_BOT=$bot_id"
        echo "AKANE_STATE=account_mismatch"
        return 32
    fi

    token="$(onebot_token)"
    if [[ -z "$token" ]]; then
        echo "AKANE_BOT=$bot_id"
        echo "AKANE_STATE=token_unavailable"
        return 33
    fi

    logger -t akane-recovery "action=relogin bot=$bot_id status=restart_requested"
    docker exec "$PROFILE_CONTAINER" sh -c 'rm -f /app/napcat/cache/qrcode.png' >/dev/null 2>&1 || true
    remove_qr "$bot_id"
    docker restart "$PROFILE_CONTAINER" >/dev/null

    deadline=$(( $(date +%s) + 60 ))
    while [[ "$(date +%s)" -lt "$deadline" ]]; do
        actual="$(actual_login_qq || true)"
        if [[ "$actual" == "$PROFILE_EXPECTED_QQ" ]]; then
            echo "AKANE_BOT=$bot_id"
            echo "AKANE_STATE=connected_after_restart"
            logger -t akane-recovery "action=relogin bot=$bot_id status=fast_login"
            return 0
        fi
        if [[ -n "$actual" && "$actual" != "$PROFILE_EXPECTED_QQ" ]]; then
            echo "AKANE_BOT=$bot_id"
            echo "AKANE_STATE=account_mismatch"
            return 34
        fi
        if docker exec "$PROFILE_CONTAINER" test -s /app/napcat/cache/qrcode.png >/dev/null 2>&1; then
            sleep 1
            temp_file="$(mktemp /run/akane-recovery-qr.XXXXXX.png)"
            if ! docker cp "$PROFILE_CONTAINER:/app/napcat/cache/qrcode.png" "$temp_file" >/dev/null; then
                rm -f -- "$temp_file"
                echo "AKANE_BOT=$bot_id"
                echo "AKANE_STATE=qr_copy_failed"
                return 35
            fi
            qr_size="$(stat -c %s "$temp_file" 2>/dev/null || printf 0)"
            if [[ "$qr_size" -lt 100 ]]; then
                rm -f -- "$temp_file"
                echo "AKANE_BOT=$bot_id"
                echo "AKANE_STATE=qr_invalid"
                return 36
            fi
            target="$QR_DIR/${bot_id}.png"
            install -o root -g "$RECOVERY_GROUP" -m 0640 "$temp_file" "$target"
            rm -f -- "$temp_file"
            if ! schedule_qr_expiry "$bot_id" "$target"; then
                rm -f -- "$target"
                echo "AKANE_BOT=$bot_id"
                echo "AKANE_STATE=qr_expiry_failed"
                return 38
            fi
            echo "AKANE_BOT=$bot_id"
            echo "AKANE_STATE=qr_ready"
            echo "AKANE_FILE=${bot_id}.png"
            logger -t akane-recovery "action=relogin bot=$bot_id status=qr_ready"
            return 0
        fi
        sleep 1
    done

    echo "AKANE_BOT=$bot_id"
    echo "AKANE_STATE=qr_timeout"
    return 37
}

action="${1:-}"
case "$action" in
    status)
        failed=0
        show_status personal || failed=1
        show_status finance || failed=1
        if [[ "$failed" -eq 0 ]]; then
            echo "AKANE_RESULT=STATUS_OK"
            exit 0
        fi
        echo "AKANE_RESULT=STATUS_DEGRADED"
        exit 2
        ;;
    personal|finance)
        if prepare_qr "$action"; then
            if [[ -f "$QR_DIR/${action}.png" ]]; then
                echo "AKANE_RESULT=QR_READY"
            else
                echo "AKANE_RESULT=NO_QR_NEEDED"
            fi
            exit 0
        fi
        echo "AKANE_RESULT=RELOGIN_FAILED"
        exit 3
        ;;
    both)
        failed=0
        prepare_qr personal || failed=1
        prepare_qr finance || failed=1
        if [[ "$failed" -ne 0 ]]; then
            echo "AKANE_RESULT=RELOGIN_FAILED"
            exit 3
        fi
        if [[ -f "$QR_DIR/personal.png" || -f "$QR_DIR/finance.png" ]]; then
            echo "AKANE_RESULT=QR_READY"
        else
            echo "AKANE_RESULT=NO_QR_NEEDED"
        fi
        exit 0
        ;;
    *)
        echo "AKANE_RESULT=INVALID_ACTION"
        exit 64
        ;;
esac
