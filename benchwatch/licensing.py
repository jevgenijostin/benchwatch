"""Public-secret demo licensing: deliberately not a payment or security system."""
import base64
import hashlib
import hmac
import json
from datetime import date

DEMO_SECRET = b'BenchWatch public demo secret v1 - not production security'


def issue_license(holder, expires):
    date.fromisoformat(expires)
    payload = json.dumps(dict(holder=holder, expires=expires, mode='multi-team'),
                         sort_keys=True, separators=(',', ':')).encode()
    encoded = base64.urlsafe_b64encode(payload).decode().rstrip('=')
    signature = hmac.new(DEMO_SECRET, encoded.encode(), hashlib.sha256).hexdigest()
    return encoded + '.' + signature


def valid_license(token, today=None):
    try:
        encoded, signature = token.split('.')
        expected = hmac.new(DEMO_SECRET, encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return False
        payload = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
        return (payload['mode'] == 'multi-team' and bool(payload['holder'])
                and date.fromisoformat(payload['expires']) >= (today or date.today()))
    except (ValueError, KeyError, TypeError, UnicodeError):
        return False
