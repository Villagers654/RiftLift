from riftlift.steam_vdf import dumps, loads


def test_binary_vdf_roundtrip() -> None:
    value = {
        "shortcuts": {
            "0": {
                "appid": 0x81234567,
                "appname": "A VR Game",
                "exe": '"/home/person/.local/bin/riftlift"',
                "OpenVR": 1,
                "tags": {"0": "VR", "1": "RiftLift"},
            }
        }
    }
    assert loads(dumps(value)) == value
