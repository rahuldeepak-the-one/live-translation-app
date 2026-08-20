"""QR code for /view — people scan it instead of typing an IP and a port."""
import pytest

from qr import svg_for, view_url_for


def test_view_url_is_built_from_the_host_the_client_used():
    assert view_url_for("192.168.1.29:8080") == "http://192.168.1.29:8080/view"


def test_view_url_keeps_a_hostname_without_a_port():
    assert view_url_for("church-laptop.local") == "http://church-laptop.local/view"


@pytest.mark.parametrize("bad", [
    "", "   ", "host with spaces", "evil\nInjected: header", "a" * 300, None,
])
def test_view_url_rejects_a_malformed_host(bad):
    # The Host header is client-controlled; a bad one must not end up encoded
    # into a QR that the whole congregation then scans.
    assert view_url_for(bad) is None


def test_svg_is_returned_as_inline_markup():
    svg = svg_for("http://192.168.1.29:8080/view")
    assert svg.lstrip().startswith("<svg")
    assert svg.rstrip().endswith("</svg>")


def test_svg_is_self_contained():
    """No network at the venue — the QR must not fetch anything.

    The xmlns URI is deliberately not treated as a violation: it names the SVG
    namespace, it is never dereferenced, and it must be present for the file to
    load as an <img> at all.
    """
    svg = svg_for("http://192.168.1.29:8080/view")
    for external in ("<image", "xlink:href", "<script", "url(http", 'href="http'):
        assert external not in svg


def test_svg_declares_the_namespace():
    """Without xmlns the file is invalid standalone and <img> renders nothing."""
    assert 'xmlns="http://www.w3.org/2000/svg"' in svg_for("http://x/view")


def test_different_urls_produce_different_codes():
    assert svg_for("http://192.168.1.29:8080/view") != svg_for("http://10.0.0.5:8080/view")


def test_same_url_is_stable():
    url = "http://192.168.1.29:8080/view"
    assert svg_for(url) == svg_for(url)
