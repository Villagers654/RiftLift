#include <Windows.h>
#include <stdio.h>
#include <dxgi.h>
#include <Shlwapi.h>
#include <string>
#include <vector>
#include <detours/detours.h>

#include "Extras\OVR_CAPI_Util.h"
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
static HRESULT(WINAPI* TrueDXGIFactory)(REFIID riid, void **ppFactory) = CreateDXGIFactory;

HMODULE revModule;
CHAR revModuleNameA[MAX_PATH];
CHAR ovrModuleNameA[MAX_PATH];
WCHAR revModuleName[MAX_PATH];
WCHAR ovrModuleName[MAX_PATH];

HANDLE WINAPI HookOpenEvent(DWORD, BOOL, LPCWSTR);
HMODULE WINAPI HookLoadLibraryA(LPCSTR);
HMODULE WINAPI HookLoadLibraryExA(LPCSTR, HANDLE, DWORD);
HMODULE WINAPI HookLoadLibraryW(LPCWSTR);
HMODULE WINAPI HookLoadLibraryExW(LPCWSTR, HANDLE, DWORD);
HMODULE WINAPI HookGetModuleHandleA(LPCSTR);
HMODULE WINAPI HookGetModuleHandleW(LPCWSTR);
BOOL WINAPI HookGetModuleHandleExA(DWORD, LPCSTR, HMODULE*);
BOOL WINAPI HookGetModuleHandleExW(DWORD, LPCWSTR, HMODULE*);
HRESULT WINAPI HookDXGIFactory(REFIID, void**);

struct ImportPatch
{
	PVOID* slot;
	PVOID original;
};

std::vector<ImportPatch> importPatches;

void PatchMainImport(const char* target, PVOID replacement)
{
	PBYTE image = reinterpret_cast<PBYTE>(GetModuleHandleW(NULL));
	auto dos = reinterpret_cast<PIMAGE_DOS_HEADER>(image);
	if (!dos || dos->e_magic != IMAGE_DOS_SIGNATURE)
		return;
	auto nt = reinterpret_cast<PIMAGE_NT_HEADERS>(image + dos->e_lfanew);
	if (nt->Signature != IMAGE_NT_SIGNATURE)
		return;
	DWORD importsRva = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT].VirtualAddress;
	if (!importsRva)
		return;
	for (auto descriptor = reinterpret_cast<PIMAGE_IMPORT_DESCRIPTOR>(image + importsRva);
		descriptor->Name; ++descriptor)
	{
		if (!descriptor->OriginalFirstThunk)
			continue;
		auto names = reinterpret_cast<PIMAGE_THUNK_DATA>(image + descriptor->OriginalFirstThunk);
		auto addresses = reinterpret_cast<PIMAGE_THUNK_DATA>(image + descriptor->FirstThunk);
		for (; names->u1.AddressOfData; ++names, ++addresses)
		{
			if (IMAGE_SNAP_BY_ORDINAL(names->u1.Ordinal))
				continue;
			auto import = reinterpret_cast<PIMAGE_IMPORT_BY_NAME>(image + names->u1.AddressOfData);
			if (strcmp(reinterpret_cast<const char*>(import->Name), target) != 0)
				continue;
			PVOID* slot = reinterpret_cast<PVOID*>(&addresses->u1.Function);
			DWORD protection;
			if (VirtualProtect(slot, sizeof(*slot), PAGE_READWRITE, &protection))
			{
				importPatches.push_back({ slot, *slot });
				*slot = replacement;
				VirtualProtect(slot, sizeof(*slot), protection, &protection);
				FlushInstructionCache(GetCurrentProcess(), slot, sizeof(*slot));
			}
		}
	}
}

void PatchMainExecutableImports()
{
	PatchMainImport("LoadLibraryA", reinterpret_cast<PVOID>(HookLoadLibraryA));
	PatchMainImport("LoadLibraryExA", reinterpret_cast<PVOID>(HookLoadLibraryExA));
	PatchMainImport("LoadLibraryW", reinterpret_cast<PVOID>(HookLoadLibraryW));
	PatchMainImport("LoadLibraryExW", reinterpret_cast<PVOID>(HookLoadLibraryExW));
	PatchMainImport("GetModuleHandleA", reinterpret_cast<PVOID>(HookGetModuleHandleA));
	PatchMainImport("GetModuleHandleW", reinterpret_cast<PVOID>(HookGetModuleHandleW));
	PatchMainImport("GetModuleHandleExA", reinterpret_cast<PVOID>(HookGetModuleHandleExA));
	PatchMainImport("GetModuleHandleExW", reinterpret_cast<PVOID>(HookGetModuleHandleExW));
	PatchMainImport("OpenEventW", reinterpret_cast<PVOID>(HookOpenEvent));
	PatchMainImport("CreateDXGIFactory", reinterpret_cast<PVOID>(HookDXGIFactory));
	char message[96];
	sprintf_s(message, "RiftLift: patched %zu executable runtime imports\n", importPatches.size());
	OutputDebugStringA(message);
}

