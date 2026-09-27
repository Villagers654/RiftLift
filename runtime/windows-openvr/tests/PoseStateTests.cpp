#include "PoseState.h"

#include <cmath>
#include <cstdlib>
#include <iostream>

void check(bool condition, const char* message)
{
    if (!condition) {
        std::cerr << message << '\n';
        std::exit(1);
    }
}

void checkFinite(const ovrVector3f& value)
{
    check(std::isfinite(value.x) && std::isfinite(value.y) && std::isfinite(value.z),
        "pose acceleration must remain finite");
}

int main()
{
    vr::TrackedDevicePose_t device = {};
    device.bPoseIsValid = true;
    device.bDeviceIsConnected = true;
    device.mDeviceToAbsoluteTracking.m[0][0] = 1;
    device.mDeviceToAbsoluteTracking.m[1][1] = 1;
    device.mDeviceToAbsoluteTracking.m[2][2] = 1;
    ovrPoseStatef history = {};
    history.ThePose = OVR::Posef::Identity();

    // Current-pose queries may supply zero, and multiple game/physics polls
    // may share the same prediction timestamp while controller input changes.
    for (double time : {0.0, 0.0, 1.0, 1.0, 0.5}) {
        device.mDeviceToAbsoluteTracking.m[0][3] += 1;
        device.vVelocity.v[0] += 1;
        device.vAngularVelocity.v[1] += 1;
        auto pose = REV::TrackedDevicePoseToOVRPose(device, history, time);
        checkFinite(pose.LinearAcceleration);
        checkFinite(pose.AngularAcceleration);
        check(pose.ThePose.Position.x == device.mDeviceToAbsoluteTracking.m[0][3],
            "repeated timestamps must not freeze controller positions");
        check(pose.TimeInSeconds == time, "pose timestamp must be preserved");
    }

    // Normal increasing samples retain the physical velocity derivative.
    history = {};
    history.ThePose = OVR::Posef::Identity();
    device.vVelocity.v[0] = 2;
    device.vAngularVelocity.v[1] = 4;
    auto first = REV::TrackedDevicePoseToOVRPose(device, history, 10.0);
    check(first.LinearAcceleration.x == 0, "first sample has no velocity derivative");
    device.vVelocity.v[0] = 3;
    device.vAngularVelocity.v[1] = 6;
    auto second = REV::TrackedDevicePoseToOVRPose(device, history, 10.5);
    check(second.LinearAcceleration.x == 2, "linear acceleration must use elapsed time");
    check(second.AngularAcceleration.y == 4, "angular acceleration must use elapsed time");

    device.bPoseIsValid = false;
    auto invalid = REV::TrackedDevicePoseToOVRPose(device, history, 11.0);
    check(invalid.ThePose.Orientation.w == 1, "invalid poses must have identity orientation");
    check(history.TimeInSeconds == 10.5, "invalid poses must not overwrite history");
    checkFinite(invalid.LinearAcceleration);
    checkFinite(invalid.AngularAcceleration);
}
