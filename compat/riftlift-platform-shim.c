/*
 * RiftLift / Wine compatibility shim for the legacy Oculus Platform SDK.
 *
 * The current Meta runtime authenticates in its Electron client under Wine, but
 * OAF never publishes that session to legacy PC SDK clients. Some applications
 * use the engine-specific initializer while others call the generic Windows
 * initializer, then block before rendering while the legacy SDK waits for that
 * unavailable session. This shim supplies the local initialization, login and
 * entitlement responses for an installed, owned copy and forwards every other
 * export to the original Meta implementation.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#define RIFTLIFT_USER_ID UINT64_C(1)
#define MSG_ENTITLEMENT UINT32_C(0x186B58B1)
#define MSG_LOGGED_IN_USER UINT32_C(0x436F345D)
#define MSG_ACHIEVEMENT_DEFINITIONS UINT32_C(0x03D3458D)
#define MSG_ACHIEVEMENT_PROGRESS UINT32_C(0x4F9FDE1D)
#define MSG_CLOUD_BUCKET_METADATA UINT32_C(0x7327A50D)
#define MSG_LOGGED_IN_USER_FRIENDS UINT32_C(0x587C2A8D)
#define FAKE_MAGIC UINT64_C(0x56414445524f5652)

typedef struct FakeMessage {
    uint64_t magic;
    uint64_t request_id;
    uint32_t type;
} FakeMessage;

static SRWLOCK queue_lock = SRWLOCK_INIT;
static FakeMessage *queue[16];
static unsigned queue_head;
static unsigned queue_tail;
static volatile LONG64 next_request = 1000;
static volatile LONG empty_poll_count;

static bool offline_compat(void)
{
    const char *value = getenv("RIFTLIFT_PLATFORM_OFFLINE");
    return value && *value && strcmp(value, "0") != 0;
}

static uint64_t configured_user_id(void)
{
    const char *value = getenv("RIFTLIFT_USER_ID");
    char *end = NULL;
    unsigned long long parsed;
    if (!value || !*value) {
        return RIFTLIFT_USER_ID;
    }
    parsed = strtoull(value, &end, 10);
    return end && *end == '\0' && parsed ? (uint64_t)parsed : RIFTLIFT_USER_ID;
}

static FARPROC real_proc(const char *name)
{
    HMODULE module = GetModuleHandleA("LibOVRPlatformImpl64_1_real.dll");
    if (!module) {
        module = LoadLibraryA("LibOVRPlatformImpl64_1_real.dll");
    }
    return module ? GetProcAddress(module, name) : NULL;
}

static void log_call(const char *name)
{
    char temp[MAX_PATH];
    char path[MAX_PATH];
    FILE *stream;

    if (!GetTempPathA((DWORD)sizeof(temp), temp)) {
        return;
    }
    if (snprintf(path, sizeof(path), "%sriftlift-platform-shim.log", temp) < 0) {
        return;
    }
    stream = fopen(path, "a");
    if (stream) {
        fprintf(stream, "%lu %s\n", GetTickCount(), name);
        fclose(stream);
    }
}

static uint64_t enqueue(uint32_t type)
{
    FakeMessage *message = (FakeMessage *)calloc(1, sizeof(*message));
    uint64_t request_id = (uint64_t)InterlockedIncrement64(&next_request);
    unsigned next;

    if (!message) {
        return 0;
    }
    message->magic = FAKE_MAGIC;
    message->request_id = request_id;
    message->type = type;

    AcquireSRWLockExclusive(&queue_lock);
    next = (queue_tail + 1) % (sizeof(queue) / sizeof(queue[0]));
    if (next == queue_head) {
        free(queue[queue_head]);
        queue_head = (queue_head + 1) % (sizeof(queue) / sizeof(queue[0]));
    }
    queue[queue_tail] = message;
    queue_tail = next;
    ReleaseSRWLockExclusive(&queue_lock);
    return request_id;
}

__declspec(dllexport) int __cdecl ovr_PlatformInitializeUnrealWindows(const char *app_id)
{
    (void)app_id;
    log_call("initialize: success");
    return 0;
}

__declspec(dllexport) int __cdecl ovr_PlatformInitializeWindows(const char *app_id)
{
    (void)app_id;
    log_call("initialize windows: success");
    return 0;
}

__declspec(dllexport) bool __cdecl ovr_IsPlatformInitialized(void)
{
    return true;
}

__declspec(dllexport) bool __cdecl ovr_IsEntitled(void)
{
    return true;
}

__declspec(dllexport) uint64_t __cdecl ovr_GetLoggedInUserID(void)
{
    return configured_user_id();
}

__declspec(dllexport) uint64_t __cdecl ovr_User_GetLoggedInUser(void)
{
    log_call("login request: success queued");
    return enqueue(MSG_LOGGED_IN_USER);
}

__declspec(dllexport) uint64_t __cdecl ovr_Entitlement_GetIsViewerEntitled(void)
{
    log_call("entitlement request: success queued");
    return enqueue(MSG_ENTITLEMENT);
}

__declspec(dllexport) uint64_t __cdecl ovr_Achievements_GetAllDefinitions(void)
{
    typedef uint64_t(__cdecl *function_type)(void);
    if (!offline_compat()) {
        union { FARPROC source; function_type target; } convert = {real_proc("ovr_Achievements_GetAllDefinitions")};
        return convert.target ? convert.target() : 0;
    }
    log_call("achievements definitions request: empty success queued");
    return enqueue(MSG_ACHIEVEMENT_DEFINITIONS);
}

__declspec(dllexport) uint64_t __cdecl ovr_Achievements_GetAllProgress(void)
{
    typedef uint64_t(__cdecl *function_type)(void);
    if (!offline_compat()) {
        union { FARPROC source; function_type target; } convert = {real_proc("ovr_Achievements_GetAllProgress")};
        return convert.target ? convert.target() : 0;
    }
    log_call("achievements progress request: empty success queued");
    return enqueue(MSG_ACHIEVEMENT_PROGRESS);
}

__declspec(dllexport) uint64_t __cdecl ovr_CloudStorage_LoadBucketMetadata(const char *bucket)
{
    typedef uint64_t(__cdecl *function_type)(const char *);
    if (!offline_compat()) {
        union { FARPROC source; function_type target; } convert = {real_proc("ovr_CloudStorage_LoadBucketMetadata")};
        return convert.target ? convert.target(bucket) : 0;
    }
    (void)bucket;
    log_call("cloud bucket metadata request: empty success queued");
    return enqueue(MSG_CLOUD_BUCKET_METADATA);
}

__declspec(dllexport) uint64_t __cdecl ovr_CloudStorage_Load(const char *bucket, const char *key)
{
    typedef uint64_t(__cdecl *function_type)(const char *, const char *);
    union {
        FARPROC source;
        function_type target;
    } convert = {real_proc("ovr_CloudStorage_Load")};
    function_type function = convert.target;
    log_call("cloud load request forwarded");
    return function ? function(bucket, key) : 0;
}

__declspec(dllexport) uint64_t __cdecl ovr_User_GetLoggedInUserFriends(void)
{
    typedef uint64_t(__cdecl *function_type)(void);
    if (!offline_compat()) {
        union { FARPROC source; function_type target; } convert = {real_proc("ovr_User_GetLoggedInUserFriends")};
        return convert.target ? convert.target() : 0;
    }
    log_call("friends request: empty success queued");
    return enqueue(MSG_LOGGED_IN_USER_FRIENDS);
}

/*
 * Unreal's Oculus online subsystem treats these as paged collections.  Empty,
 * successful collections are sufficient for optional achievements, cloud-save
 * enumeration and friends.  Returning the owning message as the collection
 * handle keeps the lifetime tied to ovr_FreeMessage without extra allocation.
 */
