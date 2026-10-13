"""Text normalization for readback from Douyin's native editor DOM."""


def normalize_douyin_editor_text(value: str) -> str:
    """Normalize only editor serialization artifacts before exact comparison.

    Douyin's visible editor can include zero-width spaces and may serialize line
    endings as CRLF. Preserve all other characters, including ordinary spaces,
    internal newlines, and emoji zero-width joiners. A non-string is an invalid
    readback and raises instead of accidentally matching an empty draft.
    """
    if not isinstance(value, str):
        raise TypeError("Douyin editor readback must be text")
    return value.replace("\r\n", "\n").replace("\u200b", "").rstrip("\n")
