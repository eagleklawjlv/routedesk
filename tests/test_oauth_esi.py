import json
import threading
from http.client import HTTPConnection

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from everoute.credentials import MemoryCredentialStore
from everoute.esi import EsiClient, OutcomeKind, interpret_waypoint_response
from everoute.http import HttpClient, HttpResult, Redirect, UrlNotAllowed
from everoute.messages import SCOPE
from everoute.oauth import (
    EXPECTED_AUDIENCE,
    AuthError,
    LoopbackServer,
    OAuthClient,
    TokenManager,
    endpoints_from_metadata,
    pkce_challenge,
    validate_access_token,
)

CLIENT_ID = "abcdef0123456789"


@pytest.fixture(scope="module")
def signing_key():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(private.public_key()))
    jwk.update({"kid": "test-key", "alg": "RS256", "use": "sig"})
    return private, {"keys": [jwk]}


def _token(private, claims):
    return jwt.encode(claims, private, algorithm="RS256", headers={"kid": "test-key"})


def _claims(**overrides):
    base = {
        "iss": "https://login.eveonline.com",
        "sub": "CHARACTER:EVE:123456789",
        "name": "Test Pilot",
        "aud": [CLIENT_ID, EXPECTED_AUDIENCE],
        "scp": [SCOPE],
        "exp": 9_999_999_999,
    }
    base.update(overrides)
    return base


class FakeTransport:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def request(self, method, url, *, headers=None, body=None, timeout=None):
        self.calls.append({"method": method, "url": url, "headers": headers or {}, "body": body})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_pkce_rfc_vector():
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert pkce_challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_authorize_url_is_public_pkce_and_one_scope():
    client = OAuthClient(HttpClient(FakeTransport([])), CLIENT_ID, "http://127.0.0.1:45831/callback")
    url = client.authorization_url()
    assert "client_secret" not in url
    assert "code_challenge_method=S256" in url
    assert "response_type=code" in url
    assert SCOPE in url
    assert "esi-location" not in url
    assert client.pending is not None


def test_state_mismatch_does_not_exchange():
    transport = FakeTransport([])
    client = OAuthClient(HttpClient(transport), CLIENT_ID, "http://127.0.0.1:45831/callback")
    client.authorization_url()
    with pytest.raises(AuthError, match="state"):
        client.exchange("auth-code", "wrong-state")
    assert transport.calls == []
    assert client.pending is not None


def test_exchange_sends_verifier_and_no_secret(signing_key):
    private, jwks = signing_key
    access = _token(private, _claims())
    body = json.dumps(
        {"access_token": access, "refresh_token": "refresh-1", "token_type": "Bearer", "expires_in": 1200}
    ).encode()
    transport = FakeTransport([HttpResult(200, {}, body, "https://login.eveonline.com/v2/oauth/token")])
    client = OAuthClient(HttpClient(transport), CLIENT_ID, "http://127.0.0.1:45831/callback")
    client.set_jwks(jwks)
    client.authorization_url()
    state = client.pending.state
    tokens = client.exchange("auth-code", state)
    sent = transport.calls[0]["body"].decode()
    assert "client_secret" not in sent
    assert "code_verifier=" in sent
    assert "grant_type=authorization_code" in sent
    assert tokens["refresh_token"] == "refresh-1"
    with pytest.raises(AuthError):
        client.exchange("auth-code", state)


def test_jwt_validation(signing_key):
    private, jwks = signing_key
    claims = validate_access_token(_token(private, _claims()), jwks, CLIENT_ID)
    assert claims["name"] == "Test Pilot"
    with pytest.raises(AuthError):
        validate_access_token(_token(private, _claims(aud=[CLIENT_ID])), jwks, CLIENT_ID)
    with pytest.raises(AuthError):
        validate_access_token(_token(private, _claims(scp=["esi-location.read_location.v1"])), jwks, CLIENT_ID)
    with pytest.raises(AuthError):
        validate_access_token(_token(private, _claims(iss="https://evil.example")), jwks, CLIENT_ID)
    with pytest.raises(AuthError):
        validate_access_token(_token(private, _claims(exp=1)), jwks, CLIENT_ID)


