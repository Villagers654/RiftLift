// OVRPlugin compatibility at the Windows OpenXR boundary. Other instances
// retain the runtime's identity and normal OpenXR validation behavior.
#define WIN32_LEAN_AND_MEAN
#define XR_USE_PLATFORM_WIN32
#include <windows.h>
#include <unknwn.h>
#include <openxr/openxr.h>
#include <openxr/openxr_platform.h>
#include <openxr/openxr_loader_negotiation.h>

#include <cstring>
#include <memory>
#include <mutex>
#include <unordered_map>

namespace {
constexpr char LayerName[] = "XR_APILAYER_RIFTLIFT_ovr_compat";
struct Instance {
    XrInstance handle;
    PFN_xrGetInstanceProcAddr get;
    bool ovr;
};
struct Session {
    XrSession handle;
    std::shared_ptr<Instance> instance;
};
std::mutex stateMutex;
std::unordered_map<XrInstance, std::shared_ptr<Instance>> instances;
std::unordered_map<XrSession, std::shared_ptr<Session>> sessions;
std::unordered_map<XrSpace, std::shared_ptr<Session>> spaces;

template<typename Map, typename Handle>
auto find(const Map& map, Handle handle) -> typename Map::mapped_type {
    std::lock_guard<std::mutex> guard(stateMutex);
    auto entry = map.find(handle);
    return entry == map.end() ? nullptr : entry->second;
}

template<typename Function>
Function resolve(const std::shared_ptr<Instance>& instance, const char* name) {
    PFN_xrVoidFunction function = nullptr;
    if (instance) instance->get(instance->handle, name, &function);
    return reinterpret_cast<Function>(function);
}

XrTime poseTime(const std::shared_ptr<Instance>& instance, XrTime time) {
    if (time != 0 || !instance->ovr) return time;
    // Some OVRPlugin versions ask for a current pose using XrTime=0. Convert
    // the current Windows clock through the runtime; never assume its epoch.
    auto convert = resolve<PFN_xrConvertWin32PerformanceCounterToTimeKHR>(
        instance, "xrConvertWin32PerformanceCounterToTimeKHR");
    LARGE_INTEGER counter;
    XrTime converted = 0;
    if (convert && QueryPerformanceCounter(&counter) &&
        XR_SUCCEEDED(convert(instance->handle, &counter, &converted))) return converted;
    return time;
}

XrResult XRAPI_CALL getProperties(XrInstance handle, XrInstanceProperties* properties) {
    auto instance = find(instances, handle);
    auto next = resolve<PFN_xrGetInstanceProperties>(instance, "xrGetInstanceProperties");
    if (!next) return XR_ERROR_HANDLE_INVALID;
    auto result = next(handle, properties);
    if (XR_SUCCEEDED(result) && instance->ovr) std::strcpy(properties->runtimeName, "Oculus");
    return result;
}

XrResult XRAPI_CALL destroyInstance(XrInstance handle) {
    auto instance = find(instances, handle);
    auto next = resolve<PFN_xrDestroyInstance>(instance, "xrDestroyInstance");
    if (!next) return XR_ERROR_HANDLE_INVALID;
    auto result = next(handle);
    if (XR_SUCCEEDED(result)) {
        std::lock_guard<std::mutex> guard(stateMutex);
        for (auto it = spaces.begin(); it != spaces.end();) {
            if (it->second->instance == instance) it = spaces.erase(it); else ++it;
        }
        for (auto it = sessions.begin(); it != sessions.end();) {
            if (it->second->instance == instance) it = sessions.erase(it); else ++it;
        }
        instances.erase(handle);
    }
    return result;
}

XrResult XRAPI_CALL createSession(XrInstance handle, const XrSessionCreateInfo* info, XrSession* output) {
    auto instance = find(instances, handle);
    auto next = resolve<PFN_xrCreateSession>(instance, "xrCreateSession");
    if (!next) return XR_ERROR_HANDLE_INVALID;
    auto result = next(handle, info, output);
    if (XR_SUCCEEDED(result)) {
        try {
            auto session = std::make_shared<Session>(Session{*output, instance});
            std::lock_guard<std::mutex> guard(stateMutex);
            sessions.emplace(*output, session);
        } catch (...) {
            auto destroy = resolve<PFN_xrDestroySession>(instance, "xrDestroySession");
            if (destroy) destroy(*output);
            *output = XR_NULL_HANDLE;
            return XR_ERROR_OUT_OF_MEMORY;
        }
    }
    return result;
}

XrResult XRAPI_CALL destroySession(XrSession handle) {
    auto session = find(sessions, handle);
    if (!session) return XR_ERROR_HANDLE_INVALID;
    auto next = resolve<PFN_xrDestroySession>(session->instance, "xrDestroySession");
    if (!next) return XR_ERROR_FUNCTION_UNSUPPORTED;
    auto result = next(handle);
    if (XR_SUCCEEDED(result)) {
        std::lock_guard<std::mutex> guard(stateMutex);
        for (auto it = spaces.begin(); it != spaces.end();) {
            if (it->second == session) it = spaces.erase(it); else ++it;
        }
        sessions.erase(handle);
    }
    return result;
}

template<typename Function, typename Info>
XrResult createSpace(XrSession handle, const Info* info, XrSpace* output, const char* name) {
    auto session = find(sessions, handle);
    if (!session) return XR_ERROR_HANDLE_INVALID;
    auto next = resolve<Function>(session->instance, name);
    if (!next) return XR_ERROR_FUNCTION_UNSUPPORTED;
    auto result = next(handle, info, output);
    if (XR_SUCCEEDED(result)) {
        try {
            std::lock_guard<std::mutex> guard(stateMutex);
            spaces.emplace(*output, session);
        } catch (...) {
            auto destroy = resolve<PFN_xrDestroySpace>(session->instance, "xrDestroySpace");
            if (destroy) destroy(*output);
            *output = XR_NULL_HANDLE;
            return XR_ERROR_OUT_OF_MEMORY;
        }
    }
    return result;
}
XrResult XRAPI_CALL referenceSpace(XrSession session, const XrReferenceSpaceCreateInfo* info, XrSpace* output) {
    return createSpace<PFN_xrCreateReferenceSpace>(session, info, output, "xrCreateReferenceSpace");
}
XrResult XRAPI_CALL actionSpace(XrSession session, const XrActionSpaceCreateInfo* info, XrSpace* output) {
    return createSpace<PFN_xrCreateActionSpace>(session, info, output, "xrCreateActionSpace");
}
XrResult XRAPI_CALL destroySpace(XrSpace handle) {
    auto session = find(spaces, handle);
    if (!session) return XR_ERROR_HANDLE_INVALID;
    auto next = resolve<PFN_xrDestroySpace>(session->instance, "xrDestroySpace");
    if (!next) return XR_ERROR_FUNCTION_UNSUPPORTED;
    auto result = next(handle);
    if (XR_SUCCEEDED(result)) {
        std::lock_guard<std::mutex> guard(stateMutex);
        spaces.erase(handle);
    }
    return result;
}
XrResult XRAPI_CALL locateSpace(XrSpace handle, XrSpace base, XrTime time, XrSpaceLocation* location) {
    auto session = find(spaces, handle);
    if (!session) return XR_ERROR_HANDLE_INVALID;
    auto next = resolve<PFN_xrLocateSpace>(session->instance, "xrLocateSpace");
    if (!next) return XR_ERROR_FUNCTION_UNSUPPORTED;
    return next(handle, base, poseTime(session->instance, time), location);
}
XrResult XRAPI_CALL locateViews(XrSession handle, const XrViewLocateInfo* info,
    XrViewState* state, uint32_t capacity, uint32_t* count, XrView* views) {
    auto session = find(sessions, handle);
    if (!session) return XR_ERROR_HANDLE_INVALID;
    auto next = resolve<PFN_xrLocateViews>(session->instance, "xrLocateViews");
    if (!next) return XR_ERROR_FUNCTION_UNSUPPORTED;
    if (!info) return next(handle, info, state, capacity, count, views);
    auto adjusted = *info;
    adjusted.displayTime = poseTime(session->instance, info->displayTime);
    return next(handle, &adjusted, state, capacity, count, views);
}

XrResult XRAPI_CALL getProc(XrInstance handle, const char* name, PFN_xrVoidFunction* function) {
    if (!name || !function) return XR_ERROR_VALIDATION_FAILURE;
    *function = nullptr;
    if (!std::strcmp(name, "xrGetInstanceProcAddr")) {
        *function = reinterpret_cast<PFN_xrVoidFunction>(getProc);
        return XR_SUCCESS;
    }
    auto instance = find(instances, handle);
    if (!instance) return handle == XR_NULL_HANDLE ? XR_ERROR_FUNCTION_UNSUPPORTED : XR_ERROR_HANDLE_INVALID;
    auto result = instance->get(handle, name, function);
    if (XR_FAILED(result) || !*function) return result;
#define INTERCEPT(api, wrapper) if (!std::strcmp(name, #api)) *function = reinterpret_cast<PFN_xrVoidFunction>(wrapper)
    INTERCEPT(xrDestroyInstance, destroyInstance);
    if (instance->ovr) {
        INTERCEPT(xrGetInstanceProperties, getProperties);
        INTERCEPT(xrCreateSession, createSession);
        INTERCEPT(xrDestroySession, destroySession);
        INTERCEPT(xrCreateReferenceSpace, referenceSpace);
        INTERCEPT(xrCreateActionSpace, actionSpace);
        INTERCEPT(xrDestroySpace, destroySpace);
        INTERCEPT(xrLocateSpace, locateSpace);
        INTERCEPT(xrLocateViews, locateViews);
    }
#undef INTERCEPT
    return result;
}

XrResult XRAPI_CALL createInstance(const XrInstanceCreateInfo* info,
    const XrApiLayerCreateInfo* layer, XrInstance* output) {
    if (!info || !layer || !output || !layer->nextInfo ||
        layer->structType != XR_LOADER_INTERFACE_STRUCT_API_LAYER_CREATE_INFO ||
        layer->structVersion != XR_API_LAYER_CREATE_INFO_STRUCT_VERSION ||
        layer->structSize != sizeof(XrApiLayerCreateInfo)) return XR_ERROR_INITIALIZATION_FAILED;
    auto next = layer->nextInfo;
    if (!next->nextCreateApiLayerInstance || !next->nextGetInstanceProcAddr ||
        std::strcmp(next->layerName, LayerName)) return XR_ERROR_INITIALIZATION_FAILED;
    auto forwarded = *layer;
    forwarded.nextInfo = next->next;
    auto result = next->nextCreateApiLayerInstance(info, &forwarded, output);
    if (XR_FAILED(result)) return result;
    try {
        // Match the middleware identity, never a game title or executable name.
        bool ovr = !std::strncmp(info->applicationInfo.applicationName, "Oculus VR Plugin", 16);
        auto instance = std::make_shared<Instance>(Instance{*output, next->nextGetInstanceProcAddr, ovr});
        std::lock_guard<std::mutex> guard(stateMutex);
        instances.emplace(*output, instance);
        if (ovr) OutputDebugStringA("RiftLift: OVRPlugin OpenXR compatibility enabled\n");
    } catch (...) {
        PFN_xrVoidFunction function = nullptr;
        next->nextGetInstanceProcAddr(*output, "xrDestroyInstance", &function);
        if (function) reinterpret_cast<PFN_xrDestroyInstance>(function)(*output);
        *output = XR_NULL_HANDLE;
        return XR_ERROR_OUT_OF_MEMORY;
    }
    return result;
}
} // namespace

