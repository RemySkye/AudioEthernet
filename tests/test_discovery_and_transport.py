import socket

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
