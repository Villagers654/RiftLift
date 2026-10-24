#include <windows.h>
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
static uint64_t user;
static bool initialized;
static int real_message;
/* Meta's implementation throws from context-bound calls before it is initialized. */
static void require_context(void) { if (!initialized) RaiseException(0xE06D7363, EXCEPTION_NONCONTINUABLE, 0, NULL); }
__declspec(dllexport) void test_set_user(uint64_t value) { user = value; }
__declspec(dllexport) void test_set_initialized(bool value) { initialized = value; }
__declspec(dllexport) bool ovr_IsPlatformInitialized(void) { return initialized; }
__declspec(dllexport) uint64_t ovr_GetLoggedInUserID(void) { require_context(); return user; }
__declspec(dllexport) uint64_t ovr_AssetFile_GetList(void) { return 71; }
__declspec(dllexport) uint64_t ovr_Achievements_Unlock(const char *name) { (void)name; return 81; }
__declspec(dllexport) uint64_t ovr_User_Get(uint64_t id) { return id + 100; }
__declspec(dllexport) void *ovr_Message_GetNativeMessage(const void *p) { return (void *)p; }
__declspec(dllexport) void *ovr_Message_GetAssetDetailsArray(const void *p) { return (void *)p; }
__declspec(dllexport) size_t ovr_AssetDetailsArray_GetSize(const void *p) { (void)p; return 3; }
__declspec(dllexport) const char *ovr_User_GetDisplayName(const void *p) { (void)p; return "Real user"; }
__declspec(dllexport) int ovr_User_GetPresenceStatus(const void *p) { (void)p; return 2; }
__declspec(dllexport) void *ovr_PopMessage(void) { require_context(); return &real_message; }
