class CoverSongError(RuntimeError):
    def __init__(self, *, stage: str, reason: str, public_message: str) -> None:
        super().__init__(reason)
        self.stage = str(stage or "unknown")
        self.reason = str(reason or "cover_song_failed")
        self.public_message = str(public_message or "翻唱处理失败了。").strip()
