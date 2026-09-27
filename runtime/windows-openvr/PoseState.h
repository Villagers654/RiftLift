#pragma once

#include "OVR_CAPI.h"
#include "REV_Math.h"

namespace REV {
inline ovrPoseStatef TrackedDevicePoseToOVRPose(vr::TrackedDevicePose_t pose, ovrPoseStatef& lastPose, double time)
{
	ovrPoseStatef result = { OVR::Posef::Identity() };
	if (!pose.bPoseIsValid)
		return result;

	OVR::Matrix4f matrix = REV::Matrix4f(pose.mDeviceToAbsoluteTracking);

	// Make sure the orientation stays in the same hemisphere as the previous orientation, this prevents
	// linear interpolations from suddenly flipping the long way around in Oculus Medium.
	OVR::Quatf q(matrix);
	q.EnsureSameHemisphere(lastPose.ThePose.Orientation);

	result.ThePose.Orientation = q;
	result.ThePose.Position = matrix.GetTranslation();
	result.AngularVelocity = (REV::Vector3f)pose.vAngularVelocity;
	result.LinearVelocity = (REV::Vector3f)pose.vVelocity;
	// Physics and render threads can query the same predicted timestamp. There
	// is no derivative for that sample (or the first/backwards sample); dividing
	// by zero here feeds NaN/Inf into the application's controller physics.
	if (lastPose.TimeInSeconds > 0.0 && time > lastPose.TimeInSeconds)
	{
		float elapsed = float(time - lastPose.TimeInSeconds);
		result.AngularAcceleration = ((REV::Vector3f)pose.vAngularVelocity - lastPose.AngularVelocity) / elapsed;
		result.LinearAcceleration = ((REV::Vector3f)pose.vVelocity - lastPose.LinearVelocity) / elapsed;
	}
	result.TimeInSeconds = time;

	// Store the last pose
	lastPose = result;

	return result;
}
}