__declspec(dllexport) void *__cdecl ovr_Message_GetAchievementDefinitionArray(const void *object)
{
    typedef void *(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) return (void *)object;
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_Message_GetAchievementDefinitionArray")};
    return convert.target ? convert.target(object) : NULL;
}

__declspec(dllexport) size_t __cdecl ovr_AchievementDefinitionArray_GetSize(const void *object)
{
    typedef size_t(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) return 0;
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_AchievementDefinitionArray_GetSize")};
    return convert.target ? convert.target(object) : 0;
}

__declspec(dllexport) bool __cdecl ovr_AchievementDefinitionArray_HasNextPage(const void *object)
{
    typedef bool(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) return false;
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_AchievementDefinitionArray_HasNextPage")};
    return convert.target ? convert.target(object) : false;
}

__declspec(dllexport) void *__cdecl ovr_Message_GetAchievementProgressArray(const void *object)
{
    typedef void *(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) return (void *)object;
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_Message_GetAchievementProgressArray")};
    return convert.target ? convert.target(object) : NULL;
}

__declspec(dllexport) size_t __cdecl ovr_AchievementProgressArray_GetSize(const void *object)
{
    typedef size_t(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) return 0;
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_AchievementProgressArray_GetSize")};
    return convert.target ? convert.target(object) : 0;
}

__declspec(dllexport) bool __cdecl ovr_AchievementProgressArray_HasNextPage(const void *object)
{
    typedef bool(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) return false;
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_AchievementProgressArray_HasNextPage")};
    return convert.target ? convert.target(object) : false;
}