def test_refresh_rotates_stored_token(signing_key):
    private, jwks = signing_key
    access = _token(private, _claims())
    body = json.dumps(
        {"access_token": access, "refresh_token": "refresh-2", "token_type": "Bearer", "expires_in": 1000}
    ).encode()
    transport = FakeTransport([HttpResult(200, {}, body, "https://login.eveonline.com/v2/oauth/token")])
    oauth = OAuthClient(HttpClient(transport), CLIENT_ID, "http://127.0.0.1:9/callback")
    oauth.set_jwks(jwks)
    creds = MemoryCredentialStore()
    creds.set_refresh_token(123456789, "refresh-1")
    manager = TokenManager(creds, oauth)
    token = manager.force_refresh(123456789)
    assert token == access
    assert creds.get_refresh_token(123456789) == "refresh-2"
    sent = transport.calls[0]["body"].decode()
    assert "client_secret" not in sent
    assert "grant_type=refresh_token" in sent


def test_metadata_host_is_pinned():
    with pytest.raises(AuthError):
        endpoints_from_metadata({"token_endpoint": "https://evil.example/token"})
    endpoints = endpoints_from_metadata({"token_endpoint": "https://login.eveonline.com/v2/oauth/token"})
    assert endpoints.token.endswith("/v2/oauth/token")


def test_redirect_off_host_is_refused():
    transport = FakeTransport([Redirect(302, "https://evil.example/steal")])
    client = HttpClient(transport)
    with pytest.raises(UrlNotAllowed):
        client.request("GET", "https://esi.evetech.net/latest/universe/systems/")


def test_post_refuses_secret_field():
    client = HttpClient(FakeTransport([]))
    with pytest.raises(Exception, match="client secret"):
        client.post_form("https://login.eveonline.com/v2/oauth/token", {"client_secret": "nope"})


def test_waypoint_request_shape_and_statuses():
    transport = FakeTransport(
        [HttpResult(204, {"X-ESI-Error-Limit-Remain": "80"}, b"", "https://esi.evetech.net/latest/ui/autopilot/waypoint/")]
    )
    outcome = EsiClient(HttpClient(transport)).set_waypoint(
        "secret-token",
        30000142,
        add_to_beginning=False,
        clear_other_waypoints=True,
    )
    assert outcome.kind == OutcomeKind.APPLIED
    url = transport.calls[0]["url"]
    assert "destination_id=30000142" in url
    assert "clear_other_waypoints=true" in url
    assert "add_to_beginning=false" in url
    assert "secret-token" not in url
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer secret-token"
    limited = interpret_waypoint_response(
        HttpResult(420, {"X-ESI-Error-Limit-Reset": "15"}, b'{"error":"limit"}', "https://esi.evetech.net/")
    )
    assert limited.kind == OutcomeKind.RATE_LIMITED
    assert limited.retry_after == 15
    uncertain = interpret_waypoint_response(HttpResult(504, {}, b"", "https://esi.evetech.net/"))
    assert uncertain.kind == OutcomeKind.UNCERTAIN
    failed = interpret_waypoint_response(
        HttpResult(400, {}, b'{"error":"Character not online"}', "https://esi.evetech.net/")
    )
    assert failed.kind == OutcomeKind.FAILED
    assert "not online" in failed.message.lower() or "Character" in failed.message
    assert interpret_waypoint_response(HttpResult(401, {}, b'{"error":"unauthorized"}', "https://esi.evetech.net/")).kind == OutcomeKind.NEEDS_REFRESH


def test_loopback_callback_hides_the_code():
    server = LoopbackServer("127.0.0.1", 0)

    def client():
        connection = HTTPConnection("127.0.0.1", server.port, timeout=5)
        connection.request("GET", "/callback?code=abc&state=expected-state")
        response = connection.getresponse()
        body = response.read()
        assert response.status == 200
        assert b"abc" not in body
        assert b"expected-state" not in body

    thread = threading.Thread(target=client)
    thread.start()
    try:
        result = server.wait_for_state("expected-state", 5)
    finally:
        server.close()
        thread.join(timeout=5)
    assert result.code == "abc"
    assert result.state == "expected-state"
