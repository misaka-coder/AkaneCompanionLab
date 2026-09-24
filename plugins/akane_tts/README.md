# Akane TTS

Service-only `tts` v1 / `synthesize`. Uses public SDK resources, the `tts`
connection and managed artifacts. The independent `capcore-adapter-speech`
package owns Edge and GPT-SoVITS protocols. No host imports or automatic installs.
FFmpeg must be available on PATH for MP3 validation; WAV uses the standard decoder.

Input: `text`, `voice: {provider, profile_id}`, `emotion`. Voice engine aliases
are normalized by the host. Unsupported engines fail without switching voice.
Output: one managed audio artifact and voice metadata. Registration is not playback.

Permissions: `service.provide`, `resource.read`, `artifact.write`, `network.read`,
`connection.tts.read`. Installation does not grant per-user execution approval.
Existing host voice configuration remains authoritative. Health checks do not
synthesize audio or start external services. Client pools belong to one worker;
reference resources belong to one invocation and are never cached as paths.

See [installation, approval and binding instructions](../../docs/plugin_tts_upgrade_v1.md)
for first installation, failed activation recovery and replacing this service.
Saving a selection is distinct from publishing an active generation.
