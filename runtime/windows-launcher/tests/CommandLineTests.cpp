#include <cstdio>
#include <string>
#include <vector>

#include <Windows.h>
#include <shellapi.h>

#include "../CommandLine.h"

static int failures = 0;

static std::string Narrow(const std::wstring& value)
{
	return std::string(value.begin(), value.end());
}

// Builds the command line the launcher passes to the game and checks both the
// exact text games read from GetCommandLineW() and that the CRT parser
// recovers every argument unchanged.
static void Check(const std::vector<const wchar_t*>& arguments, const wchar_t* expected)
{
	const std::wstring commandLine = BuildCommandLine(
		static_cast<int>(arguments.size()), arguments.data(), 0);
	if (commandLine != expected)
	{
		++failures;
		std::printf("FAIL: built  %s\n      wanted %s\n",
			Narrow(commandLine).c_str(), Narrow(expected).c_str());
	}

	// CommandLineToArgvW treats the program name specially, so parse the
	// arguments behind a fixed one.
	const std::wstring parsedLine = L"game.exe " + BuildCommandLine(
		static_cast<int>(arguments.size()), arguments.data(), 1);
	int count = 0;
	LPWSTR* parsed = CommandLineToArgvW(parsedLine.c_str(), &count);
	if (!parsed || count != static_cast<int>(arguments.size()))
	{
		++failures;
		std::printf("FAIL: %s parsed into %d arguments\n", Narrow(parsedLine).c_str(), count);
	}
	else
	{
		for (int index = 1; index < count; ++index)
		{
			if (std::wstring(parsed[index]) != arguments[index])
			{
				++failures;
				std::printf("FAIL: argument %d of %s parsed as %s\n", index,
					Narrow(parsedLine).c_str(), Narrow(parsed[index]).c_str());
			}
		}
	}
	LocalFree(parsed);
}

int main()
{
	Check({L"C:\\Users\\player\\AppData\\Local\\RiftLift\\games\\lone-echo\\bin\\win7\\loneecho.exe",
		L"-level", L"gpr_020_post_tutorial", L"-checkpoint", L"checkpoint_gpr_020"},
		L"C:\\Users\\player\\AppData\\Local\\RiftLift\\games\\lone-echo\\bin\\win7\\loneecho.exe"
		L" -level gpr_020_post_tutorial -checkpoint checkpoint_gpr_020");
	Check({L"C:\\Program Files\\Game\\Game.exe", L"-vr", L"-name=Two Words"},
		L"\"C:\\Program Files\\Game\\Game.exe\" -vr \"-name=Two Words\"");
	Check({L"game.exe", L"Z:\\games\\data\\", L"-log=C:\\logs\\run.txt"},
		L"game.exe Z:\\games\\data\\ -log=C:\\logs\\run.txt");
	Check({L"game.exe", L"C:\\Folder With Space\\"},
		L"game.exe \"C:\\Folder With Space\\\\\"");
	Check({L"game.exe", L""}, L"game.exe \"\"");
	Check({L"game.exe", L"say\"hi\"", L"tab\there"},
		L"game.exe \"say\\\"hi\\\"\" \"tab\there\"");
	Check({L"game.exe", L"a\\\"b"}, L"game.exe \"a\\\\\\\"b\"");
	if (failures)
		return 1;
	std::puts("Launcher command lines quote only arguments that need it");
	return 0;
}
