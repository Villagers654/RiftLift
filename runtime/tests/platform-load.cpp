#include <Windows.h>
#include <cstdio>
#include <cstdint>

int main()
{
    HMODULE platform = LoadLibraryW(L"LibOVRPlatform64_1.dll");
    if (!platform) { std::printf("Platform load failed: %lu\n", GetLastError()); return 1; }
    auto initialize = reinterpret_cast<int(__cdecl*)(const char*)>(GetProcAddress(platform, "ovr_PlatformInitializeWindows"));
    auto fromString = reinterpret_cast<bool(__cdecl*)(uint64_t*, const char*)>(GetProcAddress(platform, "ovrID_FromString"));
    auto initialized = reinterpret_cast<bool(__cdecl*)()>(GetProcAddress(platform, "ovr_IsPlatformInitialized"));
    if (!initialize || !fromString || !initialized) { std::puts("Required export missing"); return 1; }
    uint64_t id = 0;
    if (initialize("0") != 0 || !initialized() || !fromString(&id, "42") || id != 42) {
        std::puts("Platform forwarding/initialization failed"); return 1;
    }
    std::puts("Signed platform facade, offline initialization, and utility forwarding: PASSED");
    FreeLibrary(platform);
    return 0;
}
