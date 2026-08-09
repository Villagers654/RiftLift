from riftlift.library import KNOWN_LAUNCHES


def test_vader_uses_shipping_vr_executable() -> None:
    executable, arguments = KNOWN_LAUNCHES["2031736060288351"]
    assert executable.endswith("WKND-Win64-Shipping.exe")
    assert arguments == ["-vr"]
