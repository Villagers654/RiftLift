#define WIN32_LEAN_AND_MEAN
#define XR_USE_PLATFORM_WIN32
#include <windows.h>
#include <unknwn.h>
#include <openxr/openxr.h>
#include <openxr/openxr_platform.h>
#include <openxr/openxr_loader_negotiation.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>

extern "C" XrResult XRAPI_CALL xrNegotiateLoaderApiLayerInterface(
    const XrNegotiateLoaderInfo*, const char*, XrNegotiateApiLayerRequest*);
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "Failed line %d: %s\n", __LINE__, #x); std::exit(1); } } while (0)
static uintptr_t nextHandle = 100;
static XrTime observedTime;
static XrResult propertiesResult = XR_SUCCESS;
static XrResult creationResult = XR_SUCCESS;
static XrResult destroyResult = XR_SUCCESS;
static int destroyedInstances;
static XrResult XRAPI_CALL properties(XrInstance, XrInstanceProperties* output) {
    std::strcpy(output->runtimeName, "Test runtime"); return propertiesResult;
}
static XrResult XRAPI_CALL destroyInstance(XrInstance) { ++destroyedInstances; return destroyResult; }
static XrResult XRAPI_CALL createSession(XrInstance, const XrSessionCreateInfo*, XrSession* output) {
    *output = reinterpret_cast<XrSession>(++nextHandle); return XR_SUCCESS;
}
static XrResult XRAPI_CALL destroySession(XrSession) { return XR_SUCCESS; }
static XrResult XRAPI_CALL referenceSpace(XrSession, const XrReferenceSpaceCreateInfo*, XrSpace* output) {
    *output = reinterpret_cast<XrSpace>(++nextHandle); return XR_SUCCESS;
}
static XrResult XRAPI_CALL actionSpace(XrSession, const XrActionSpaceCreateInfo*, XrSpace* output) {
    *output = reinterpret_cast<XrSpace>(++nextHandle); return XR_SUCCESS;
}
static XrResult XRAPI_CALL destroySpace(XrSpace) { return XR_SUCCESS; }
static XrResult XRAPI_CALL locateSpace(XrSpace, XrSpace, XrTime time, XrSpaceLocation*) {
    observedTime = time; return time > 0 ? XR_SUCCESS : XR_ERROR_TIME_INVALID;
}
static XrResult XRAPI_CALL locateViews(XrSession, const XrViewLocateInfo* info, XrViewState*, uint32_t, uint32_t*, XrView*) {
    observedTime = info->displayTime; return observedTime > 0 ? XR_SUCCESS : XR_ERROR_TIME_INVALID;
}
static XrResult XRAPI_CALL convertTime(XrInstance, const LARGE_INTEGER*, XrTime* output) {
    *output = 123456789; return XR_SUCCESS;
}
static XrResult XRAPI_CALL nextGet(XrInstance, const char* name, PFN_xrVoidFunction* output) {
    *output = nullptr;
#define FUNCTION(n, f) if (!std::strcmp(name, #n)) *output = reinterpret_cast<PFN_xrVoidFunction>(f)
    FUNCTION(xrGetInstanceProperties, properties);
    FUNCTION(xrDestroyInstance, destroyInstance);
    FUNCTION(xrCreateSession, createSession);
    FUNCTION(xrDestroySession, destroySession);
    FUNCTION(xrCreateReferenceSpace, referenceSpace);
    FUNCTION(xrCreateActionSpace, actionSpace);
    FUNCTION(xrDestroySpace, destroySpace);
    FUNCTION(xrLocateSpace, locateSpace);
    FUNCTION(xrLocateViews, locateViews);
    FUNCTION(xrConvertWin32PerformanceCounterToTimeKHR, convertTime);
#undef FUNCTION
    return *output ? XR_SUCCESS : XR_ERROR_FUNCTION_UNSUPPORTED;
}
static XrResult XRAPI_CALL nextCreate(const XrInstanceCreateInfo*, const XrApiLayerCreateInfo* layer, XrInstance* output) {
    CHECK(layer->nextInfo == nullptr);
    if (XR_SUCCEEDED(creationResult)) *output = reinterpret_cast<XrInstance>(++nextHandle);
    return creationResult;
}
static XrNegotiateApiLayerRequest negotiated;
template<typename T> T function(XrInstance instance, const char* name) {
    PFN_xrVoidFunction output = nullptr;
    CHECK(negotiated.getInstanceProcAddr(instance, name, &output) == XR_SUCCESS);
    CHECK(output != nullptr);
    return reinterpret_cast<T>(output);
}
static XrInstance create(const char* name) {
    XrInstanceCreateInfo info{XR_TYPE_INSTANCE_CREATE_INFO};
    std::strcpy(info.applicationInfo.applicationName, name);
    XrApiLayerNextInfo next{};
    next.nextGetInstanceProcAddr = nextGet;
    next.nextCreateApiLayerInstance = nextCreate;
    std::strcpy(next.layerName, "XR_APILAYER_RIFTLIFT_ovr_compat");
    XrApiLayerCreateInfo layer{};
    layer.structType = XR_LOADER_INTERFACE_STRUCT_API_LAYER_CREATE_INFO;
    layer.structVersion = XR_API_LAYER_CREATE_INFO_STRUCT_VERSION;
    layer.structSize = sizeof(layer);
    layer.nextInfo = &next;
    XrInstance result = XR_NULL_HANDLE;
    CHECK(negotiated.createApiLayerInstance(&info, &layer, &result) == creationResult);
    CHECK(layer.nextInfo == &next);
    return result;
}
int main() {
    XrNegotiateLoaderInfo loader{};
    loader.structType = XR_LOADER_INTERFACE_STRUCT_LOADER_INFO;
    loader.structVersion = XR_LOADER_INFO_STRUCT_VERSION;
    loader.structSize = sizeof(loader);
    loader.minInterfaceVersion = loader.maxInterfaceVersion = 1;
    loader.minApiVersion = XR_MAKE_VERSION(1, 0, 0);
    loader.maxApiVersion = XR_MAKE_VERSION(1, 1, 0);
    negotiated.structType = XR_LOADER_INTERFACE_STRUCT_API_LAYER_REQUEST;
    negotiated.structVersion = XR_API_LAYER_INFO_STRUCT_VERSION;
    negotiated.structSize = sizeof(negotiated);
    CHECK(xrNegotiateLoaderApiLayerInterface(&loader, "wrong", &negotiated) == XR_ERROR_INITIALIZATION_FAILED);
    CHECK(xrNegotiateLoaderApiLayerInterface(&loader, "XR_APILAYER_RIFTLIFT_ovr_compat", &negotiated) == XR_SUCCESS);
    auto ordinary = create("Ordinary OpenXR game");
    auto ovr = create("Oculus VR Plugin (Unknown Engine)");
    auto ovr2 = create("Oculus VR Plugin (Unity)");
    CHECK(function<PFN_xrGetInstanceProperties>(ordinary, "xrGetInstanceProperties") == properties);
    XrInstanceProperties p{XR_TYPE_INSTANCE_PROPERTIES};
    auto get = function<PFN_xrGetInstanceProperties>(ovr, "xrGetInstanceProperties");
    CHECK(get(ovr, &p) == XR_SUCCESS && !std::strcmp(p.runtimeName, "Oculus"));
    propertiesResult = XR_ERROR_RUNTIME_FAILURE;
    CHECK(get(ovr, &p) == propertiesResult && !std::strcmp(p.runtimeName, "Test runtime"));
    propertiesResult = XR_SUCCESS;
    PFN_xrVoidFunction unsupported = nullptr;
    CHECK(negotiated.getInstanceProcAddr(ovr, "xrNotImplemented", &unsupported) == XR_ERROR_FUNCTION_UNSUPPORTED);
    CHECK(!unsupported);
    XrSession session;
    XrSessionCreateInfo sessionInfo{XR_TYPE_SESSION_CREATE_INFO};
    CHECK(function<PFN_xrCreateSession>(ovr, "xrCreateSession")(ovr, &sessionInfo, &session) == XR_SUCCESS);
    XrSpace space;
    XrReferenceSpaceCreateInfo spaceInfo{XR_TYPE_REFERENCE_SPACE_CREATE_INFO};
    CHECK(function<PFN_xrCreateReferenceSpace>(ovr, "xrCreateReferenceSpace")(session, &spaceInfo, &space) == XR_SUCCESS);
    auto locate = function<PFN_xrLocateSpace>(ovr, "xrLocateSpace");
    CHECK(locate(space, space, 0, nullptr) == XR_SUCCESS && observedTime == 123456789);
    CHECK(locate(space, space, 42, nullptr) == XR_SUCCESS && observedTime == 42);
    CHECK(locate(space, space, -1, nullptr) == XR_ERROR_TIME_INVALID && observedTime == -1);
    XrViewLocateInfo view{XR_TYPE_VIEW_LOCATE_INFO};
    CHECK(function<PFN_xrLocateViews>(ovr, "xrLocateViews")(session, &view, nullptr, 0, nullptr, nullptr) == XR_SUCCESS);
    CHECK(view.displayTime == 0 && observedTime == 123456789);
    CHECK(function<PFN_xrLocateSpace>(ordinary, "xrLocateSpace") == locateSpace);
    CHECK(function<PFN_xrCreateSession>(ordinary, "xrCreateSession") == createSession);
    CHECK(function<PFN_xrDestroySession>(ovr, "xrDestroySession")(session) == XR_SUCCESS);
    CHECK(locate(space, space, 1, nullptr) == XR_ERROR_HANDLE_INVALID);
    destroyResult = XR_ERROR_RUNTIME_FAILURE;
    auto destroy = function<PFN_xrDestroyInstance>(ovr, "xrDestroyInstance");
    CHECK(destroy(ovr) == destroyResult);
    CHECK(get(ovr, &p) == XR_SUCCESS);
    destroyResult = XR_SUCCESS;
    CHECK(destroy(ovr) == XR_SUCCESS);
    CHECK(get(ovr, &p) == XR_ERROR_HANDLE_INVALID);
    CHECK(function<PFN_xrGetInstanceProperties>(ovr2, "xrGetInstanceProperties")(ovr2, &p) == XR_SUCCESS);
    CHECK(!std::strcmp(p.runtimeName, "Oculus"));
    creationResult = XR_ERROR_EXTENSION_NOT_PRESENT;
    CHECK(create("Oculus VR Plugin") == XR_NULL_HANDLE);
    CHECK(destroyedInstances == 2);
    std::puts("OpenXR layer isolation, forwarding, time conversion and lifetime tests passed");
}
