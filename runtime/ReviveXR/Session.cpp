#include "OVR_CAPI.h"
#include "XR_Math.h"
#include "Common.h"
#include "Session.h"
#include "Runtime.h"
#include "InputManager.h"

#define XR_USE_GRAPHICS_API_D3D11
#include <d3d11.h>
#include <dxgi1_2.h>
#include <openxr/openxr.h>
#include <openxr/openxr_platform.h>
#include <wrl/client.h>
#include <chrono>
#include <thread>

using namespace std::chrono_literals;

ovrResult ovrHmdStruct::InitSession(XrInstance instance)
{
	XR_FUNCTION(instance, GetD3D11GraphicsRequirementsKHR);

	memset(FrameStats, 0, sizeof(FrameStats));
	for (int i = 0; i < ovrMaxProvidedFrameStats; i++)
		FrameStats[i].type = XR_TYPE_FRAME_STATE;
	CurrentFrame = FrameStats;
	Instance = instance;
	TrackingOrigin = ovrTrackingOrigin_EyeLevel;
	SystemProperties = XR_TYPE(SYSTEM_PROPERTIES);
	SystemColorSpace = XR_TYPE(SYSTEM_COLOR_SPACE_PROPERTIES_FB);

	// Initialize view structures
	for (int i = 0; i < ovrEye_Count; i++)
	{
		ViewConfigs[i] = XR_TYPE(VIEW_CONFIGURATION_VIEW);
		ViewFov[i] = XR_TYPE(VIEW_CONFIGURATION_VIEW_FOV_EPIC);
		ViewPoses[i] = XR_TYPE(VIEW);
		ViewConfigs[i].next = &ViewFov[i];
	}

	XrSystemGetInfo systemInfo = XR_TYPE(SYSTEM_GET_INFO);
	systemInfo.formFactor = XR_FORM_FACTOR_HEAD_MOUNTED_DISPLAY;
	CHK_XR(xrGetSystem(Instance, &systemInfo, &System));
	if (Runtime::Get().ColorSpace)
		SystemProperties.next = &SystemColorSpace;
	CHK_XR(xrGetSystemProperties(Instance, System, &SystemProperties));

	uint32_t numViews;
	CHK_XR(xrEnumerateViewConfigurationViews(Instance, System, XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO, ovrEye_Count, &numViews, ViewConfigs));
	assert(numViews == ovrEye_Count);

	XrGraphicsRequirementsD3D11KHR graphicsReq = XR_TYPE(GRAPHICS_REQUIREMENTS_D3D11_KHR);
	CHK_XR(GetD3D11GraphicsRequirementsKHR(Instance, System, &graphicsReq));

	// Copy the LUID into the structure
	static_assert(sizeof(graphicsReq.adapterLuid) == sizeof(ovrGraphicsLuid),
		"The adapter LUID needs to fit in ovrGraphicsLuid");
	memcpy(&Adapter, &graphicsReq.adapterLuid, sizeof(ovrGraphicsLuid));

	// Create a temporary session to retrieve the headset field-of-view
	Microsoft::WRL::ComPtr<IDXGIFactory1> pFactory = NULL;
	if (Runtime::Get().MinorVersion >= 17 && Runtime::Get().Supports(XR_EPIC_VIEW_CONFIGURATION_FOV_EXTENSION_NAME) &&
		!Runtime::Get().UseHack(Runtime::HACK_FORCE_FOV_FALLBACK))
	{
		for (int i = 0; i < ovrEye_Count; i++)
		{
			ViewPoses[i].fov = ViewFov[i].recommendedFov;
			ViewPoses[i].pose = XR::Posef::Identity();
		}
	}
	else
	{
		if (Runtime::Get().Supports(XR_MND_HEADLESS_EXTENSION_NAME))
		{
			// A temporary graphics session is only needed to query the runtime's
			// view poses. Prefer the standard headless extension when available so
			// initialization does not depend on a throwaway D3D/DXVK device.
			CHK_OVR(StartSession(nullptr));
		}
		else if (SUCCEEDED(CreateDXGIFactory1(__uuidof(IDXGIFactory1), (void**)&pFactory)))
		{
			Microsoft::WRL::ComPtr<IDXGIAdapter1> pAdapter;
			Microsoft::WRL::ComPtr<ID3D11Device> pDevice;

			for (UINT i = 0;; ++i)
			{
				Microsoft::WRL::ComPtr<IDXGIAdapter1> candidate;
				if (pFactory->EnumAdapters1(i, &candidate) == DXGI_ERROR_NOT_FOUND)
					break;
				if (!pAdapter)
					pAdapter = candidate;

				DXGI_ADAPTER_DESC1 adapterDesc;
				if (SUCCEEDED(candidate->GetDesc1(&adapterDesc)) &&
					memcmp(&adapterDesc.AdapterLuid, &graphicsReq.adapterLuid, sizeof(graphicsReq.adapterLuid)) == 0)
				{
					pAdapter = candidate;
					break;
				}
			}

			// WineOpenXR normally exposes the runtime GPU through a matching DXGI
			// LUID. Some Wine/DXVK combinations cannot provide a stable LUID,
			// though, so use the first enumerated DXGI adapter as a fallback.
			if (!pAdapter)
				return ovrError_IncompatibleGPU;

			HRESULT hr = D3D11CreateDevice(pAdapter.Get(),
				D3D_DRIVER_TYPE_UNKNOWN, 0, 0,
				NULL, 0, D3D11_SDK_VERSION,
				&pDevice, nullptr, nullptr);
			if (FAILED(hr) || !pDevice)
				return ovrError_IncompatibleGPU;

			XrGraphicsBindingD3D11KHR graphicsBinding = XR_TYPE(GRAPHICS_BINDING_D3D11_KHR);
			graphicsBinding.device = pDevice.Get();
			CHK_OVR(StartSession(&graphicsBinding));
		}

		if (Session)
		{
			// A temporary session follows the same lifecycle rules as a render
			// session: xrLocateViews is not valid until the runtime has made the
			// session ready and the application has begun it.  Do this for every
			// runtime instead of relying on a runtime-specific workaround flag.
			// This keeps early, pre-swapchain Oculus queries independent of the
			// graphics API and device that the application will eventually supply.
			XrEventDataBuffer event;
			const XrEventDataSessionStateChanged& stateChanged =
				reinterpret_cast<XrEventDataSessionStateChanged&>(event);
			const auto deadline = std::chrono::steady_clock::now() + 10s;
			do
			{
				event = XR_TYPE(EVENT_DATA_BUFFER);
				XrResult result = xrPollEvent(Instance, &event);
				if (XR_FAILED(result) && result != XR_EVENT_UNAVAILABLE)
					return ResultToOvrResult(result);
				if (result == XR_EVENT_UNAVAILABLE)
					std::this_thread::sleep_for(10ms);
				if (std::chrono::steady_clock::now() >= deadline)
					return ovrError_Timeout;
			} while (event.type != XR_TYPE_EVENT_DATA_SESSION_STATE_CHANGED ||
				stateChanged.session != Session ||
				stateChanged.state != XR_SESSION_STATE_READY);
			assert(stateChanged.session == Session);

			XrSessionBeginInfo beginInfo = XR_TYPE(SESSION_BEGIN_INFO);
			beginInfo.primaryViewConfigurationType = XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO;
			CHK_XR(xrBeginSession(Session, &beginInfo));
		}

		if (Session)
			CHK_OVR(LocateViews(ViewPoses));
		for (int i = 0; i < ovrEye_Count; i++)
		{
			ViewFov[i].recommendedFov = ViewPoses[i].fov;
			ViewFov[i].maxMutableFov = ViewPoses[i].fov;
		}

		if (Session)
		{
			CHK_XR(xrGetReferenceSpaceBoundsRect(Session, XR_REFERENCE_SPACE_TYPE_STAGE, &bounds));
			CHK_OVR(DestroySession());
		}
	}

	// Calculate the pixels per tan angle
	for (int i = 0; i < ovrEye_Count; i++)
	{
		const XR::FovPort fov(ViewFov[i].recommendedFov);
		PixelsPerTan[i] = OVR::Vector2f(
			(float)ViewConfigs[i].recommendedImageRectWidth / (fov.LeftTan + fov.RightTan),
			(float)ViewConfigs[i].recommendedImageRectHeight / (fov.UpTan + fov.DownTan)
		);
	}

	// Initialize input manager
	Input.reset(new InputManager(Instance));
	return ovrSuccess;
}

