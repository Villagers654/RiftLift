from pathlib import Path

RUNTIME = Path(__file__).parents[1] / "runtime/windows-openvr"


def test_d3d_array_swapchains_use_array_views():
    source = (RUNTIME / "TextureD3D.cpp").read_text()

    assert "D3D11_SRV_DIMENSION_TEXTURE2DARRAY" in source
    assert "D3D11_SRV_DIMENSION_TEXTURE2DMSARRAY" in source
    assert "desc.Texture2DArray.ArraySize = ArraySize" in source
    assert "desc.Texture2DMSArray.ArraySize = ArraySize" in source
    assert "D3D11_RTV_DIMENSION_TEXTURE2DARRAY" in source
    assert "D3D11_RTV_DIMENSION_TEXTURE2DMSARRAY" in source
    assert "target_desc.Texture2DArray.ArraySize = ArraySize" in source
    assert "target_desc.Texture2DMSArray.ArraySize = ArraySize" in source


def test_d3d_cube_swapchains_create_cube_resources():
    source = (RUNTIME / "TextureD3D.cpp").read_text()

    assert "Type == ovrTexture_Cube" in source
    assert "D3D11_RESOURCE_MISC_TEXTURECUBE" in source
    assert "D3D11_SRV_DIMENSION_TEXTURECUBE" in source


def test_d3d_swapchain_trace_records_capabilities_and_result():
    source = (RUNTIME / "REV_CAPI_D3D.cpp").read_text()

    for field in (
        "Type",
        "Format",
        "ArraySize",
        "Width",
        "Height",
        "MipLevels",
        "SampleCount",
        "StaticImage",
        "MiscFlags",
        "BindFlags",
        "result",
    ):
        assert f'TraceOculusValue("ovr_CreateTextureSwapChainDX.{field}"' in source
