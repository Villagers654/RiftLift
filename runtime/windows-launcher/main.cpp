#include <string>
#include <codecvt>
#include <vector>

#include <Windows.h>
#include <stdio.h>
#include <string.h>
#include <Shlobj.h>
#include <Shlwapi.h>
#include <openvr.h>

extern FILE* g_LogFile;
#define LOG(x, ...) if (g_LogFile) fprintf(g_LogFile, x, __VA_ARGS__); \
					printf(x, __VA_ARGS__); \
					fflush(g_LogFile);

FILE* g_LogFile = NULL;

bool GetOculusBasePath(PWCHAR path, DWORD length)
{
	LONG error = ERROR_SUCCESS;

	HKEY oculusKey;
	error = RegOpenKeyEx(HKEY_LOCAL_MACHINE, L"Software\\Oculus VR, LLC\\Oculus", 0, KEY_READ | KEY_WOW64_32KEY, &oculusKey);
	if (error != ERROR_SUCCESS)
	{
		LOG("Unable to open Oculus key.");
		return false;
	}
	error = RegQueryValueEx(oculusKey, L"Base", NULL, NULL, (PBYTE)path, &length);
	if (error != ERROR_SUCCESS)
	{
		LOG("Unable to read Base path.");
		return false;
	}
	RegCloseKey(oculusKey);

	return true;
}

bool GetLibraryPath(PWCHAR path, DWORD length, PWCHAR guid)
{
	LONG error = ERROR_SUCCESS;

	// Open the libraries key
	WCHAR keyPath[MAX_PATH] = { L"Software\\Oculus VR, LLC\\Oculus\\Libraries\\" };
	HKEY oculusKey;

	// Open the library key
	wcsncat(keyPath, guid, MAX_PATH);
	error = RegOpenKeyExW(HKEY_CURRENT_USER, keyPath, 0, KEY_READ, &oculusKey);
	if (error != ERROR_SUCCESS)
	{
		LOG("Unable to open Library path key.");
		return false;
	}

	// Get the volume path to this library
	DWORD pathSize;
	error = RegQueryValueExW(oculusKey, L"Path", NULL, NULL, NULL, &pathSize);
	PWCHAR volumePath = (PWCHAR)malloc(pathSize);
	error = RegQueryValueExW(oculusKey, L"Path", NULL, NULL, (PBYTE)volumePath, &pathSize);
	RegCloseKey(oculusKey);
	if (error != ERROR_SUCCESS)
	{
		free(volumePath);
		LOG("Unable to read Library path.");
		return false;
	}

	// Resolve the volume path to a mount point
	DWORD total;
	WCHAR volume[50] = { L'\0' };
	wcsncpy(volume, volumePath, 49);
	GetVolumePathNamesForVolumeNameW(volume, path, length, &total);
	wcsncat(path, volumePath + 49, MAX_PATH);
	free(volumePath);

	return true;
}

bool GetDefaultLibraryPath(PWCHAR path, DWORD length)
{
	LONG error = ERROR_SUCCESS;

	// Open the libraries key
	WCHAR keyPath[MAX_PATH] = { L"Software\\Oculus VR, LLC\\Oculus\\Libraries\\" };
	HKEY oculusKey;
	error = RegOpenKeyExW(HKEY_CURRENT_USER, keyPath, 0, KEY_READ, &oculusKey);
	if (error != ERROR_SUCCESS)
	{
		LOG("Unable to open Libraries key.");
		return false;
	}

	// Get the default library
	WCHAR guid[40] = { L'\0' };
	DWORD guidSize = sizeof(guid);
	error = RegQueryValueExW(oculusKey, L"DefaultLibrary", NULL, NULL, (PBYTE)guid, &guidSize);
	RegCloseKey(oculusKey);
	if (error != ERROR_SUCCESS)
	{
		LOG("Unable to read DefaultLibrary guid.");
		return false;
	}

	// Open the default library key
	wcsncat(keyPath, guid, MAX_PATH);
	error = RegOpenKeyExW(HKEY_CURRENT_USER, keyPath, 0, KEY_READ, &oculusKey);
	if (error != ERROR_SUCCESS)
	{
		LOG("Unable to open Library path key.");
		return false;
	}

	// Get the volume path to this library
	DWORD pathSize;
	error = RegQueryValueExW(oculusKey, L"Path", NULL, NULL, NULL, &pathSize);
	PWCHAR volumePath = (PWCHAR)malloc(pathSize);
	error = RegQueryValueExW(oculusKey, L"Path", NULL, NULL, (PBYTE)volumePath, &pathSize);
	RegCloseKey(oculusKey);
	if (error != ERROR_SUCCESS)
	{
		free(volumePath);
		LOG("Unable to read Library path.");
		return false;
	}

	// Resolve the volume path to a mount point
	DWORD total;
	WCHAR volume[50] = { L'\0' };
	wcsncpy(volume, volumePath, 49);
	GetVolumePathNamesForVolumeNameW(volume, path, length, &total);
	wcsncat(path, volumePath + 49, MAX_PATH);
	free(volumePath);

	return true;
}

