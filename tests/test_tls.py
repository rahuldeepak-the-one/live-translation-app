"""TLS selection: a half-installed certificate must not take the service down."""
import os

import pytest

import tls


@pytest.fixture
def cert_pair(tmp_path):
    crt, key = tmp_path / "server.crt", tmp_path / "server.key"
    crt.write_text("cert"), key.write_text("key")
    return str(crt), str(key)


def test_both_files_present_enables_tls(cert_pair):
    crt, key = cert_pair
    assert tls.paths(crt, key) == (crt, key)
    assert tls.enabled(crt, key)
    assert tls.scheme(crt, key) == "https"


def test_no_certificate_falls_back_to_http(tmp_path):
    missing = str(tmp_path / "nope")
    assert tls.paths(missing, missing) == (None, None)
    assert not tls.enabled(missing, missing)
    assert tls.scheme(missing, missing) == "http"


@pytest.mark.parametrize("drop", ["crt", "key"])
def test_half_a_pair_is_treated_as_no_certificate(cert_pair, drop):
    """uvicorn given one of the two refuses to boot. A server that will not
    start is worse than one without TLS, so half a pair must read as none."""
    crt, key = cert_pair
    os.remove(crt if drop == "crt" else key)
    assert tls.paths(crt, key) == (None, None)
    assert tls.scheme(crt, key) == "http"


def test_a_directory_is_not_a_certificate(tmp_path):
    """certs/ existing but empty is the state right after a fresh clone."""
    assert tls.paths(str(tmp_path), str(tmp_path)) == (None, None)
