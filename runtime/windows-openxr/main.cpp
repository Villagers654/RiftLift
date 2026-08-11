#include <Windows.h>
#include <stdio.h>
#include <dxgi.h>
#include <d3d11.h>
#include <Shlwapi.h>
#include <string>
#include <detours/detours.h>

#include "OVR_CAPI.h"
#include "OVR_Version.h"

static HMODULE(WINAPI* TrueLoadLibraryA)(LPCSTR lpFileName) = LoadLibraryA;
static HMODULE(WINAPI* TrueLoadLibraryExA)(LPCSTR lpLibFileName, HANDLE hFile, DWORD dwFlags) = LoadLibraryExA;
static HMODULE(WINAPI* TrueLoadLibraryW)(LPCWSTR lpFileName) = LoadLibraryW;
static HMODULE(WINAPI* TrueLoadLibraryExW)(LPCWSTR lpLibFileName, HANDLE hFile, DWORD dwFlags) = LoadLibraryExW;
static HMODULE(WINAPI* TrueGetModuleHandleA)(LPCSTR lpModuleName) = GetModuleHandleA;
static HMODULE(WINAPI* TrueGetModuleHandleW)(LPCWSTR lpModuleName) = GetModuleHandleW;
static BOOL(WINAPI* TrueGetModuleHandleExA)(DWORD dwFlags, LPCSTR lpModuleName, HMODULE* phModule) = GetModuleHandleExA;
static BOOL(WINAPI* TrueGetModuleHandleExW)(DWORD dwFlags, LPCWSTR lpModuleName, HMODULE* phModule) = GetModuleHandleExW;
static HANDLE(WINAPI* TrueOpenEvent)(DWORD dwDesiredAccess, BOOL bInheritHandle, LPCWSTR lpName) = OpenEventW;

HMODULE revModule;
CHAR revModuleNameA[MAX_PATH];
CHAR ovrModuleNameA[MAX_PATH];
WCHAR revModuleName[MAX_PATH];
WCHAR ovrModuleName[MAX_PATH];

bool IsOvrRuntimeName(LPCSTR lpModuleName)
{
	return lpModuleName && _stricmp(PathFindFileNameA(lpModuleName), ovrModuleNameA) == 0;
}

bool IsOvrRuntimeName(LPCWSTR lpModuleName)
{
	return lpModuleName && _wcsicmp(PathFindFileNameW(lpModuleName), ovrModuleName) == 0;
}

HANDLE WINAPI HookOpenEvent(DWORD dwDesiredAccess, BOOL bInheritHandle, LPCWSTR lpName)
{
	// Don't touch this, it heavily affects performance in Unity games.
	if (wcscmp(lpName, OVR_HMD_CONNECTED_EVENT_NAME) == 0)
		return ::CreateEventW(NULL, TRUE, TRUE, NULL);

	return TrueOpenEvent(dwDesiredAccess, bInheritHandle, lpName);
}

HMODULE WINAPI HookLoadLibraryW(LPCWSTR lpFileName)
{
	LPCWSTR name = PathFindFileNameW(lpFileName);
	LPCWSTR ext = PathFindExtensionW(name);
	size_t length = ext - name;

	// Load our own library again so the ref count is incremented.
	if (wcsncmp(name, ovrModuleName, length) == 0)
		return TrueLoadLibraryW(revModuleName);
	
	return TrueLoadLibraryW(lpFileName);
}

HMODULE WINAPI HookLoadLibraryA(LPCSTR lpFileName)
{
	LPCSTR name = PathFindFileNameA(lpFileName);
	LPCSTR ext = PathFindExtensionA(name);
	size_t length = ext - name;

	// Newer OVRPlugin builds resolve LibOVRRT through the ANSI loader. RiftLift
	// historically hooked only the wide entry points, making a successfully
	// injected runtime invisible and causing games to report no Touch devices.
	if (_strnicmp(name, ovrModuleNameA, length) == 0)
		return TrueLoadLibraryA(revModuleNameA);

	return TrueLoadLibraryA(lpFileName);
}

HMODULE WINAPI HookLoadLibraryExA(LPCSTR lpLibFileName, HANDLE hFile, DWORD dwFlags)
{
	LPCSTR name = PathFindFileNameA(lpLibFileName);
	LPCSTR ext = PathFindExtensionA(name);
	size_t length = ext - name;

	if (_strnicmp(name, ovrModuleNameA, length) == 0)
		return TrueLoadLibraryExA(revModuleNameA, hFile, dwFlags);

	return TrueLoadLibraryExA(lpLibFileName, hFile, dwFlags);
}