void RestoreMainExecutableImports()
{
	for (auto patch = importPatches.rbegin(); patch != importPatches.rend(); ++patch)
	{
		DWORD protection;
		if (VirtualProtect(patch->slot, sizeof(*patch->slot), PAGE_READWRITE, &protection))
		{
			*patch->slot = patch->original;
			VirtualProtect(patch->slot, sizeof(*patch->slot), protection, &protection);
		}
	}
	importPatches.clear();
}

HRESULT WINAPI HookDXGIFactory(REFIID riid, void **ppFactory)
{
	// We need shared texture support for OpenVR, so force DXGI 1.0 games to use DXGI 1.1
	IDXGIFactory1* pDXGIFactory;
	HRESULT hr = CreateDXGIFactory1(__uuidof(IDXGIFactory1), (void **)&pDXGIFactory);
	if (FAILED(hr))
		return hr;
	return pDXGIFactory->QueryInterface(riid, ppFactory);
}

HANDLE WINAPI HookOpenEvent(DWORD dwDesiredAccess, BOOL bInheritHandle, LPCWSTR lpName)
{
	// Don't touch this, it heavily affects performance in Unity games.
	if (wcscmp(lpName, OVR_HMD_CONNECTED_EVENT_NAME) == 0)
		return ::CreateEventW(NULL, TRUE, TRUE, NULL);

	return TrueOpenEvent(dwDesiredAccess, bInheritHandle, lpName);
}

bool IsOvrRuntimeName(LPCSTR lpModuleName)
{
	return lpModuleName && _stricmp(PathFindFileNameA(lpModuleName), ovrModuleNameA) == 0;
}

bool IsOvrRuntimeName(LPCWSTR lpModuleName)
{
	return lpModuleName && _wcsicmp(PathFindFileNameW(lpModuleName), ovrModuleName) == 0;
}

HMODULE WINAPI HookLoadLibraryA(LPCSTR lpFileName)
{
	LPCSTR name = PathFindFileNameA(lpFileName);
	LPCSTR ext = PathFindExtensionA(name);
	size_t length = ext - name;
	if (_strnicmp(name, ovrModuleNameA, length) == 0)
		return TrueLoadLibraryA(revModuleNameA);
	return TrueLoadLibraryA(lpFileName);
}

HMODULE WINAPI HookLoadLibraryExA(LPCSTR lpFileName, HANDLE file, DWORD flags)
{
	LPCSTR name = PathFindFileNameA(lpFileName);
	LPCSTR ext = PathFindExtensionA(name);
	size_t length = ext - name;
	if (_strnicmp(name, ovrModuleNameA, length) == 0)
		return TrueLoadLibraryExA(revModuleNameA, file, flags);
	return TrueLoadLibraryExA(lpFileName, file, flags);
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

HMODULE WINAPI HookGetModuleHandleA(LPCSTR name)
{
	return IsOvrRuntimeName(name) ? revModule : TrueGetModuleHandleA(name);
}

HMODULE WINAPI HookGetModuleHandleW(LPCWSTR name)
{
	return IsOvrRuntimeName(name) ? revModule : TrueGetModuleHandleW(name);
}

BOOL WINAPI HookGetModuleHandleExA(DWORD flags, LPCSTR name, HMODULE* module)
{
	if (!(flags & GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS) && IsOvrRuntimeName(name))
		return TrueGetModuleHandleExA(flags, revModuleNameA, module);
	return TrueGetModuleHandleExA(flags, name, module);
}

BOOL WINAPI HookGetModuleHandleExW(DWORD flags, LPCWSTR name, HMODULE* module)
{
	if (!(flags & GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS) && IsOvrRuntimeName(name))
		return TrueGetModuleHandleExW(flags, revModuleName, module);
	return TrueGetModuleHandleExW(flags, name, module);
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
	DetourAttach((PVOID*)&TrueOpenEvent, HookOpenEvent);
	DetourAttach((PVOID*)&TrueDXGIFactory, HookDXGIFactory);
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
	DetourDetach((PVOID*)&TrueOpenEvent, HookOpenEvent);
	DetourDetach((PVOID*)&TrueDXGIFactory, HookDXGIFactory);
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
			PatchMainExecutableImports();
			break;
		case DLL_PROCESS_DETACH:
			RestoreMainExecutableImports();
			DetachDetours();
			break;
		default:
			break;
	}
	return TRUE;
}
