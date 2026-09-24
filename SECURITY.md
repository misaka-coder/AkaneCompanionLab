# Security Policy

## Supported Version

Only the latest public Alpha branch is currently supported. This is an
experimental project and does not yet provide production security guarantees.

## Reporting

Please report security issues privately through the repository host's security
advisory feature. Do not include real API keys, private conversations, user
memory databases, or other people's personal data in a public issue.

Include:

- affected component and revision
- reproduction steps using synthetic data
- expected and observed behavior
- potential impact
- a proposed fix, if available

## Sensitive Areas

Pay particular attention to:

- path traversal and recursive filesystem operations
- character-pack import and archive extraction
- prompt, log, snapshot, and diagnostic redaction
- local tool execution and approval boundaries
- screen, clipboard, microphone, and desktop context permissions
- QQ/NapCat event authenticity and file delivery
- external LLM, TTS, ASR, MCP, ComfyUI, and browser endpoints

Rotate any credential immediately if it was committed or shared. Removing it
from the latest revision is not sufficient; repository history must also be
cleaned before publication.
