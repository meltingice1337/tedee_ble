"""MTU detection must never take exclusive write access (#15)."""

from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.tedee_ble.tedee_lib import ble
from custom_components.tedee_ble.tedee_lib.ble import MTU_FALLBACK, TedeeBLETransport


def _transport(sizes: list[int]) -> tuple[TedeeBLETransport, MagicMock]:
    """Transport whose handshake characteristic reports these sizes in turn."""
    char = MagicMock()
    type(char).max_write_without_response_size = property(
        lambda _self: sizes.pop(0) if len(sizes) > 1 else sizes[0]
    )
    client = MagicMock()
    client.services.get_characteristic.return_value = char
    client._backend._acquire_mtu = AsyncMock()
    client._backend._bus = None
    transport = TedeeBLETransport.__new__(TedeeBLETransport)
    transport._client = client
    return transport, client


async def test_mtu_read_from_characteristic() -> None:
    transport, client = _transport([495])
    assert await transport._read_mtu() == 498
    client._backend._acquire_mtu.assert_not_called()


async def test_mtu_waits_for_late_value() -> None:
    transport, client = _transport([20, 20, 244])
    with patch.object(ble, "MTU_WAIT", 5):
        assert await transport._read_mtu() == 247
    client._backend._acquire_mtu.assert_not_called()


async def test_mtu_falls_back_without_acquiring_write() -> None:
    transport, client = _transport([20])
    with patch.object(ble, "MTU_WAIT", 0.2):
        assert await transport._read_mtu() == MTU_FALLBACK
    client._backend._acquire_mtu.assert_not_called()


async def test_mtu_read_from_bluez_when_bleak_copy_is_stale() -> None:
    transport, client = _transport([20])
    transport._client.services.get_characteristic.return_value.obj = ("/char", {})
    reply = MagicMock()
    reply.body = [MagicMock(value=498)]
    client._backend._bus = MagicMock(call=AsyncMock(return_value=reply))
    assert await transport._read_mtu() == 498
    client._backend._acquire_mtu.assert_not_called()
