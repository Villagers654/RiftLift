#include "Common.h"

#include <Windows.h>

#include <cstdio>
#include <cstdlib>
#include <mutex>
#include <string>
#include <unordered_set>

namespace
{
bool RuntimeTraceEnabled()
{
	static const bool enabled = std::getenv("RIFTLIFT_RUNTIME_TRACE") != nullptr;
	return enabled;
}

bool OpenTraceFile(FILE** stream)
{
	char temp[MAX_PATH];
	char path[MAX_PATH];
	if (!GetTempPathA(static_cast<DWORD>(sizeof(temp)), temp) ||
		sprintf_s(path, sizeof(path), "%sriftlift-runtime-trace.log", temp) < 0)
		return false;
	return fopen_s(stream, path, "a") == 0 && *stream;
}
}

void TraceOculusCall(const char* name)
{
	if (!RuntimeTraceEnabled())
		return;

	static std::mutex lock;
	static std::unordered_set<std::string> seen;
	std::lock_guard<std::mutex> guard(lock);
	if (!seen.emplace(name).second)
		return;

	FILE* stream = nullptr;
	if (OpenTraceFile(&stream))
	{
		fprintf(stream, "%lu %s\n", GetCurrentProcessId(), name);
		fclose(stream);
	}
}

void TraceOculusValue(const char* name, long long value)
{
	if (!RuntimeTraceEnabled())
		return;

	FILE* stream = nullptr;
	if (OpenTraceFile(&stream))
	{
		fprintf(stream, "%lu %s %lld\n", GetCurrentProcessId(), name, value);
		fclose(stream);
	}
}
