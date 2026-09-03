#pragma once

// Opt-in diagnostics of application-owned render textures, before compositor
// submission. Never reads a desktop/window surface. At most six frames per process.
#include <d3d11.h>
#include <d3d11on12.h>
#include <wrl/client.h>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <mutex>
#include <vector>

inline void CaptureApplicationFrame(ID3D11Texture2D* texture, ID3D11On12Device* interop = nullptr)
{
    static const char* directory = std::getenv("RIFTLIFT_CAPTURE_FRAMES_DIR");
    if (!directory || !*directory || !texture) return;
    D3D11_TEXTURE2D_DESC desc;
    texture->GetDesc(&desc);
    const bool bgra = desc.Format == DXGI_FORMAT_B8G8R8A8_TYPELESS || desc.Format == DXGI_FORMAT_B8G8R8A8_UNORM || desc.Format == DXGI_FORMAT_B8G8R8A8_UNORM_SRGB;
    const bool rgba = desc.Format == DXGI_FORMAT_R8G8B8A8_TYPELESS || desc.Format == DXGI_FORMAT_R8G8B8A8_UNORM || desc.Format == DXGI_FORMAT_R8G8B8A8_UNORM_SRGB;
    const bool half = desc.Format == DXGI_FORMAT_R16G16B16A16_TYPELESS || desc.Format == DXGI_FORMAT_R16G16B16A16_FLOAT;
    if (!(bgra || rgba || half) || desc.SampleDesc.Count != 1 || desc.Width < 64 || desc.Height < 64) return;
    static std::mutex lock;
    static unsigned count = 0;
    static ULONGLONG previous = 0;
    static const ULONGLONG started = GetTickCount64();
    static const unsigned delay = [] {
        const char* value = std::getenv("RIFTLIFT_CAPTURE_DELAY_SECONDS");
        return value ? static_cast<unsigned>(std::atoi(value)) : 0U;
    }();
    std::lock_guard<std::mutex> guard(lock);
    const auto now = GetTickCount64();
    if (now - started < static_cast<ULONGLONG>(delay) * 1000 || count >= 6 || now - previous < 5000) return;
    previous = now;
    ++count;
    Microsoft::WRL::ComPtr<ID3D11Device> device;
    Microsoft::WRL::ComPtr<ID3D11DeviceContext> context;
    Microsoft::WRL::ComPtr<ID3D11Texture2D> staging;
    texture->GetDevice(&device);
    device->GetImmediateContext(&context);
    desc.MipLevels = desc.ArraySize = 1;
    desc.Usage = D3D11_USAGE_STAGING;
    desc.BindFlags = desc.MiscFlags = 0;
    desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    if (FAILED(device->CreateTexture2D(&desc, nullptr, &staging))) return;
    ID3D11Resource* wrapped = texture;
    if (interop) interop->AcquireWrappedResources(&wrapped, 1);
    const auto release = [&] {
        if (interop) { interop->ReleaseWrappedResources(&wrapped, 1); context->Flush(); }
    };
    context->CopySubresourceRegion(staging.Get(), 0, 0, 0, 0, texture, 0, nullptr);
    D3D11_MAPPED_SUBRESOURCE mapped = {};
    if (FAILED(context->Map(staging.Get(), 0, D3D11_MAP_READ, 0, &mapped))) { release(); return; }
    char path[MAX_PATH];
    FILE* output = nullptr;
    if (sprintf_s(path, "%s/application-%lu-%u.ppm", directory, GetCurrentProcessId(), count) > 0 &&
        fopen_s(&output, path, "wb") == 0) {
        std::fprintf(output, "P6\n# format=%u; HDR uses Reinhard tone mapping and gamma 2.2\n%u %u\n255\n", desc.Format, desc.Width, desc.Height);
        std::vector<unsigned char> line(static_cast<size_t>(desc.Width) * 3);
        for (UINT y = 0; y < desc.Height; ++y) {
            const auto row = static_cast<const unsigned char*>(mapped.pData) + y * mapped.RowPitch;
            for (UINT x = 0; x < desc.Width; ++x) {
                for (UINT c = 0; c < 3; ++c) {
                    if (half) {
                        const auto value = reinterpret_cast<const unsigned short*>(row)[x*4+c];
                        const unsigned exponent = (value >> 10) & 31, fraction = value & 1023;
                        float linear = exponent ? std::ldexp(1.0f + fraction / 1024.0f, static_cast<int>(exponent)-15) : std::ldexp(static_cast<float>(fraction), -24);
                        if ((value & 0x8000) || exponent == 31) linear = 0;
                        line[x*3+c] = static_cast<unsigned char>(255 * std::pow(linear/(1+linear), 1.0f/2.2f));
                    } else {
                        line[x*3+c] = row[x*4+(bgra ? 2-c : c)];
                    }
                }
            }
            std::fwrite(line.data(), 1, line.size(), output);
        }
        std::fclose(output);
    }
    context->Unmap(staging.Get(), 0);
    release();
}
