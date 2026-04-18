"""Parse user messages into intents."""

import re
from enum import Enum
from typing import Optional


class Intent(str, Enum):
    AUDIT = "audit"
    GREETING = "greeting"
    HELP = "help"
    UNKNOWN = "unknown"


# URL pattern
URL_PATTERN = re.compile(
    r'(?:https?://)?'
    r'(?:www\.)?'
    r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?'
    r'(?:\.[a-zA-Z]{2,})+(?:/\S*)?'
)

GREETING_WORDS = {"hello", "hi", "hey", "howdy", "greetings", "good morning", "good afternoon", "good evening"}
HELP_WORDS = {"help", "what can you do", "how does this work", "commands", "usage"}
AUDIT_WORDS = {"audit", "check", "scan", "analyze", "test", "review", "inspect"}


def parse_intent(message: str) -> tuple[Intent, Optional[str]]:
    """Parse a user message into an intent and optional URL.

    Returns:
        (intent, extracted_url or None)
    """
    text = message.strip().lower()

    # Check for URL in the message
    url_match = URL_PATTERN.search(message)
    extracted_url = None

    if url_match:
        extracted_url = url_match.group(0)
        # Ensure it has a protocol
        if not extracted_url.startswith("http"):
            extracted_url = "https://" + extracted_url

    # If message contains an audit keyword + URL, it's an audit
    if extracted_url:
        if any(word in text for word in AUDIT_WORDS):
            return Intent.AUDIT, extracted_url
        # URL alone implies audit
        return Intent.AUDIT, extracted_url

    # Greeting
    if any(word in text for word in GREETING_WORDS):
        return Intent.GREETING, None

    # Help
    if any(word in text for word in HELP_WORDS):
        return Intent.HELP, None

    return Intent.UNKNOWN, None
