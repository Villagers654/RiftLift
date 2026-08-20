#include <string>
#include <vector>

#include <Windows.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <Shlobj.h>
#include <Shlwapi.h>
#include <detours/detours.h>
#include <openvr.h>

FILE* g_LogFile = NULL;

void Log(const char* format, ...)
{
	if (!g_LogFile)
		return;

	va_list arguments;
	va_start(arguments, format);
	vfprintf(g_LogFile, format, arguments);
	va_end(arguments);
	fflush(g_LogFile);
}

bool WideToUtf8(const wchar_t* value, std::string& result)
{
	int size = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value, -1,
		NULL, 0, NULL, NULL);
	if (size <= 0)
		return false;
	std::vector<char> buffer(static_cast<size_t>(size));
	if (WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value, -1,
		buffer.data(), size, NULL, NULL) <= 0)
		return false;
	result.assign(buffer.data(), static_cast<size_t>(size - 1));
	return true;
}

bool GetModuleDirectory(std::string& result)
{
	std::vector<char> path(MAX_PATH);
	for (;;)
	{
		DWORD length = GetModuleFileNameA(NULL, path.data(),
			static_cast<DWORD>(path.size()));
		if (length == 0)
			return false;
		if (length < path.size() - 1)
			break;
		path.resize(path.size() * 2);
	}
	if (!PathRemoveFileSpecA(path.data()))
		return false;
	result = path.data();
	return true;
}

std::wstring QuoteArgument(const wchar_t* argument)
{
	std::wstring result = L"\"";
	size_t backslashes = 0;
	for (const wchar_t* current = argument; *current; ++current)
	{
		if (*current == L'\\')
		{
			++backslashes;
			continue;
		}
		if (*current == L'\"')
		{
			result.append(backslashes * 2 + 1, L'\\');
			result.push_back(L'\"');
			backslashes = 0;
			continue;
		}
		result.append(backslashes, L'\\');
		backslashes = 0;
		result.push_back(*current);
	}
	result.append(backslashes * 2, L'\\');
	result.push_back(L'\"');
	return result;
}

std::wstring BuildCommandLine(int argc, wchar_t* argv[], int firstArgument)
{
	std::wstring result;
	for (int index = firstArgument; index < argc; ++index)
	{
		if (!result.empty())
			result.push_back(L' ');
		result += QuoteArgument(argv[index]);
	}
	return result;
}

bool OpenLog()
{
	wchar_t localAppData[MAX_PATH];
	if (FAILED(SHGetFolderPathW(NULL, CSIDL_LOCAL_APPDATA, NULL, 0, localAppData)))
		return false;
	std::wstring directory = std::wstring(localAppData) + L"\\RiftLift";
	if (!CreateDirectoryW(directory.c_str(), NULL) &&
		GetLastError() != ERROR_ALREADY_EXISTS)
		return false;
	std::wstring path = directory + L"\\RiftLiftLauncher.txt";
	g_LogFile = _wfopen(path.c_str(), L"w");
	return g_LogFile != NULL;
}

