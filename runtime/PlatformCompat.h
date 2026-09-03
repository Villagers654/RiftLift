#pragma once

// Launch-scoped platform compatibility; never changes a machine-wide runtime.
inline const char* PlatformRedirect(LPCSTR name)
{
    if (!name)
        return nullptr;
    const char* base = PathFindFileNameA(name);
    if (_stricmp(base, "LibOVRPlatformImpl64_1.dll") != 0)
        return nullptr;
    return getenv("RIFTLIFT_PLATFORM_DLL");
}

inline const char* PlatformRedirect(LPCWSTR name)
{
    if (!name)
        return nullptr;
    const wchar_t* base = PathFindFileNameW(name);
    if (_wcsicmp(base, L"LibOVRPlatformImpl64_1.dll") != 0)
        return nullptr;
    return getenv("RIFTLIFT_PLATFORM_DLL");
}
