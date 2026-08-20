from riftlift.steam_vdf import dumps, loads, loads_text


def test_text_vdf_supports_nested_values_comments_and_escapes() -> None:
    assert loads_text(
        '// heading\n"users" { "123" { '
        '"Name" "A \\"quoted\\" name" "Path" "C:\\Games" } }'
    ) == {"users": {"123": {"Name": 'A "quoted" name', "Path": "C:\\Games"}}}


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
