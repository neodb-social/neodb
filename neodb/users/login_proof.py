import hashlib
import hmac
import json
import secrets
from datetime import timedelta

from altcha import Payload, create_challenge, verify_solution
from django.conf import settings
from django.core.cache import cache
from django.http import HttpRequest
from django.utils import timezone

LOGIN_PROOF_ALGORITHM = "PBKDF2/SHA-256"
LOGIN_PROOF_COST = 5_000
LOGIN_PROOF_COUNTER_MIN = 5_000
LOGIN_PROOF_COUNTER_MAX = 10_000
LOGIN_PROOF_TTL = 5 * 60
LOGIN_PROOF_PAYLOAD_MAX_LENGTH = 16_384
LOGIN_PROOF_METHODS = frozenset({"passkey", "email", "mastodon", "threads", "bluesky"})

_SESSION_BINDING_KEY = "login_proof_binding"
_CACHE_KEY_PREFIX = "login_proof_used"


def _hmac_secret() -> bytes:
    return hmac.new(
        str(settings.SECRET_KEY).encode(),
        b"neodb-login-proof",
        hashlib.sha256,
    ).digest()


def _session_binding(request: HttpRequest, *, create: bool) -> str | None:
    binding = request.session.get(_SESSION_BINDING_KEY)
    if isinstance(binding, str) and binding:
        return binding
    if not create:
        return None
    binding = secrets.token_urlsafe(32)
    request.session[_SESSION_BINDING_KEY] = binding
    return binding


def _binding_digest(binding: str) -> str:
    return hashlib.sha256(binding.encode()).hexdigest()


def create_login_proof_challenge(
    request: HttpRequest, method: str
) -> dict[str, object]:
    if method not in LOGIN_PROOF_METHODS:
        raise ValueError("Unknown login method")
    binding = _session_binding(request, create=True)
    if not binding:
        raise RuntimeError("Unable to create login proof session binding")
    counter = (
        secrets.randbelow(LOGIN_PROOF_COUNTER_MAX - LOGIN_PROOF_COUNTER_MIN + 1)
        + LOGIN_PROOF_COUNTER_MIN
    )
    challenge = create_challenge(
        LOGIN_PROOF_ALGORITHM,
        cost=LOGIN_PROOF_COST,
        counter=counter,
        expires_at=timezone.now() + timedelta(seconds=LOGIN_PROOF_TTL),
        data={"method": method, "session": _binding_digest(binding)},
        hmac_secret=_hmac_secret(),
    )
    return challenge.to_dict()


def extract_login_proof(request: HttpRequest) -> str:
    if request.content_type == "application/json":
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError, UnicodeDecodeError:
            return ""
        value = data.get("altcha", "") if isinstance(data, dict) else ""
    else:
        value = request.POST.get("altcha", "")
    return value if isinstance(value, str) else ""


def verify_login_proof(request: HttpRequest, method: str) -> bool:
    if request.user.is_authenticated:
        return True
    if method not in LOGIN_PROOF_METHODS:
        return False

    encoded = extract_login_proof(request)
    if not encoded or len(encoded) > LOGIN_PROOF_PAYLOAD_MAX_LENGTH:
        return False
    try:
        payload = Payload.from_base64(encoded)
        result = verify_solution(payload, _hmac_secret())
        if not result.verified:
            return False

        challenge = payload.challenge
        parameters = challenge.parameters
        solution = payload.solution
        binding = _session_binding(request, create=False)
        expected_data = (
            {"method": method, "session": _binding_digest(binding)} if binding else None
        )
        if (
            parameters.algorithm != LOGIN_PROOF_ALGORITHM
            or parameters.cost != LOGIN_PROOF_COST
            or parameters.data != expected_data
            or not isinstance(challenge.signature, str)
            or not challenge.signature
            or not isinstance(solution.counter, int)
            or not LOGIN_PROOF_COUNTER_MIN
            <= solution.counter
            <= LOGIN_PROOF_COUNTER_MAX
            or not isinstance(solution.derived_key, str)
            or not isinstance(parameters.key_length, int)
            or parameters.key_length <= 0
            or len(solution.derived_key) != parameters.key_length * 2
            or not isinstance(parameters.key_prefix, str)
            or not parameters.key_prefix
            or not solution.derived_key.startswith(parameters.key_prefix)
        ):
            return False
    except Exception:
        return False

    replay_key = f"{_CACHE_KEY_PREFIX}:{challenge.signature}"
    return cache.add(replay_key, True, timeout=LOGIN_PROOF_TTL)