ovrResult ovrHmdStruct::StartSession(void* graphicsBinding)
{
	if (Session)
		return ovrError_InvalidOperation;

	XrSessionCreateInfo createInfo = XR_TYPE(SESSION_CREATE_INFO);
	createInfo.next = graphicsBinding;
	createInfo.systemId = System;
	CHK_XR(xrCreateSession(Instance, &createInfo, &Session));
	SessionRunning = false;
	// Several Oculus titles query presence before creating their first graphics
	// swapchain. Seed the status to the connected state; real OpenXR lifecycle
	// events continue to update these bits in ovr_GetSessionStatus.
	SessionStatusBits initialStatus = {};
	initialStatus.IsVisible = true;
	initialStatus.HmdPresent = true;
	initialStatus.HmdMounted = true;
	initialStatus.HasInputFocus = true;
	SessionStatus = initialStatus;

	// Attach it to the InputManager
	if (Input)
		Input->AttachSession(Session);

	// Create reference spaces
	XrReferenceSpaceCreateInfo spaceInfo = XR_TYPE(REFERENCE_SPACE_CREATE_INFO);
	spaceInfo.poseInReferenceSpace = XR::Posef::Identity();
	spaceInfo.referenceSpaceType = XR_REFERENCE_SPACE_TYPE_VIEW;
	CHK_XR(xrCreateReferenceSpace(Session, &spaceInfo, &ViewSpace));
	spaceInfo.referenceSpaceType = XR_REFERENCE_SPACE_TYPE_LOCAL;
	CHK_XR(xrCreateReferenceSpace(Session, &spaceInfo, &OriginSpaces[ovrTrackingOrigin_EyeLevel]));
	CHK_XR(xrCreateReferenceSpace(Session, &spaceInfo, &TrackingSpaces[ovrTrackingOrigin_EyeLevel]));
	spaceInfo.referenceSpaceType = XR_REFERENCE_SPACE_TYPE_STAGE;
	CHK_XR(xrCreateReferenceSpace(Session, &spaceInfo, &OriginSpaces[ovrTrackingOrigin_FloorLevel]));
	CHK_XR(xrCreateReferenceSpace(Session, &spaceInfo, &TrackingSpaces[ovrTrackingOrigin_FloorLevel]));

	// A headless session exists only long enough to locate the headset views
	// for SDK clients that ask for FOV data before supplying a render device.
	// It has no graphics API, swapchain formats, or render visibility masks.
	// Defer all graphics-dependent setup until the application creates its real
	// swapchain and StartSession is called with that API's graphics binding.
	if (!graphicsBinding)
	{
		Running.second.notify_all();
		return ovrSuccess;
	}

	// Update the visibility mask for both eyes
	if (Runtime::Get().VisibilityMask)
	{
		for (uint32_t i = 0; i < ovrEye_Count; i++)
		{
			UpdateStencil((ovrEyeType)i, XR_VISIBILITY_MASK_TYPE_HIDDEN_TRIANGLE_MESH_KHR);
			UpdateStencil((ovrEyeType)i, XR_VISIBILITY_MASK_TYPE_VISIBLE_TRIANGLE_MESH_KHR);
			UpdateStencil((ovrEyeType)i, XR_VISIBILITY_MASK_TYPE_LINE_LOOP_KHR);
		}
	}

	// Enumerate formats
	uint32_t formatCount = 0;
	CHK_XR(xrEnumerateSwapchainFormats(Session, 0, &formatCount, nullptr));
	SupportedFormats.resize(formatCount);
	CHK_XR(xrEnumerateSwapchainFormats(Session, (uint32_t)SupportedFormats.size(), &formatCount, SupportedFormats.data()));
	assert(formatCount == SupportedFormats.size());

	Running.second.notify_all();

	return ovrSuccess;
}