extern "C" __declspec(dllexport) XrResult XRAPI_CALL xrNegotiateLoaderApiLayerInterface(
    const XrNegotiateLoaderInfo* loader, const char* name, XrNegotiateApiLayerRequest* request) {
    constexpr XrVersion version = XR_MAKE_VERSION(1, 0, 0);
    if (!loader || !name || !request || std::strcmp(name, LayerName) ||
        loader->structType != XR_LOADER_INTERFACE_STRUCT_LOADER_INFO ||
        loader->structVersion != XR_LOADER_INFO_STRUCT_VERSION ||
        loader->structSize != sizeof(XrNegotiateLoaderInfo) ||
        request->structType != XR_LOADER_INTERFACE_STRUCT_API_LAYER_REQUEST ||
        request->structVersion != XR_API_LAYER_INFO_STRUCT_VERSION ||
        request->structSize != sizeof(XrNegotiateApiLayerRequest) ||
        loader->minInterfaceVersion > 1 || loader->maxInterfaceVersion < 1 ||
        loader->minApiVersion > version || loader->maxApiVersion < version) return XR_ERROR_INITIALIZATION_FAILED;
    request->layerInterfaceVersion = 1;
    request->layerApiVersion = version;
    request->getInstanceProcAddr = getProc;
    request->createApiLayerInstance = createInstance;
    return XR_SUCCESS;
}
