// SteamVR driver that adds two simulated Oculus Touch controllers for testing
// RiftLift without hardware. It only adds controllers; any headset driver
// (including SteamVR's null HMD) keeps providing the display.
//
// Controllers follow the headset at a resting hand position. Inputs are set by
// writing lines to %LOCALAPPDATA%\RiftLift\touchsim.txt, which the driver
// consumes:  "<left|right> <input> <value>"  where input is one of
//   a b x y system trigger grip stickx sticky stickclick thumbrest
// or posx posy posz (metres from the resting hand position, headset-relative)
// and pitch (degrees, positive tilts the controller up).
// Values persist until changed, e.g. "right trigger 1" then "right trigger 0".
#include <openvr_driver.h>

#include <windows.h>

#include <cmath>
#include <cstdio>
#include <cstring>
#include <map>
#include <mutex>
#include <string>

namespace {

std::wstring CommandPath()
{
	wchar_t local[MAX_PATH];
	DWORD length = GetEnvironmentVariableW(L"LOCALAPPDATA", local, MAX_PATH);
	if (!length || length >= MAX_PATH)
		return L"";
	return std::wstring(local) + L"\\RiftLift\\touchsim.txt";
}

vr::HmdQuaternion_t QuaternionFromMatrix(const vr::HmdMatrix34_t& m)
{
	vr::HmdQuaternion_t q;
	q.w = std::sqrt(std::fmax(0.0, 1.0 + m.m[0][0] + m.m[1][1] + m.m[2][2])) / 2.0;
	q.x = std::sqrt(std::fmax(0.0, 1.0 + m.m[0][0] - m.m[1][1] - m.m[2][2])) / 2.0;
	q.y = std::sqrt(std::fmax(0.0, 1.0 - m.m[0][0] + m.m[1][1] - m.m[2][2])) / 2.0;
	q.z = std::sqrt(std::fmax(0.0, 1.0 - m.m[0][0] - m.m[1][1] + m.m[2][2])) / 2.0;
	q.x = std::copysign(q.x, m.m[2][1] - m.m[1][2]);
	q.y = std::copysign(q.y, m.m[0][2] - m.m[2][0]);
	q.z = std::copysign(q.z, m.m[1][0] - m.m[0][1]);
	return q;
}

class Controller : public vr::ITrackedDeviceServerDriver
{
public:
	explicit Controller(bool left) : m_Left(left) {}

	const char* Serial() const { return m_Left ? "RiftLiftTouchSim-L" : "RiftLiftTouchSim-R"; }

	vr::EVRInitError Activate(uint32_t id) override
	{
		m_Id = id;
		auto props = vr::VRProperties();
		auto c = props->TrackedDeviceToPropertyContainer(id);
		props->SetInt32Property(c, vr::Prop_ControllerRoleHint_Int32,
			m_Left ? vr::TrackedControllerRole_LeftHand : vr::TrackedControllerRole_RightHand);
		props->SetStringProperty(c, vr::Prop_ControllerType_String, "oculus_touch");
		// SteamVR's own Touch profile: games and RiftLift see standard Touch.
		props->SetStringProperty(c, vr::Prop_InputProfilePath_String, "{oculus}/input/touch_profile.json");
		props->SetStringProperty(c, vr::Prop_ManufacturerName_String, "Oculus");
		props->SetStringProperty(c, vr::Prop_ModelNumber_String,
			m_Left ? "Oculus Rift CV1 (Left Controller)" : "Oculus Rift CV1 (Right Controller)");
		props->SetStringProperty(c, vr::Prop_RenderModelName_String,
			m_Left ? "oculus_cv1_controller_left" : "oculus_cv1_controller_right");
		props->SetStringProperty(c, vr::Prop_SerialNumber_String, Serial());
		props->SetStringProperty(c, vr::Prop_TrackingSystemName_String, "riftlift_touchsim");
		props->SetBoolProperty(c, vr::Prop_DeviceProvidesBatteryStatus_Bool, true);
		props->SetFloatProperty(c, vr::Prop_DeviceBatteryPercentage_Float, 1.0f);

		auto input = vr::VRDriverInput();
		auto boolean = [&](const char* name, const char* path) {
			input->CreateBooleanComponent(c, path, &m_Bool[name]);
		};
		auto scalar = [&](const char* name, const char* path, vr::EVRScalarUnits units) {
			input->CreateScalarComponent(c, path, &m_Scalar[name], vr::VRScalarType_Absolute, units);
		};
		boolean(m_Left ? "x" : "a", m_Left ? "/input/x/click" : "/input/a/click");
		boolean(m_Left ? "x_touch" : "a_touch", m_Left ? "/input/x/touch" : "/input/a/touch");
		boolean(m_Left ? "y" : "b", m_Left ? "/input/y/click" : "/input/b/click");
		boolean(m_Left ? "y_touch" : "b_touch", m_Left ? "/input/y/touch" : "/input/b/touch");
		boolean("system", m_Left ? "/input/system/click" : "/input/system/click");
		boolean("trigger_click", "/input/trigger/click");
		boolean("trigger_touch", "/input/trigger/touch");
		boolean("stickclick", "/input/joystick/click");
		boolean("stick_touch", "/input/joystick/touch");
		boolean("thumbrest", "/input/thumbrest/touch");
		scalar("trigger", "/input/trigger/value", vr::VRScalarUnits_NormalizedOneSided);
		scalar("grip", "/input/grip/value", vr::VRScalarUnits_NormalizedOneSided);
		scalar("stickx", "/input/joystick/x", vr::VRScalarUnits_NormalizedTwoSided);
		scalar("sticky", "/input/joystick/y", vr::VRScalarUnits_NormalizedTwoSided);
		input->CreateHapticComponent(c, "/output/haptic", &m_Haptic);
		return vr::VRInitError_None;
	}