__declspec(dllexport) void *__cdecl ovr_Message_GetCloudStorageMetadataArray(const void *object)
{
    typedef void *(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) return (void *)object;
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_Message_GetCloudStorageMetadataArray")};
    return convert.target ? convert.target(object) : NULL;
}

__declspec(dllexport) size_t __cdecl ovr_CloudStorageMetadataArray_GetSize(const void *object)
{
    typedef size_t(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) return 0;
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_CloudStorageMetadataArray_GetSize")};
    return convert.target ? convert.target(object) : 0;
}

__declspec(dllexport) bool __cdecl ovr_CloudStorageMetadataArray_HasNextPage(const void *object)
{
    typedef bool(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) return false;
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_CloudStorageMetadataArray_HasNextPage")};
    return convert.target ? convert.target(object) : false;
}

__declspec(dllexport) void *__cdecl ovr_Message_GetUserArray(const void *object)
{
    typedef void *(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) return (void *)object;
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_Message_GetUserArray")};
    return convert.target ? convert.target(object) : NULL;
}

__declspec(dllexport) size_t __cdecl ovr_UserArray_GetSize(const void *object)
{
    typedef size_t(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) return 0;
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_UserArray_GetSize")};
    return convert.target ? convert.target(object) : 0;
}

__declspec(dllexport) bool __cdecl ovr_UserArray_HasNextPage(const void *object)
{
    typedef bool(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) return false;
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_UserArray_HasNextPage")};
    return convert.target ? convert.target(object) : false;
}

__declspec(dllexport) void *__cdecl ovr_PopMessage(void)
{
    typedef void *(__cdecl *function_type)(void);
    FakeMessage *message = NULL;

    AcquireSRWLockExclusive(&queue_lock);
    if (queue_head != queue_tail) {
        message = queue[queue_head];
        queue[queue_head] = NULL;
        queue_head = (queue_head + 1) % (sizeof(queue) / sizeof(queue[0]));
    }
    ReleaseSRWLockExclusive(&queue_lock);

    if (message) {
        log_call("message popped");
    } else if (InterlockedIncrement(&empty_poll_count) <= 3) {
        log_call("empty message poll");
    }
    if (message) {
        return message;
    }
    union {
        FARPROC source;
        function_type target;
    } convert = {real_proc("ovr_PopMessage")};
    return convert.target ? convert.target() : NULL;
}

__declspec(dllexport) uint64_t __cdecl ovr_Message_GetRequestID(const void *object)
{
    typedef uint64_t(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) {
        log_call("message request id read");
    }
    if (message && message->magic == FAKE_MAGIC) {
        return message->request_id;
    }
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_Message_GetRequestID")};
    return convert.target ? convert.target(object) : 0;
}

__declspec(dllexport) uint32_t __cdecl ovr_Message_GetType(const void *object)
{
    typedef uint32_t(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) {
        log_call("message type read");
    }
    if (message && message->magic == FAKE_MAGIC) {
        return message->type;
    }
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_Message_GetType")};
    return convert.target ? convert.target(object) : 0;
}

__declspec(dllexport) bool __cdecl ovr_Message_IsError(const void *object)
{
    typedef bool(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) {
        return false;
    }
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_Message_IsError")};
    return convert.target ? convert.target(object) : false;
}

__declspec(dllexport) void *__cdecl ovr_Message_GetUser(const void *object)
{
    typedef void *(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) {
        return (void *)object;
    }
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_Message_GetUser")};
    return convert.target ? convert.target(object) : NULL;
}

__declspec(dllexport) uint64_t __cdecl ovr_User_GetID(const void *object)
{
    typedef uint64_t(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) {
        return configured_user_id();
    }
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_User_GetID")};
    return convert.target ? convert.target(object) : configured_user_id();
}

__declspec(dllexport) const char *__cdecl ovr_User_GetOculusID(const void *object)
{
    typedef const char *(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    const char *name = getenv("RIFTLIFT_USER_NAME");
    if (message && message->magic == FAKE_MAGIC) {
        return name && *name ? name : "RiftLift User";
    }
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_User_GetOculusID")};
    return convert.target ? convert.target(object) : (name && *name ? name : "RiftLift User");
}

__declspec(dllexport) void __cdecl ovr_FreeMessage(void *object)
{
    typedef void(__cdecl *function_type)(void *);
    FakeMessage *message = (FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) {
        log_call("message freed");
        message->magic = 0;
        free(message);
        return;
    }
    union {
        FARPROC source;
        function_type target;
    } convert = {real_proc("ovr_FreeMessage")};
    if (convert.target) {
        convert.target(object);
    }
}