ovrResult ovrHmdStruct::BeginSession()
{
	XrSessionBeginInfo beginInfo = XR_TYPE(SESSION_BEGIN_INFO);
	beginInfo.primaryViewConfigurationType = XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO;
	CHK_XR(xrBeginSession(Session, &beginInfo));
	// Some Oculus clients wait for input focus before entering their render
	// loop. Advance OpenXR with one complete, empty frame before publishing
	// session readiness. Completing the frame here is important: leaving the
	// bootstrap frame open races explicit Wait/Begin calls on the render thread.
	XrIndexedFrameState* frameState = &FrameStats[0];
	XrFrameWaitInfo waitInfo = XR_TYPE(FRAME_WAIT_INFO);
	XrResult waitResult = xrWaitFrame(Session, &waitInfo, frameState);
	TraceOculusValue("bootstrap.xrWaitFrame.result", waitResult);
	CHK_XR(waitResult);
	frameState->frameIndex = 0;
	CurrentFrame = frameState;
	RecenterSpace(ovrTrackingOrigin_EyeLevel, ViewSpace);

	XrFrameBeginInfo frameBeginInfo = XR_TYPE(FRAME_BEGIN_INFO);
	XrResult frameBeginResult = xrBeginFrame(Session, &frameBeginInfo);
	TraceOculusValue("bootstrap.xrBeginFrame.result", frameBeginResult);
	CHK_XR(frameBeginResult);

	XrFrameEndInfo frameEndInfo = XR_TYPE(FRAME_END_INFO);
	frameEndInfo.displayTime = frameState->predictedDisplayTime;
	frameEndInfo.environmentBlendMode = XR_ENVIRONMENT_BLEND_MODE_OPAQUE;
	XrResult frameEndResult = xrEndFrame(Session, &frameEndInfo);
	TraceOculusValue("bootstrap.xrEndFrame.result", frameEndResult);
	CHK_XR(frameEndResult);

	FrameBegun = false;
	SessionRunning = true;
	Running.second.notify_all();
	return ovrSuccess;
}

