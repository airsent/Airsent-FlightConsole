"""Signed presence sessions: roles come from credential verification, never client claims."""
import base64
import hashlib
import hmac
import json
import time


def issue_token(role, secret, now=None):
    payload = json.dumps({'role': role, 'expires': int(time.time() if now is None else now) + 2592000}, separators=(',', ':')).encode()
    encoded = base64.urlsafe_b64encode(payload).rstrip(b'=')
    signature = hmac.new(secret.encode(), encoded, hashlib.sha256).hexdigest()
    return encoded.decode() + '.' + signature


def token_role(token, secret, now=None):
    try:
        encoded, signature = token.split('.')
        expected = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected): return None
        payload = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
        if payload['expires'] <= (time.time() if now is None else now): return None
        return payload['role'] if payload['role'] in ('operator', 'viewer') else None
    except (ValueError, KeyError, TypeError):
        return None


def authenticate(username, password, passwords):
    if username not in ('operator', 'viewer'): return None
    expected = passwords.get(username)
    return username if expected and hmac.compare_digest(password, expected) else None