int wmain(int argc, wchar_t *argv[]) {
	if (argc < 2) {
		return -1;
	}

	OpenLog();
	Log("Launched injector with: %ls\n", GetCommandLine());

	std::string moduleDir;
	if (!GetModuleDirectory(moduleDir))
	{
		Log("Unable to locate launcher module (%lu)\n", GetLastError());
		return -1;
	}
	Log("Launcher module directory: %s\n", moduleDir.c_str());

	bool debug = false;
	bool waitForExit = false;
	bool identifyOpenVRApplication = false;
	std::vector<std::string> dlls;
	std::string appKey;
	std::wstring workingDirOverride;
	int targetIndex = -1;
	for (int i = 1; i < argc; i++)
	{
		Log("Parsing argument %d\n", i);
		if (wcscmp(argv[i], L"/openxr") == 0)
		{
			dlls.push_back(moduleDir + "\\RiftLiftOpenXR64.dll");
		}
		else if (wcscmp(argv[i], L"/openvr") == 0)
		{
			// Runtime discovery can be unreliable under Wine because Proton's
			// OpenVR bridge is configured after the injector starts. Let launchers
			// select the classic backend explicitly instead of silently falling
			// back to the OpenXR bridge when a native OpenVR runtime is available.
			dlls.push_back(moduleDir + "\\openvr_api64.dll");
			dlls.push_back(moduleDir + "\\RiftLiftOpenVR64.dll");
			identifyOpenVRApplication = true;
		}
		else if (wcscmp(argv[i], L"/proxy") == 0)
		{
			dlls.push_back(moduleDir + "\\LibOVRProxy64.dll");
		}
		else if (wcscmp(argv[i], L"/app") == 0)
		{
			if (++i >= argc)
			{
				Log("Missing value for /app (%lu)\n", ERROR_INVALID_PARAMETER);
				return -1;
			}
			std::string key;
			if (!WideToUtf8(argv[i], key))
			{
				Log("Invalid UTF-16 value for /app (%lu)\n", GetLastError());
				return -1;
			}
			appKey = "riftlift.app." + key;
			Log("Parsed OpenVR application key\n");
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
				Log("Missing value for /cwd\n");
				return -1;
			}
			workingDirOverride = argv[i];
			Log("Parsed working directory\n");
		}
		else
		{
			targetIndex = i;
			break;
		}
	}
	if (targetIndex < 0)
	{
		Log("Missing target executable (%lu)\n", ERROR_INVALID_PARAMETER);
		return -1;
	}

	if (dlls.empty())
	{
		if (vr::VR_IsRuntimeInstalled())
		{
			dlls.push_back(moduleDir + "\\openvr_api64.dll");
			dlls.push_back(moduleDir + "\\RiftLiftOpenVR64.dll");
		}
		else
		{
			dlls.push_back(moduleDir + "\\RiftLiftOpenXR64.dll");
		}
	}
	
	std::wstring commandLine = BuildCommandLine(argc, argv, targetIndex);
	Log("Command for injector is: %ls\n", commandLine.c_str());

	STARTUPINFO si;
	PROCESS_INFORMATION pi;
	ZeroMemory(&si, sizeof(si));
	si.cb = sizeof(si);
	ZeroMemory(&pi, sizeof(pi));

	std::wstring workingDir = workingDirOverride;
	if (workingDirOverride.empty())
	{
		workingDir = argv[targetIndex];
		size_t separator = workingDir.find_last_of(L"\\/");
		if (separator == std::wstring::npos)
			workingDir.clear();
		else
			workingDir.resize(separator);
	}

	HANDLE connectedEvent = CreateEventW(NULL, TRUE, TRUE, L"OculusHMDConnected");
	if (!connectedEvent)
	{
		Log("Failed to create Oculus compatibility event (%lu)\n", GetLastError());
		return -1;
	}
	std::vector<const char*> dllPaths;
	dllPaths.reserve(dlls.size());
	for (const auto& dll : dlls)
		dllPaths.push_back(dll.c_str());
	if (!DetourCreateProcessWithDllsW(argv[targetIndex], &commandLine[0], NULL, NULL, FALSE,
		debug ? CREATE_SUSPENDED | DEBUG_ONLY_THIS_PROCESS : 0,
		NULL, workingDir.empty() ? NULL : workingDir.c_str(), &si, &pi,
		static_cast<DWORD>(dllPaths.size()), dllPaths.data(), NULL))
	{
		Log("Failed to create and inject process (%lu)\n", GetLastError());
		CloseHandle(connectedEvent);
		return -1;
	}

	if (debug)
	{
		if (!DebugActiveProcessStop(pi.dwProcessId))
		{
			Log("Failed to stop debugging (%lu)\n", GetLastError());
			TerminateProcess(pi.hProcess, static_cast<UINT>(-1));
			CloseHandle(pi.hThread);
			CloseHandle(pi.hProcess);
			CloseHandle(connectedEvent);
			return -1;
		}

		if (ResumeThread(pi.hThread) == static_cast<DWORD>(-1))
		{
			Log("Failed to resume process (%lu)\n", GetLastError());
			TerminateProcess(pi.hProcess, static_cast<UINT>(-1));
			CloseHandle(pi.hThread);
			CloseHandle(pi.hProcess);
			CloseHandle(connectedEvent);
			return -1;
		}
	}

	Log("Successfully injected!\n");

	if (!appKey.empty() && identifyOpenVRApplication)
	{
		vr::EVRInitError err;
		vr::VR_Init(&err, vr::VRApplication_Utility);
		if (err == vr::VRInitError_None)
		{
			if (vr::VRApplications()->IdentifyApplication(pi.dwProcessId, appKey.c_str()) == vr::VRApplicationError_None)
				Log("Identified application as: %s\n", appKey.c_str());
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