ovrResult ovrHmdStruct::EndSession()
{
	SessionRunning = false;
	FrameBegun = false;
	CHK_XR(xrEndSession(Session));
	return ovrSuccess;
}

ovrResult ovrHmdStruct::DestroySession()
{
	if (!Session)
		return ovrError_InvalidOperation;
	SessionRunning = false;
	FrameBegun = false;

	if (Input)
		Input->AttachSession(XR_NULL_HANDLE);

	CHK_XR(xrDestroySession(Session));
	Session = XR_NULL_HANDLE;
	ViewSpace = XR_NULL_HANDLE;
	for (uint32_t i = 0; i < ovrTrackingOrigin_Count; i++)
	{
		OriginSpaces[i] = XR_NULL_HANDLE;
		TrackingSpaces[i] = XR_NULL_HANDLE;
	}
	return ovrSuccess;
}

ovrResult ovrHmdStruct::LocateViews(XrView out_Views[ovrEye_Count], XrViewStateFlags* out_Flags) const
{
	if (!Session)
		return ovrError_InvalidSession;

	uint32_t numViews;
	XrViewLocateInfo locateInfo = XR_TYPE(VIEW_LOCATE_INFO);
	XrViewState viewState = XR_TYPE(VIEW_STATE);
	locateInfo.space = ViewSpace;
	locateInfo.viewConfigurationType = XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO;
	locateInfo.displayTime = AbsTimeToXrTime(Instance, ovr_GetTimeInSeconds());
	CHK_XR(xrLocateViews(Session, &locateInfo, &viewState, ovrEye_Count, &numViews, out_Views));
	assert(numViews == ovrEye_Count);
	if (out_Flags)
		*out_Flags = viewState.viewStateFlags;
	return ovrSuccess;
}

