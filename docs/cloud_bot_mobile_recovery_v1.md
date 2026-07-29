# Cloud Bot mobile recovery

The mobile recovery path is an emergency operator surface, not an Akane app or
public management endpoint. It uses a dedicated, expiring SSH key bound to a
forced command. The key can only:

- inspect whether the personal and finance NapCat accounts are connected;
- explicitly restart one fixed NapCat container when recovery is requested;
- generate a fresh login QR for one fixed expected QQ account;
- read the generated QR files through read-only SFTP.

It cannot request a shell, forward ports, upload files, choose a container or
path, read the Bot data roots, or invoke arbitrary sudo commands. QR files live
in a root-owned recovery directory, are readable only by the recovery group,
and are removed after ten minutes. The dedicated key must have an explicit
OpenSSH `expiry-time` and should be removed after the trip even if it has
already expired.

An account is considered connected only when the expected QQ identity is
present and OneBot `/get_status` reports both `online=true` and `good=true`.
`/get_login_info` alone is not a liveness check: NapCat can retain the account
identity while its messaging session is unhealthy.

Status option `1` is read-only. Recovery options `2`, `3`, and `4` always
restart the selected fixed container; they must not be skipped merely because
an API still exposes the expected account identity. After restart, a healthy
saved-session login completes without a QR. Otherwise the helper publishes the
fresh QR through the restricted SFTP directory.

Server files:

- `deploy/recovery/akane-bot-recovery-dispatch.sh`: forced-command and
  read-only SFTP dispatcher.
- `deploy/recovery/akane-bot-recovery-root.sh`: fixed-profile status/relogin
  implementation.
- `deploy/recovery/akane-bot-recovery.sudoers`: exact allowed root commands.

The portable Windows client is `scripts/mobile_relogin_cloud_bot.ps1`. A
runtime package supplies the private recovery key and a pinned `known_hosts`
file beside that script; neither file belongs in Git.

For same-phone login, download the PNG from the SSH/SFTP client, then use QQ's
scanner and choose the image from the album. Never send the private key or QR
through chat, email, or a public cloud link.
