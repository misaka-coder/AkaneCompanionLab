#!/usr/bin/env bash
set -euo pipefail

readonly QR_DIR="/home/akane-recovery/qr"
readonly ROOT_HELPER="/usr/local/sbin/akane-bot-recovery-root"
original_command="${SSH_ORIGINAL_COMMAND:-}"

# OpenSSH SFTP clients request a subsystem through the same forced command.
# Serve only a read-only SFTP process starting in the QR directory.
case "$original_command" in
    internal-sftp*|/usr/lib/openssh/sftp-server*)
        exec /usr/lib/openssh/sftp-server -R -d "$QR_DIR"
        ;;
esac

action=""
case "$original_command" in
    "")
        echo "Akane Bot emergency recovery"
        echo "  1. Check status"
        echo "  2. Personal Bot QR"
        echo "  3. Finance Bot QR"
        echo "  4. Both"
        printf 'Choose 1-4: '
        IFS= read -r choice
        case "$choice" in
            1) action="status" ;;
            2) action="personal" ;;
            3) action="finance" ;;
            4) action="both" ;;
            *) echo "Denied: invalid choice"; exit 64 ;;
        esac
        ;;
    status|personal|finance|both)
        action="$original_command"
        ;;
    help)
        echo "Allowed commands: status, personal, finance, both"
        exit 0
        ;;
    *)
        echo "Denied: this key can only recover Akane Bot login."
        exit 64
        ;;
esac

exec sudo -n "$ROOT_HELPER" "$action"
