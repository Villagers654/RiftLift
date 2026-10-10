#pragma once

#include <string>

// The CRT's argv parser removes quotes, but many games read GetCommandLineW()
// and compare raw tokens, so a quoted "-level" no longer matches. Quote only
// arguments that would otherwise split, vanish or lose a quote character.
inline bool ArgumentNeedsQuotes(const wchar_t* argument)
{
	if (!*argument)
		return true;
	for (const wchar_t* current = argument; *current; ++current)
	{
		switch (*current)
		{
		case L' ':
		case L'\t':
		case L'\n':
		case L'\v':
		case L'\"':
			return true;
		}
	}
	return false;
}

inline std::wstring QuoteArgument(const wchar_t* argument)
{
	if (!ArgumentNeedsQuotes(argument))
		return argument;
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

inline std::wstring BuildCommandLine(int argc, const wchar_t* const argv[], int firstArgument)
{
	std::wstring result;
	for (int index = firstArgument; index < argc; ++index)
	{
		if (index > firstArgument)
			result.push_back(L' ');
		result += QuoteArgument(argv[index]);
	}
	return result;
}
