#include <windows.h>
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
static uint64_t user;
__declspec(dllexport) void test_set_user(uint64_t value) { user = value; }
__declspec(dllexport) uint64_t ovr_GetLoggedInUserID(void) { return user; }
__declspec(dllexport) uint64_t ovr_AssetFile_GetList(void) { return 71; }
__declspec(dllexport) uint64_t ovr_User_Get(uint64_t id) { return id + 100; }
__declspec(dllexport) void *ovr_Message_GetNativeMessage(const void *p) { return (void *)p; }
__declspec(dllexport) void *ovr_Message_GetAssetDetailsArray(const void *p) { return (void *)p; }
__declspec(dllexport) size_t ovr_AssetDetailsArray_GetSize(const void *p) { (void)p; return 3; }
__declspec(dllexport) const char *ovr_User_GetDisplayName(const void *p) { (void)p; return "Real user"; }
__declspec(dllexport) int ovr_User_GetPresenceStatus(const void *p) { (void)p; return 2; }
__declspec(dllexport) void *ovr_PopMessage(void) { return NULL; }
