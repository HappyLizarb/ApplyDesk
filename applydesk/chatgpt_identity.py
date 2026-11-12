"""Validate OAuth identity using the installed cryptography runtime and pinned issuer."""

import base64
import json
import time
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature


def decode64(value):
    """JWT and JWK components use unpadded base64url, never ordinary secret strings."""
    if not isinstance(value, str) or len(value) > 100000:
        raise ValueError("Invalid identity token.")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def verify_signature(header, signature, message, jwks):
    """Allow only asymmetric signatures whose key ID and algorithm match the JWKS."""
    keys = [key for key in jwks.get("keys", []) if key.get("kid") == header.get("kid") and key.get("use", "sig") == "sig"]
    if len(keys) != 1 or header.get("crit"):
        raise ValueError("Unrecognized identity signing key.")
    key, algorithm = keys[0], header.get("alg")
    if key.get("alg", algorithm) != algorithm:
        raise ValueError("Identity algorithm mismatch.")
    if algorithm == "RS256" and key.get("kty") == "RSA":
        numbers = rsa.RSAPublicNumbers(int.from_bytes(decode64(key["e"]), "big"), int.from_bytes(decode64(key["n"]), "big"))
        numbers.public_key().verify(signature, message, padding.PKCS1v15(), hashes.SHA256())
    elif algorithm == "ES256" and key.get("kty") == "EC" and key.get("crv") == "P-256" and len(signature) == 64:
        numbers = ec.EllipticCurvePublicNumbers(int.from_bytes(decode64(key["x"]), "big"), int.from_bytes(decode64(key["y"]), "big"), ec.SECP256R1())
        encoded = encode_dss_signature(int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big"))
        numbers.public_key().verify(encoded, message, ec.ECDSA(hashes.SHA256()))
    else:
        raise ValueError("Unsupported identity signature.")


def validate_identity(token, client_id, nonce, jwks, subject=None):
    """Verify signature, issuer, audience, lifetime, and transaction binding before storage."""
    try:
        head, body, signature = token.split(".")
        header, claims = json.loads(decode64(head)), json.loads(decode64(body))
        verify_signature(header, decode64(signature), (head + "." + body).encode(), jwks)
        audience = claims.get("aud")
        audiences = audience if isinstance(audience, list) else [audience]
        valid = claims.get("iss") == "https://auth.openai.com" and client_id in audiences
        valid = valid and (len(audiences) == 1 or claims.get("azp") == client_id)
        valid = valid and float(claims["exp"]) > time.time() - 5 and float(claims["iat"]) <= time.time() + 5
        valid = valid and float(claims.get("nbf", 0)) <= time.time() + 5
        valid = valid and isinstance(claims.get("sub"), str) and bool(claims["sub"])
        valid = valid and (nonce is None or claims.get("nonce") == nonce)
        valid = valid and (subject is None or claims["sub"] == subject)
        if not valid:
            raise ValueError()
        return claims
    except Exception:
        raise ValueError("ChatGPT identity could not be verified. Start sign-in again.") from None