	void Deactivate() override { m_Id = vr::k_unTrackedDeviceIndexInvalid; }
	void EnterStandby() override {}
	void* GetComponent(const char*) override { return nullptr; }
	void DebugRequest(const char*, char* response, uint32_t size) override
	{
		if (size)
			response[0] = 0;
	}
	vr::DriverPose_t GetPose() override { return m_Pose; }

	void Set(const std::string& name, float value)
	{
		std::lock_guard<std::mutex> guard(m_Lock);
		m_Values[name] = value;
	}

	void RunFrame()
	{
		if (m_Id == vr::k_unTrackedDeviceIndexInvalid)
			return;
		UpdatePose();
		std::map<std::string, float> values;
		{
			std::lock_guard<std::mutex> guard(m_Lock);
			values = m_Values;
		}
		auto input = vr::VRDriverInput();
		auto value = [&](const char* name) {
			auto it = values.find(name);
			return it == values.end() ? 0.0f : it->second;
		};
		for (const char* button : {"a", "b", "x", "y"})
		{
			auto handle = m_Bool.find(button);
			if (handle == m_Bool.end())
				continue;
			bool down = value(button) > 0.5f;
			input->UpdateBooleanComponent(handle->second, down, 0);
			input->UpdateBooleanComponent(m_Bool[std::string(button) + "_touch"], down, 0);
		}
		float trigger = value("trigger");
		input->UpdateBooleanComponent(m_Bool["system"], value("system") > 0.5f, 0);
		input->UpdateScalarComponent(m_Scalar["trigger"], trigger, 0);
		input->UpdateBooleanComponent(m_Bool["trigger_click"], trigger > 0.9f, 0);
		input->UpdateBooleanComponent(m_Bool["trigger_touch"], trigger > 0.0f, 0);
		input->UpdateScalarComponent(m_Scalar["grip"], value("grip"), 0);
		float x = value("stickx"), y = value("sticky");
		input->UpdateScalarComponent(m_Scalar["stickx"], x, 0);
		input->UpdateScalarComponent(m_Scalar["sticky"], y, 0);
		input->UpdateBooleanComponent(m_Bool["stickclick"], value("stickclick") > 0.5f, 0);
		input->UpdateBooleanComponent(m_Bool["stick_touch"],
			value("stickclick") > 0.5f || x != 0.0f || y != 0.0f, 0);
		input->UpdateBooleanComponent(m_Bool["thumbrest"], value("thumbrest") > 0.5f, 0);
	}

private:
	void UpdatePose()
	{
		vr::TrackedDevicePose_t head;
		vr::VRServerDriverHost()->GetRawTrackedDevicePoses(0.0f, &head, 1);
		vr::DriverPose_t pose = {};
		pose.qRotation = {1, 0, 0, 0};
		pose.qDriverFromHeadRotation = {1, 0, 0, 0};
		pose.qWorldFromDriverRotation = {1, 0, 0, 0};
		if (head.bPoseIsValid)
		{
			// Hands rest in front of and below the headset, facing forward.
			const auto& m = head.mDeviceToAbsoluteTracking;
			pose.qWorldFromDriverRotation = QuaternionFromMatrix(m);
			for (int axis = 0; axis < 3; ++axis)
				pose.vecWorldFromDriverTranslation[axis] = m.m[axis][3];
		}
		// posx/posy/posz move the controller from its rest point, in metres
		// relative to the headset (for example arms out: posx -0.5 / 0.5).
		float offset[3] = {};
		float pitch = 0.0f;
		{
			std::lock_guard<std::mutex> guard(m_Lock);
			auto tilt = m_Values.find("pitch");
			if (tilt != m_Values.end())
				pitch = tilt->second;
			const char* names[3] = {"posx", "posy", "posz"};
			for (int axis = 0; axis < 3; ++axis)
			{
				auto it = m_Values.find(names[axis]);
				if (it != m_Values.end())
					offset[axis] = it->second;
			}
		}
		// pitch tilts the controller up (positive) or down, in degrees.
		const double half = pitch * 3.14159265358979 / 360.0;
		pose.qRotation = {std::cos(half), std::sin(half), 0, 0};
		pose.vecPosition[0] = (m_Left ? -0.2 : 0.2) + offset[0];
		pose.vecPosition[1] = -0.3 + offset[1];
		pose.vecPosition[2] = -0.35 + offset[2];
		pose.poseIsValid = true;
		pose.result = vr::TrackingResult_Running_OK;
		pose.deviceIsConnected = true;
		m_Pose = pose;
		vr::VRServerDriverHost()->TrackedDevicePoseUpdated(m_Id, m_Pose, sizeof(m_Pose));
	}