class StringArray
{
public:
	void add(const std::string& str)
	{
		strings.push_back(str);
	}

	void clear()
	{
		strings.clear();
	}

	const std::string& at(size_t index) const
	{
		return strings.at(index);
	}

	bool empty()
	{
		return ptrs.empty();
	}

	size_t size()
	{
		return ptrs.size();
	}

private:
	std::vector<std::string> strings;
};

bool InjectLibrary(HANDLE process, const std::string& path)
{
	SIZE_T size = path.size() + 1;
	PVOID remotePath = VirtualAllocEx(process, NULL, size,
		MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
	if (!remotePath || !WriteProcessMemory(process, remotePath, path.c_str(), size, NULL))
		return false;
	auto loadLibrary = reinterpret_cast<LPTHREAD_START_ROUTINE>(
		GetProcAddress(GetModuleHandleW(L"kernel32.dll"), "LoadLibraryA"));
	HANDLE thread = CreateRemoteThread(process, NULL, 0, loadLibrary, remotePath, 0, NULL);
	if (!thread)
	{
		VirtualFreeEx(process, remotePath, 0, MEM_RELEASE);
		return false;
	}
	WaitForSingleObject(thread, INFINITE);
	DWORD module = 0;
	GetExitCodeThread(thread, &module);
	CloseHandle(thread);
	VirtualFreeEx(process, remotePath, 0, MEM_RELEASE);
	return module != 0;
}

int wmain(int argc, wchar_t *argv[]) {
	if (argc < 2) {
		printf("usage: RiftLiftLauncher.exe <executable path>\n");
		return -1;
	}

	WCHAR LogPath[MAX_PATH];
	if (SUCCEEDED(SHGetFolderPath(NULL, CSIDL_LOCAL_APPDATA, NULL, 0, LogPath)))
	{
		wcsncat(LogPath, L"\\RiftLift", MAX_PATH);
		
		BOOL exists = PathFileExists(LogPath);
		if (!exists)
			exists = CreateDirectory(LogPath, NULL);

		wcsncat(LogPath, L"\\RiftLiftLauncher.txt", MAX_PATH);
		if (exists)
			g_LogFile = _wfopen(LogPath, L"w");
	}

	LOG("Launched injector with: %ls\n", GetCommandLine());

	char moduleDir[MAX_PATH];
	GetModuleFileNameA(NULL, moduleDir, MAX_PATH);
	PathRemoveFileSpecA(moduleDir);

	bool debug = false;
	bool waitForExit = false;
	bool identifyOpenVRApplication = false;
	StringArray dlls;
	std::string appKey;
	std::wstring workingDirOverride;
	wchar_t path[MAX_PATH] = { 0 };
	for (int i = 1; i < argc; i++)
	{
		if (wcscmp(argv[i], L"/openxr") == 0)
		{
			dlls.add(moduleDir + std::string("\\RiftLiftOpenXR64.dll"));
		}
		else if (wcscmp(argv[i], L"/openvr") == 0)
		{
			// Runtime discovery can be unreliable under Wine because Proton's
			// OpenVR bridge is configured after the injector starts. Let launchers
			// select the classic backend explicitly instead of silently falling
			// back to the OpenXR bridge when a native OpenVR runtime is available.
			dlls.add(moduleDir + std::string("\\openvr_api64.dll"));
			dlls.add(moduleDir + std::string("\\RiftLiftOpenVR64.dll"));
			identifyOpenVRApplication = true;
		}
		else if (wcscmp(argv[i], L"/proxy") == 0)
		{
			dlls.add(moduleDir + std::string("\\LibOVRProxy64.dll"));
		}
		else if (wcscmp(argv[i], L"/app") == 0)
		{
			appKey = "riftlift.app." + std::wstring_convert<std::codecvt_utf8<wchar_t>, wchar_t>().to_bytes(argv[++i]);
		}
		else if (wcscmp(argv[i], L"/base") == 0)
		{
			if (!GetOculusBasePath(path, MAX_PATH))
				return -1;
		}
		else if (wcscmp(argv[i], L"/library") == 0)
		{
			if (!GetLibraryPath(path, MAX_PATH, argv[++i]))
			{
				if (!GetDefaultLibraryPath(path, MAX_PATH))
				{
					return -1;
				}
			}
			wcsncat(path, L"\\", MAX_PATH);
		}
		else if (wcscmp(argv[i], L"/debug") == 0)
		{
			debug = true;
		}
		else if (wcscmp(argv[i], L"/wait") == 0)
		{
			waitForExit = true;
		}
		else if (wcscmp(argv[i], L"/cwd") == 0)
		{
			if (++i >= argc)
			{
				LOG("Missing value for /cwd\n");
				return -1;
			}
			workingDirOverride = argv[i];
		}
		else
		{
			// Concatenate all other arguments
			wcsncat(path, argv[i], MAX_PATH);
			wcsncat(path, L" ", MAX_PATH);
		}
	}

	if (dlls.empty())
	{
		if (vr::VR_IsRuntimeInstalled())
		{
			dlls.add(moduleDir + std::string("\\openvr_api64.dll"));
			dlls.add(moduleDir + std::string("\\RiftLiftOpenVR64.dll"));
		}
		else
		{
			dlls.add(moduleDir + std::string("\\RiftLiftOpenXR64.dll"));
		}
	}
	
	LOG("Command for injector is: %ls\n", path);

	STARTUPINFO si;
	PROCESS_INFORMATION pi;
	ZeroMemory(&si, sizeof(si));
	si.cb = sizeof(si);
	ZeroMemory(&pi, sizeof(pi));

	wchar_t workingDir[MAX_PATH] = { 0 };
	if (!workingDirOverride.empty())
		wcsncpy(workingDir, workingDirOverride.c_str(), MAX_PATH - 1);
	else
		wcsncpy(workingDir, path, MAX_PATH - 1);

	wchar_t* file = NULL;
	wchar_t* ext = NULL;
	if (workingDirOverride.empty())
	{
		// Remove extension
		ext = wcsstr(workingDir, L".exe");
		if (ext)
			*ext = L'\0';

		// Remove filename
		file = wcsrchr(workingDir, L'\\');
		if (file)
			*file = L'\0';
	}

	HANDLE connectedEvent = CreateEventW(NULL, TRUE, TRUE, L"OculusHMDConnected");
	if (!connectedEvent)
	{
		LOG("Failed to create Oculus compatibility event (%lu)\n", GetLastError());
		return -1;
	}
	SetEvent(connectedEvent);

	if (!CreateProcessW(NULL, path, NULL, NULL, FALSE, CREATE_SUSPENDED,
		NULL, (!workingDirOverride.empty() || (file && ext)) ? workingDir : NULL, &si, &pi))
	{
		LOG("Failed to create process (%lu)\n", GetLastError());
		CloseHandle(connectedEvent);
		return -1;
	}

	for (size_t index = 0; index < dlls.size(); ++index)
	{
		if (!InjectLibrary(pi.hProcess, dlls.at(index)))
		{
			LOG("Failed to inject %s (%lu)\n", dlls.at(index).c_str(), GetLastError());
			TerminateProcess(pi.hProcess, static_cast<UINT>(-1));
			CloseHandle(pi.hThread);
			CloseHandle(pi.hProcess);
			CloseHandle(connectedEvent);
			return -1;
		}
	}

	if (ResumeThread(pi.hThread) == static_cast<DWORD>(-1))
	{
		LOG("Failed to resume process (%lu)\n", GetLastError());
		TerminateProcess(pi.hProcess, static_cast<UINT>(-1));
		CloseHandle(pi.hThread);
		CloseHandle(pi.hProcess);
		CloseHandle(connectedEvent);
		return -1;
	}

	LOG("Succesfully injected!\n");

	if (!appKey.empty() && identifyOpenVRApplication)
	{
		vr::EVRInitError err;
		vr::VR_Init(&err, vr::VRApplication_Utility);
		if (err == vr::VRInitError_None)
		{
			if (vr::VRApplications()->IdentifyApplication(pi.dwProcessId, appKey.c_str()) == vr::VRApplicationError_None)
				LOG("Identified application as: %s\n", appKey.c_str());
			vr::VR_Shutdown();
		}
	}

	DWORD exitCode = 0;
	if (waitForExit)
	{
		WaitForSingleObject(pi.hProcess, INFINITE);
		GetExitCodeProcess(pi.hProcess, &exitCode);
	}
	CloseHandle(pi.hThread);
	CloseHandle(pi.hProcess);
	CloseHandle(connectedEvent);
	return static_cast<int>(exitCode);
}
