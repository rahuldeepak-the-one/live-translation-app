"""Picking the URL to hand out — this box has 13 addresses, only one is useful."""
from netinfo import primary_ip, physical_ips, service_urls

ROUTE = "1.1.1.1 via 192.168.1.1 dev wlp3s0 src 192.168.1.29 uid 1001 \n    cache \n"

ADDRS = """1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever preferred_lft forever
3: wlp3s0    inet 192.168.1.29/24 brd 192.168.1.255 scope global dynamic noprefixroute wlp3s0\\       valid_lft 86171sec
4: ava-br0    inet 172.31.190.190/24 brd 172.31.190.255 scope global noprefixroute ava-br0\\       valid_lft forever
5: tun0    inet 21.8.18.70/16 brd 21.8.255.255 scope global tun0\\       valid_lft forever
6: flannel.1    inet 10.42.0.0/32 scope global flannel.1\\       valid_lft forever
7: br-d29f40baafe9    inet 172.21.0.1/16 brd 172.21.255.255 scope global br-d29f40baafe9\\       valid_lft forever
8: docker0    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0\\       valid_lft forever
9: cni0    inet 10.42.0.1/24 brd 10.42.0.255 scope global cni0\\       valid_lft forever
10: eno1    inet 192.168.1.77/24 brd 192.168.1.255 scope global eno1\\       valid_lft forever
"""


def test_primary_ip_is_the_default_route_source():
    assert primary_ip(ROUTE) == "192.168.1.29"


def test_primary_ip_handles_a_direct_route_without_a_gateway():
    assert primary_ip("1.1.1.1 dev eth0 src 10.0.0.5 uid 1000 \n    cache \n") == "10.0.0.5"


def test_primary_ip_is_none_when_there_is_no_route():
    assert primary_ip("") is None
    assert primary_ip("RTNETLINK answers: Network is unreachable") is None


def test_physical_ips_keeps_real_network_cards():
    found = dict(physical_ips(ADDRS))
    assert found["wlp3s0"] == "192.168.1.29"
    assert found["eno1"] == "192.168.1.77"


def test_physical_ips_drops_loopback_docker_k8s_and_vpn():
    names = [iface for iface, _ in physical_ips(ADDRS)]
    for virtual in ("lo", "docker0", "br-d29f40baafe9", "flannel.1", "cni0",
                    "tun0", "ava-br0"):
        assert virtual not in names, f"{virtual} should not be offered to people"


def test_service_urls_put_the_default_route_first():
    urls = service_urls(8080, ROUTE, ADDRS)
    assert urls[0] == ("wlp3s0", "http://192.168.1.29:8080")


def test_service_urls_still_list_other_real_cards():
    urls = service_urls(8080, ROUTE, ADDRS)
    assert ("eno1", "http://192.168.1.77:8080") in urls


def test_service_urls_never_repeat_the_primary():
    urls = service_urls(8080, ROUTE, ADDRS)
    addresses = [url for _, url in urls]
    assert len(addresses) == len(set(addresses))


def test_service_urls_survive_a_missing_default_route():
    urls = service_urls(8080, "", ADDRS)
    assert ("wlp3s0", "http://192.168.1.29:8080") in urls


from netinfo import current_ssid, startup_banner

NMCLI = "no:Airtel 2ndfloorB\nyes:Airtel_wix2ndfloor2C\nno:Airtel_1st floor B\n"


def test_current_ssid_picks_the_active_network():
    assert current_ssid(NMCLI) == "Airtel_wix2ndfloor2C"


def test_current_ssid_is_none_when_not_on_wifi():
    assert current_ssid("no:SomeNet\nno:Other\n") is None
    assert current_ssid("") is None


def test_banner_lists_all_three_pages():
    banner = startup_banner(8080, ROUTE, ADDRS, NMCLI)
    for page in ("display", "view", "mic"):
        assert f"http://192.168.1.29:8080/{page}" in banner


def test_banner_names_the_wifi_phones_must_join():
    """Today's failure was a phone on one of the other 11 Airtel networks."""
    assert "Airtel_wix2ndfloor2C" in startup_banner(8080, ROUTE, ADDRS, NMCLI)


def test_banner_flags_other_addresses_as_alternates():
    banner = startup_banner(8080, ROUTE, ADDRS, NMCLI)
    assert "192.168.1.77" in banner          # the eno1 address
    assert banner.index("192.168.1.29") < banner.index("192.168.1.77")


def test_banner_warns_when_nothing_is_reachable():
    banner = startup_banner(8080, "", "1: lo    inet 127.0.0.1/8 scope host lo\\  x", "")
    assert "no network" in banner.lower()


def test_banner_prints_the_control_url_when_a_token_is_supplied():
    banner = startup_banner(8080, ROUTE, ADDRS, control_token="7f3a9c")
    assert "/control/7f3a9c" in banner


def test_banner_omits_control_line_without_a_token():
    banner = startup_banner(8080, ROUTE, ADDRS)
    assert "/control" not in banner
