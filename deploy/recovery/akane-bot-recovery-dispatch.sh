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
interactive_session=0
case "$original_command" in
    "")
        interactive_session=1
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

if [[ "$interactive_session" -eq 0 ]]; then
    exec sudo -n "$ROOT_HELPER" "$action"
fi

echo
echo "Checking login state. Creating a fresh QR can take up to 60 seconds..."
set +e
result="$(sudo -n "$ROOT_HELPER" "$action" 2>&1)"
exit_code=$?
set -e
printf '%s\n' "$result"
echo

if [[ "$exit_code" -ne 0 ]]; then
    echo "Recovery did not complete. Keep this result visible and report the AKANE_STATE line."
elif grep -q '^AKANE_FILE=' <<<"$result"; then
    echo "QR image created."
    echo "Open this Host's SFTP / Files page, refresh the current qr directory,"
    echo "then download personal.png or finance.png within 10 minutes."
elif grep -Eq '^AKANE_STATE=(connected|connected_after_restart)$' <<<"$result"; then
    echo "All requested Bots are already connected. No QR image is needed or created."
fi

echo
printf 'Press Enter to close this result...'
IFS= read -r _ || true
exit "$exit_code"
