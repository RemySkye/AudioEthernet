import socket
from types import SimpleNamespace

import audioethernet.discovery as discovery
from audioethernet.config import StreamConfig
from audioethernet.discovery import ReceiverDiscoveryClient, SenderDiscoveryService
from audioethernet.transport_udp import UDPReceiver, UDPSender


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_discovery_unicast_round_trip_and_invalid_payload() -> None:
    control_port = _free_udp_port()
    discovered: list[tuple[str, int]] = []

    sender_cfg = StreamConfig(
        role="sender",
        control_port=control_port,
        data_port=50482,
        endpoint_name="StudioPC",
        sample_rate=96000,
        bit_depth=24,
    )
    service = SenderDiscoveryService(
        sender_cfg,
        on_receiver_discover=lambda ip, port: discovered.append((ip, port)),
    )
    service.start()

    try:
        # Send malformed JSON and invalid port to verify service stays alive.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as raw:
            raw.sendto(b"not-json", ("127.0.0.1", control_port))
            raw.sendto(
                b'{"tag":"audioethernet-v1","type":"discover","data_port":"bad"}',
                ("127.0.0.1", control_port),
            )

        recv_cfg = StreamConfig(
            role="receiver",
            control_port=control_port,
            data_port=51234,
            target_ip="127.0.0.1",
            sender_name="Studio",
        )
        client = ReceiverDiscoveryClient(recv_cfg)
        try:
            offer = client.discover_once(timeout_seconds=1.0)
        finally:
            client.close()

        assert offer is not None
        assert offer.sender_ip == "127.0.0.1"
        assert offer.sender_name == "StudioPC"
        assert offer.sample_rate == 96000
        assert offer.bit_depth == 24
        assert offer.channels == 2
        assert ("127.0.0.1", 51234) in discovered
    finally:
        service.stop()


def test_udp_sender_and_receiver_round_trip() -> None:
    port = _free_udp_port()
    receiver = UDPReceiver(port)
    sender = UDPSender()
    try:
        assert sender.send(b"hello-audio", ("127.0.0.1", port)) is True
        received = receiver.recv(timeout=1.0)
        assert received is not None
        data, addr = received
        assert data == b"hello-audio"
        assert addr[0] == "127.0.0.1"
    finally:
        sender.close()
        receiver.close()


def test_broadcast_addresses_prefer_physical_ethernet(monkeypatch) -> None:
    fake_stats = {
        "Tailscale": SimpleNamespace(isup=True, speed=100),
        "vEthernet (Default Switch)": SimpleNamespace(isup=True, speed=10000),
        "Wi-Fi": SimpleNamespace(isup=True, speed=866),
        "Ethernet 3": SimpleNamespace(isup=True, speed=1000),
    }
    fake_addrs = {
        "Tailscale": [
            SimpleNamespace(
                family=socket.AF_INET,
                address="100.117.157.54",
                netmask="255.255.255.255",
            )
        ],
        "vEthernet (Default Switch)": [
            SimpleNamespace(
                family=socket.AF_INET,
                address="172.17.16.1",
                netmask="255.255.240.0",
            )
        ],
        "Wi-Fi": [
            SimpleNamespace(
                family=socket.AF_INET,
                address="192.168.1.42",
                netmask="255.255.255.0",
            )
        ],
        "Ethernet 3": [
            SimpleNamespace(
                family=socket.AF_INET,
                address="192.168.0.1",
                netmask="255.255.255.0",
            )
        ],
    }

    monkeypatch.setattr(discovery.psutil, "net_if_stats", lambda: fake_stats)
    monkeypatch.setattr(discovery.psutil, "net_if_addrs", lambda: fake_addrs)

    addrs = discovery._get_broadcast_addresses()
    assert addrs[0] == "192.168.0.255"
    assert addrs[1] == "192.168.1.255"
    assert addrs[-1] == "255.255.255.255"
