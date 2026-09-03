// Read the VR compositor's eye texture, without creating or capturing a desktop window.
#include <openvr.h>
#include <d3d11.h>
#include <dxgi.h>
#include <wrl/client.h>
#include <cstdio>

using Microsoft::WRL::ComPtr;

int main(int argc, char** argv)
{
    vr::EVRInitError error;
    auto system = vr::VR_Init(&error, vr::VRApplication_Background);
    if (error != vr::VRInitError_None) {
        std::printf("OpenVR initialization error: %d\n", error);
        return 2;
    }
    auto compositor = vr::VRCompositor();
    vr::Compositor_CumulativeStats stats = {};
    compositor->GetCumulativeStats(&stats, sizeof(stats));
    std::printf("scene_pid=%u renderer_pid=%u stats_pid=%u presents=%u dropped=%u reprojected=%u\n",
        compositor->GetCurrentSceneFocusProcess(), compositor->GetLastFrameRenderer(),
        stats.m_nPid, stats.m_nNumFramePresents, stats.m_nNumDroppedFrames, stats.m_nNumReprojectedFrames);
    vr::TrackedDevicePose_t poses[vr::k_unMaxTrackedDeviceCount] = {};
    system->GetDeviceToAbsoluteTrackingPose(vr::TrackingUniverseStanding, 0, poses, vr::k_unMaxTrackedDeviceCount);
    std::printf("hmd_connected=%d pose_valid=%d position=%.3f,%.3f,%.3f\n",
        poses[0].bDeviceIsConnected, poses[0].bPoseIsValid,
        poses[0].mDeviceToAbsoluteTracking.m[0][3], poses[0].mDeviceToAbsoluteTracking.m[1][3], poses[0].mDeviceToAbsoluteTracking.m[2][3]);
    if (argc < 2) { vr::VR_Shutdown(); return 0; }
    int32_t adapterIndex = 0;
    system->GetDXGIOutputInfo(&adapterIndex);
    ComPtr<IDXGIFactory1> factory;
    ComPtr<IDXGIAdapter1> adapter;
    ComPtr<ID3D11Device> device;
    ComPtr<ID3D11DeviceContext> context;
    HRESULT hr = CreateDXGIFactory1(IID_PPV_ARGS(&factory));
    if (SUCCEEDED(hr)) hr = factory->EnumAdapters1(adapterIndex, &adapter);
    if (SUCCEEDED(hr)) hr = D3D11CreateDevice(adapter.Get(), D3D_DRIVER_TYPE_UNKNOWN, nullptr, 0,
        nullptr, 0, D3D11_SDK_VERSION, &device, nullptr, &context);
    if (FAILED(hr)) { std::printf("D3D device error: %08X\n", (unsigned)hr); vr::VR_Shutdown(); return 3; }
    ID3D11ShaderResourceView* mirror = nullptr;
    const auto result = compositor->GetMirrorTextureD3D11(vr::Eye_Left, device.Get(), reinterpret_cast<void**>(&mirror));
    if (result != vr::VRCompositorError_None || !mirror) {
        std::printf("Eye texture unavailable: %d\n", result); vr::VR_Shutdown(); return 4;
    }
    ComPtr<ID3D11Resource> resource;
    ComPtr<ID3D11Texture2D> texture;
    mirror->GetResource(&resource);
    hr = resource.As(&texture);
    D3D11_TEXTURE2D_DESC desc = {};
    if (SUCCEEDED(hr)) texture->GetDesc(&desc);
    std::printf("eye_width=%u eye_height=%u format=%u\n", desc.Width, desc.Height, desc.Format);
    const bool bgra = desc.Format == DXGI_FORMAT_B8G8R8A8_TYPELESS || desc.Format == DXGI_FORMAT_B8G8R8A8_UNORM || desc.Format == DXGI_FORMAT_B8G8R8A8_UNORM_SRGB;
    const bool rgba = desc.Format == DXGI_FORMAT_R8G8B8A8_TYPELESS || desc.Format == DXGI_FORMAT_R8G8B8A8_UNORM || desc.Format == DXGI_FORMAT_R8G8B8A8_UNORM_SRGB;
    int status = 5;
    if (bgra || rgba) {
        desc.Usage = D3D11_USAGE_STAGING;
        desc.BindFlags = desc.MiscFlags = 0;
        desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
        ComPtr<ID3D11Texture2D> staging;
        hr = device->CreateTexture2D(&desc, nullptr, &staging);
        if (SUCCEEDED(hr)) {
            context->CopyResource(staging.Get(), texture.Get());
            D3D11_MAPPED_SUBRESOURCE mapped = {};
            hr = context->Map(staging.Get(), 0, D3D11_MAP_READ, 0, &mapped);
            if (SUCCEEDED(hr)) {
                FILE* output = nullptr;
                if (fopen_s(&output, argv[1], "wb") == 0) {
                    std::fprintf(output, "P6\n%u %u\n255\n", desc.Width, desc.Height);
                    for (UINT y = 0; y < desc.Height; ++y) {
                        const auto row = static_cast<const unsigned char*>(mapped.pData) + y * mapped.RowPitch;
                        for (UINT x = 0; x < desc.Width; ++x) {
                            const unsigned char rgb[] = {row[x*4+(bgra?2:0)], row[x*4+1], row[x*4+(bgra?0:2)]};
                            std::fwrite(rgb, 1, 3, output);
                        }
                    }
                    status = std::fclose(output) == 0 ? 0 : 6;
                }
                context->Unmap(staging.Get(), 0);
            }
        }
    }
    compositor->ReleaseMirrorTextureD3D11(mirror);
    vr::VR_Shutdown();
    return status;
}
