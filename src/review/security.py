import re

LABEL_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
CLOSING_TAG_PATTERN = re.compile(r"</untrusted_content\s*>", re.IGNORECASE)


def sanitize_untrusted_content(content: str) -> str:
    """Neutralize potential closing boundary injection attempts within untrusted text."""
    # Replace closing tags so an attacker cannot prematurely terminate the untrusted block
    return CLOSING_TAG_PATTERN.sub("&lt;/untrusted_content&gt;", content)


def wrap_untrusted(content: str, label: str = "data") -> str:
    """Enclose untrusted external data within defensive XML boundary delimiters.

    All pull request diffs, code contents, and user discussions must be wrapped
    using this function before being passed into model prompts to defend against
    prompt injection attacks.
    """
    safe_label = label if LABEL_PATTERN.match(label) else "data"
    sanitized = sanitize_untrusted_content(content)

    return f'<untrusted_content label="{safe_label}">\n{sanitized}\n</untrusted_content>'
