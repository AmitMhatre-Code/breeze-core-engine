"""Fixed ES256 key pair for heartbeat policy token unit tests (not for production)."""

TEST_PRIVATE_KEY_PEM = """-----BEGIN PRIVATE KEY-----
MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQg6dgB4iydlrEPShIm
kac/w0uKoTI2LWefAZQt+QvgrY+hRANCAARtXSJ13MLWa+4a2vMxnaAl2l2uYXNK
k68xdYaSi+F4OchVqTTvd79/ZARWGs3Wdu7hYKUPj1Q2WT59qZhbJ9GL
-----END PRIVATE KEY-----"""

TEST_PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEbV0iddzC1mvuGtrzMZ2gJdpdrmFz
SpOvMXWGkovheDnIVak073e/f2QEVhrN1nbu4WClD49UNlk+famYWyfRiw==
-----END PUBLIC KEY-----"""


def attach_test_policy_token(policy: dict, *, public_ip: str) -> dict:
    """Sign a policy dict for core-engine tests (mirrors portal heartbeat_policy_token)."""
    import time

    import jwt

    now = int(time.time())
    claims = dict(policy)
    claims["public_ip"] = public_ip
    claims["iss"] = "breeze-portal"
    claims["aud"] = "breeze-core-engine"
    claims["iat"] = now
    claims["exp"] = now + 600
    out = dict(policy)
    out["policy_token"] = jwt.encode(claims, TEST_PRIVATE_KEY_PEM, algorithm="ES256")
    return out
