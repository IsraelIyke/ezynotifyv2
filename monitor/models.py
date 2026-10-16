from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
import hashlib
import json
import re


def utcnow():
    return datetime.now(timezone.utc)


def normalize(text):
    # Keep meaningful line boundaries and original case for readable summaries.
    lines = [re.sub(r'\s+', ' ', line).strip() for line in text.splitlines()]
    return '\n'.join(line for line in lines if line)


def fetch_key(url, config):
    return hashlib.sha256(json.dumps([url, config], sort_keys=True).encode()).hexdigest()


@dataclass
class FetchResult:
    status: str
    text: str = ''
    final_url: str = ''
    reason: str = ''
    http_status: int = 0
    retry_after: int = 0
    etag: str = ''
    modified: str = ''
    screenshot: str = ''
    alert_images: dict = field(default_factory=dict)

    def cached(self):
        value = asdict(self)
        value.pop('screenshot', None)
        value.pop('alert_images', None)
        return value