HMODULE WINAPI HookLoadLibraryExW(LPCWSTR lpLibFileName, HANDLE hFile, DWORD dwFlags)
{
	LPCWSTR name = PathFindFileNameW(lpLibFileName);
	LPCWSTR ext = PathFindExtensionW(name);
	size_t length = ext - name;

	// Load our own library again so the ref count is incremented.
	if (wcsncmp(name, ovrModuleName, length) == 0) 
		return TrueLoadLibraryExW(revModuleName, hFile, dwFlags);
	
	return TrueLoadLibraryExW(lpLibFileName, hFile, dwFlags);
}

HMODULE WINAPI HookGetModuleHandleA(LPCSTR lpModuleName)
{
	if (IsOvrRuntimeName(lpModuleName))
		return revModule;

	return TrueGetModuleHandleA(lpModuleName);
}

HMODULE WINAPI HookGetModuleHandleW(LPCWSTR lpModuleName)
{
	if (IsOvrRuntimeName(lpModuleName))
		return revModule;

	return TrueGetModuleHandleW(lpModuleName);
}

BOOL WINAPI HookGetModuleHandleExA(DWORD dwFlags, LPCSTR lpModuleName, HMODULE* phModule)
{
	if (!(dwFlags & GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS) && IsOvrRuntimeName(lpModuleName))
		return TrueGetModuleHandleExA(dwFlags, revModuleNameA, phModule);

	return TrueGetModuleHandleExA(dwFlags, lpModuleName, phModule);
}

BOOL WINAPI HookGetModuleHandleExW(DWORD dwFlags, LPCWSTR lpModuleName, HMODULE* phModule)
{
	if (!(dwFlags & GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS) && IsOvrRuntimeName(lpModuleName))
		return TrueGetModuleHandleExW(dwFlags, revModuleName, phModule);

	return TrueGetModuleHandleExW(dwFlags, lpModuleName, phModule);
}

void AttachDetours()
{
	DetourTransactionBegin();
	DetourUpdateThread(GetCurrentThread());
	DetourAttach((PVOID*)&TrueLoadLibraryA, HookLoadLibraryA);
	DetourAttach((PVOID*)&TrueLoadLibraryExA, HookLoadLibraryExA);
	DetourAttach((PVOID*)&TrueLoadLibraryW, HookLoadLibraryW);
	DetourAttach((PVOID*)&TrueLoadLibraryExW, HookLoadLibraryExW);
	DetourAttach((PVOID*)&TrueGetModuleHandleA, HookGetModuleHandleA);
	DetourAttach((PVOID*)&TrueGetModuleHandleW, HookGetModuleHandleW);
	DetourAttach((PVOID*)&TrueGetModuleHandleExA, HookGetModuleHandleExA);
	DetourAttach((PVOID*)&TrueGetModuleHandleExW, HookGetModuleHandleExW);
	DetourAttach(&(PVOID&)TrueOpenEvent, HookOpenEvent);
	DetourTransactionCommit();
}

void DetachDetours()
{
	DetourTransactionBegin();
	DetourUpdateThread(GetCurrentThread());
	DetourDetach((PVOID*)&TrueLoadLibraryA, HookLoadLibraryA);
	DetourDetach((PVOID*)&TrueLoadLibraryExA, HookLoadLibraryExA);
	DetourDetach((PVOID*)&TrueLoadLibraryW, HookLoadLibraryW);
	DetourDetach((PVOID*)&TrueLoadLibraryExW, HookLoadLibraryExW);
	DetourDetach((PVOID*)&TrueGetModuleHandleA, HookGetModuleHandleA);
	DetourDetach((PVOID*)&TrueGetModuleHandleW, HookGetModuleHandleW);
	DetourDetach((PVOID*)&TrueGetModuleHandleExA, HookGetModuleHandleExA);
	DetourDetach((PVOID*)&TrueGetModuleHandleExW, HookGetModuleHandleExW);
	DetourDetach(&(PVOID&)TrueOpenEvent, HookOpenEvent);
	DetourTransactionCommit();
}

BOOL APIENTRY DllMain(HANDLE hModule, DWORD ul_reason_for_call, LPVOID lpReserved)
{
	if (DetourIsHelperProcess())
		return TRUE;

#if defined(_WIN64)
	const char* pBitDepth = "64";
#else
	const char* pBitDepth = "32";
#endif
	switch (ul_reason_for_call)
	{
		case DLL_PROCESS_ATTACH:
			revModule = (HMODULE)hModule;
			GetModuleFileNameA(revModule, revModuleNameA, MAX_PATH);
			GetModuleFileName(revModule, revModuleName, MAX_PATH);
			sprintf_s(ovrModuleNameA, MAX_PATH, "LibOVRRT%s_%d.dll", pBitDepth, OVR_MAJOR_VERSION);
			swprintf(ovrModuleName, MAX_PATH, L"LibOVRRT%hs_%d.dll", pBitDepth, OVR_MAJOR_VERSION);

			DetourRestoreAfterWith();
			AttachDetours();
			break;
		case DLL_PROCESS_DETACH:
			DetachDetours();
			break;
		default:
			break;
	}
	return TRUE;
}