ovrResult ovrHmdStruct::UpdateStencil(ovrEyeType view, XrVisibilityMaskTypeKHR type)
{
	if (!Session)
		return ovrError_InvalidSession;

	XR_FUNCTION(Instance, GetVisibilityMaskKHR);

	VisibilityMask& result = VisibilityMasks[view][type];
	XrVisibilityMaskKHR mask = XR_TYPE(VISIBILITY_MASK_KHR);
	CHK_XR(GetVisibilityMaskKHR(Session, XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO, view, type, &mask));
	if (!mask.vertexCountOutput || !mask.indexCountOutput)
		return ovrError_Unsupported;

	result.first.resize(mask.vertexCountOutput);
	result.second.resize(mask.indexCountOutput);

	mask.vertexCapacityInput = (uint32_t)result.first.size();
	mask.vertices = result.first.data();
	mask.indexCapacityInput = (uint32_t)result.second.size();
	mask.indices = result.second.data();
	CHK_XR(GetVisibilityMaskKHR(Session, XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO, view, type, &mask));

	if (type == XR_VISIBILITY_MASK_TYPE_LINE_LOOP_KHR && Runtime::Get().UseHack(Runtime::HACK_BROKEN_LINE_LOOP))
	{
		// There are actually only 27 valid vertices in this line loop
		result.first.resize(27);
		result.second.resize(27);
	}
	return ovrSuccess;
}

ovrResult ovrHmdStruct::RecenterSpace(ovrTrackingOrigin origin, XrSpace anchor, ovrPosef offset)
{
	std::lock_guard<std::shared_mutex> lk(TrackingMutex);
	XrSpaceLocation location = XR_TYPE(SPACE_LOCATION);
	CHK_XR(xrLocateSpace(anchor, OriginSpaces[origin], (*CurrentFrame).predictedDisplayTime, &location));

	if (!(location.locationFlags & (XR_SPACE_LOCATION_ORIENTATION_VALID_BIT | XR_SPACE_LOCATION_POSITION_VALID_BIT)))
		return ovrError_InvalidHeadsetOrientation;

	// Get the yaw orientation from the view pose
	float yaw;
	XR::Quatf(location.pose.orientation).GetYawPitchRoll(&yaw, nullptr, nullptr);

	// Construct the new origin pose
	XR::Posef newOrigin(OVR::Quatf(OVR::Axis_Y, yaw), XR::Vector3f(location.pose.position));

	// For floor level spaces we keep the height at the floor
	if (origin == ovrTrackingOrigin_FloorLevel)
		newOrigin.Translation.y = 0.0f;

	// Replace the tracking space with the newly calibrated one
	XrReferenceSpaceCreateInfo spaceInfo = XR_TYPE(REFERENCE_SPACE_CREATE_INFO);
	spaceInfo.referenceSpaceType = static_cast<XrReferenceSpaceType>(XR_REFERENCE_SPACE_TYPE_LOCAL + origin);
	spaceInfo.poseInReferenceSpace = XR::Posef(newOrigin * offset);
	CHK_XR(xrDestroySpace(TrackingSpaces[origin]));
	CHK_XR(xrCreateReferenceSpace(Session, &spaceInfo, &TrackingSpaces[origin]));
	return ovrSuccess;
}

bool ovrHmdStruct::SupportsFormat(int64_t format) const
{
	return std::find(SupportedFormats.begin(), SupportedFormats.end(), format) != SupportedFormats.end();
}
