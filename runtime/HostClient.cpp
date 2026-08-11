#include "HostClient.h"

#include <winsock2.h>
#include <ws2tcpip.h>

#include <windows.h>

#include <cstdlib>
#include <mutex>
#include <string>

namespace
{
constexpr int ProtocolVersion = 2;

bool Connect()
{
	const char* endpoint = std::getenv("RIFTLIFT_RUNTIME_ENDPOINT");
	const char* token = std::getenv("RIFTLIFT_RUNTIME_TOKEN");
	const char* protocol = std::getenv("RIFTLIFT_RUNTIME_PROTOCOL");
	if (!endpoint || !token || !protocol || std::atoi(protocol) != ProtocolVersion)
		return false;

	const std::string address(endpoint);
	const size_t separator = address.rfind(':');
	if (separator == std::string::npos || address.substr(0, separator) != "127.0.0.1")
		return false;
	const int port = std::atoi(address.substr(separator + 1).c_str());
	if (port <= 0 || port > 65535)
		return false;

	WSADATA data{};
	if (WSAStartup(MAKEWORD(2, 2), &data) != 0)
		return false;

	SOCKET connection = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
	if (connection == INVALID_SOCKET)
	{
		WSACleanup();
		return false;
	}

	sockaddr_in host{};
	host.sin_family = AF_INET;
	host.sin_port = htons(static_cast<u_short>(port));
	InetPtonA(AF_INET, "127.0.0.1", &host.sin_addr);
	bool connected = connect(connection, reinterpret_cast<sockaddr*>(&host), sizeof(host)) == 0;
	if (connected)
	{
		const std::string request = std::string("PING ") + token + "\n";
		connected = send(connection, request.data(), static_cast<int>(request.size()), 0) ==
			static_cast<int>(request.size());
		char response[64]{};
		if (connected)
		{
			const int received = recv(connection, response, sizeof(response) - 1, 0);
			connected = received > 0 &&
				std::string(response, static_cast<size_t>(received)).rfind("OK 2 ", 0) == 0;
		}
	}
	closesocket(connection);
	WSACleanup();
	return connected;
}
}

bool RiftLiftConnectNativeHost()
{
	static std::once_flag once;
	static bool connected = false;
	std::call_once(once, [] { connected = Connect(); });
	if (!connected)
		OutputDebugStringA("RiftLift: native runtime handshake failed\n");
	return connected;
}
