from datetime import datetime,timezone
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError


def local_timestamp(value,zone='UTC'):
    try:
        date=datetime.fromisoformat(value.replace('Z','+00:00'))
        if date.tzinfo is None:date=date.replace(tzinfo=timezone.utc)
        try:target=ZoneInfo(zone)
        except (ZoneInfoNotFoundError,ValueError,TypeError):target=timezone.utc;zone='UTC'
        return date.astimezone(target).strftime('%d %b %Y, %H:%M %Z')+f' ({zone})'
    except (ValueError,TypeError,AttributeError):return None
