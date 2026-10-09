#include <OVR_CAPI.h>
#include <cstdio>
#include <cmath>
#include <chrono>
#include <thread>

int main()
{
    if (OVR_FAILURE(ovr_Initialize(nullptr))) return 77;
    ovrSession session = nullptr;
    ovrGraphicsLuid luid;
    if (OVR_FAILURE(ovr_Create(&session, &luid))) { ovr_Shutdown(); return 1; }
    int failures = 0;
    const auto check = [&](bool ok, const char* message) {
        if (!ok) { ++failures; std::printf("FAIL: %s\n", message); }
    };
    check((ovr_GetConnectedControllerTypes(session) & ovrControllerType_Touch) == ovrControllerType_Touch, "two simulated hands connected");
    ovrInputState input = {};
    check(OVR_SUCCESS(ovr_GetInputState(session, ovrControllerType_Touch, &input)), "input query succeeds");
    check(input.ControllerType == ovrControllerType_Touch && input.Buttons == 0, "static controllers have no pressed buttons");
    const double now = ovr_GetTimeInSeconds();
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    std::printf("Clock advanced %.6f seconds in 100ms\n", ovr_GetTimeInSeconds() - now);
    auto state = ovr_GetTrackingState(session, now, false);
    for (int attempt = 0; !state.StatusFlags && attempt < 100; ++attempt) {
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
        state = ovr_GetTrackingState(session, 0, false);
    }
    ovrTrackedDeviceType types[] = {ovrTrackedDevice_LTouch, ovrTrackedDevice_RTouch};
    ovrPoseStatef poses[2] = {};
    check(OVR_SUCCESS(ovr_GetDevicePoses(session, types, 2, now, poses)), "device pose query succeeds without physical controllers");
    for (int hand = 0; hand < 2; ++hand) {
        check(state.HandStatusFlags[hand] != 0, "hand tracking flags set");
        check(std::abs(poses[hand].ThePose.Position.x - state.HandPoses[hand].ThePose.Position.x) < 0.001f, "both pose APIs agree");
    }
    ovr_SetTrackingOriginType(session, ovrTrackingOrigin_FloorLevel);
    const auto standing = ovr_GetTrackingState(session, 0, false);
    check(standing.HeadPose.ThePose.Position.y > 1.0f, "simulated standing head is above the floor");
    check(standing.HandPoses[0].ThePose.Position.y > 0.7f, "simulated hands follow standing height");
    ovr_Destroy(session);
    ovr_Shutdown();
    std::printf("Simulated input: %s\n", failures ? "FAILED" : "PASSED");
    return failures ? 1 : 0;
}
