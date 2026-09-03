#include <OVR_CAPI.h>
#include <openvr.h>
#include <array>
#include <cstdio>
#include <cstring>

int main()
{
    vr::EVRInitError error;
    vr::VR_Init(&error, vr::VRApplication_Background);
    if (error != vr::VRInitError_None) {
        std::printf("SKIP: an active OpenVR runtime is needed (%d)\n", error);
        return 77;
    }
    int failures = 0;
    const auto check = [&failures](bool ok, const char* message) {
        if (!ok) { std::printf("FAIL: %s\n", message); ++failures; }
    };
    check(ovr_GetBoundaryGeometry(nullptr, ovrBoundary_PlayArea, nullptr, nullptr)
          == ovrError_InvalidParameter, "null count must be rejected");
    int count = -1;
    const auto query = ovr_GetBoundaryGeometry(nullptr, ovrBoundary_PlayArea, nullptr, &count);
    check(OVR_SUCCESS(query), "size query must ignore the input count");
    check(count == 0 || count == 4, "boundary contains zero or four corners");
    std::array<ovrVector3f, 4> guard;
    std::memset(guard.data(), 0x5A, sizeof(guard));
    const auto before = guard;
    int capacity = 0;
    const auto result = ovr_GetBoundaryGeometry(nullptr, ovrBoundary_PlayArea, guard.data(), &capacity);
    check(std::memcmp(guard.data(), before.data(), sizeof(guard)) == 0,
          "a zero-capacity buffer must never be written");
    check(count ? result == ovrError_InsufficientArraySize : result == ovrSuccess_BoundaryInvalid,
          "return the correct capacity/boundary result");
    check(capacity == count, "report the required point count");
    vr::VR_Shutdown();
    std::printf("Boundary buffer checks: %s\n", failures ? "FAILED" : "PASSED");
    return failures ? 1 : 0;
}
