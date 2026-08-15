#pragma once

#include "microprofile.h"

// Debug launches record each Oculus entry point once. This is intentionally
// bounded: frame-loop APIs do not append a line on every frame.
void TraceOculusCall(const char* name);
void TraceOculusValue(const char* name, long long value);
bool RunningUnderWine();
#define REV_TRACE(x) MICROPROFILE_SCOPEI("RiftLift", #x, 0xff0000); TraceOculusCall(#x);

extern unsigned int g_MinorVersion;

#if MICROPROFILE_ENABLED
extern class ProfileManager g_ProfileManager;
#endif