	bool m_Left;
	uint32_t m_Id = vr::k_unTrackedDeviceIndexInvalid;
	vr::DriverPose_t m_Pose = {};
	std::map<std::string, vr::VRInputComponentHandle_t> m_Bool;
	std::map<std::string, vr::VRInputComponentHandle_t> m_Scalar;
	vr::VRInputComponentHandle_t m_Haptic = vr::k_ulInvalidInputComponentHandle;
	std::mutex m_Lock;
	std::map<std::string, float> m_Values;
};

class Provider : public vr::IServerTrackedDeviceProvider
{
public:
	vr::EVRInitError Init(vr::IVRDriverContext* context) override
	{
		VR_INIT_SERVER_DRIVER_CONTEXT(context);
		m_Path = CommandPath();
		for (Controller* controller : {&m_Left, &m_Right})
			vr::VRServerDriverHost()->TrackedDeviceAdded(
				controller->Serial(), vr::TrackedDeviceClass_Controller, controller);
		return vr::VRInitError_None;
	}

	void Cleanup() override { VR_CLEANUP_SERVER_DRIVER_CONTEXT(); }
	const char* const* GetInterfaceVersions() override { return vr::k_InterfaceVersions; }
	bool ShouldBlockStandbyMode() override { return false; }
	void EnterStandby() override {}
	void LeaveStandby() override {}

	void RunFrame() override
	{
		ULONGLONG now = GetTickCount64();
		if (now - m_LastPoll >= 100)
		{
			m_LastPoll = now;
			ReadCommands();
		}
		m_Left.RunFrame();
		m_Right.RunFrame();
		vr::VREvent_t event;
		while (vr::VRServerDriverHost()->PollNextEvent(&event, sizeof(event)))
		{
		}
	}

private:
	void ReadCommands()
	{
		if (m_Path.empty() || GetFileAttributesW(m_Path.c_str()) == INVALID_FILE_ATTRIBUTES)
			return;
		FILE* file = _wfopen(m_Path.c_str(), L"r");
		if (!file)
			return;
		char line[256];
		while (std::fgets(line, sizeof(line), file))
		{
			char hand[16], name[32];
			float value = 0.0f;
			if (std::sscanf(line, "%15s %31s %f", hand, name, &value) != 3)
				continue;
			if (!std::strcmp(hand, "left") || !std::strcmp(hand, "both"))
				m_Left.Set(name, value);
			if (!std::strcmp(hand, "right") || !std::strcmp(hand, "both"))
				m_Right.Set(name, value);
		}
		std::fclose(file);
		DeleteFileW(m_Path.c_str());
	}

	Controller m_Left{true};
	Controller m_Right{false};
	std::wstring m_Path;
	ULONGLONG m_LastPoll = 0;
};

Provider g_Provider;

} // namespace

extern "C" __declspec(dllexport) void* HmdDriverFactory(const char* name, int* error)
{
	if (std::strcmp(name, vr::IServerTrackedDeviceProvider_Version) == 0)
		return &g_Provider;
	if (error)
		*error = vr::VRInitError_Init_InterfaceNotFound;
	return nullptr;
}
