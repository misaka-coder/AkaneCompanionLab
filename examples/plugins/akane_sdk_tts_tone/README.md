# TTS replacement fixture

This independent public SDK plugin implements `tts` v1 / `synthesize` with a
440 Hz WAV tone. It is a test provider, not speech synthesis. It does not read
host voice configuration or inherit another provider's connection permission.
Install only in an isolated test instance and bind `tts` v1 to `example.tts-tone`.

The method preserves the requested voice identifiers in its metadata so host
consumer tests can exercise arbitrary engine identifiers. Its generated audio
is intentionally distinct from the first-party controlled GPT fixture.
