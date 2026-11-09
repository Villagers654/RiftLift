#include "../riftlift-platform-shim.c"
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "Failed line %d: %s\n", __LINE__, #x); exit(1); } } while (0)

/* A stand-in for riftlift-runtime's relay: one JSON line in, one out. */
static SOCKET relay;
static volatile LONG entitlement_requests;
static volatile LONG bad_requests;
static DWORD WINAPI serve_relay(LPVOID parameter)
{
    (void)parameter;
    for (;;) {
        char request[1024] = {0}, op[32] = {0}, secret[96] = {0}, app[32] = {0};
        const char *reply = "{\"error\":-20000}\n";
        int length = 0, n;
        SOCKET client = accept(relay, NULL, NULL);
        if (client == INVALID_SOCKET) return 0;
        while (length < (int)sizeof(request) - 1 && !strchr(request, '\n') &&
               (n = recv(client, request + length, (int)sizeof(request) - 1 - length, 0)) > 0)
            length += n;
        if (!json_get(request, "secret", secret, sizeof(secret)) || strcmp(secret, "00ff") ||
            !json_get(request, "appId", app, sizeof(app)) || strcmp(app, "1477883658957255"))
            InterlockedIncrement(&bad_requests);
        json_get(request, "op", op, sizeof(op));
        if (!strcmp(op, "credentials"))
            reply = "{\"alias\":\"tester \\\"one\\\" \\u00e9\",\"appToken\":\"app-scoped-token\",\"displayName\":\"Test User\","
                    "\"error\":0,\"nested\":{\"x\":[1,\"}\"]},\"userId\":\"1234\"}\n";
        else if (!strcmp(op, "entitlement")) {
            /* Meta answers the first check slower than a game's 1 s timer. */
            LONG n = InterlockedIncrement(&entitlement_requests);
            if (n == 1) Sleep(1500);
            reply = n == 1 ? "{\"error\":0}\n" : "{\"error\":-20002}\n";
        }
        else if (!strcmp(op, "user_proof"))
            reply = "{\"error\":0,\"nonce\":\"proof-nonce\"}\n";
        else if (!strcmp(op, "org_scoped_id"))
            reply = strstr(request, "\"userId\":\"1234\"") ? "{\"error\":0,\"orgScopedId\":\"777\"}\n" : "{\"error\":-20009}\n";
        send(client, reply, (int)strlen(reply), 0);
        closesocket(client);
    }
}

static void *pop_reply(uint64_t request)
{
    for (int i = 0; i < 500; ++i) {
        void *message = ovr_PopMessage();
        if (message) {
            CHECK(ovr_Message_GetRequestID(message) == request);
            return message;
        }
        Sleep(10);
    }
    CHECK(!"no reply");
    return NULL;
}

int main(void) {
    /* No relay (no launcher broker): requests fail as unavailable, never as a made-up success. */
    _putenv("RIFTLIFT_PLATFORM_BROKER=");
    CHECK(ovr_PlatformInitializeWindows("1477883658957255") == 0);
    uint64_t request = ovr_Entitlement_GetIsViewerEntitled();
    void *message = pop_reply(request);
    CHECK(ovr_Message_GetType(message) == MSG_ENTITLEMENT);
    CHECK(ovr_Message_IsError(message));
    CHECK(ovr_Error_GetCode(ovr_Message_GetError(message)) == ERROR_UNAVAILABLE);
    CHECK(strstr(ovr_Error_GetMessage(ovr_Message_GetError(message)), "unavailable"));
    ovr_FreeMessage(message);
    CHECK(!ovr_IsEntitled());
    CHECK(ovr_GetLoggedInUserID() == 0);
    request = ovr_User_GetLoggedInUser();
    message = pop_reply(request);
    CHECK(ovr_Message_IsError(message));
    CHECK(ovr_Message_GetUser(message) == NULL);
    ovr_FreeMessage(message);

    WSADATA data;
    CHECK(WSAStartup(MAKEWORD(2, 2), &data) == 0);
    relay = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    struct sockaddr_in address = {0};
    int size = sizeof(address);
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    CHECK(bind(relay, (struct sockaddr *)&address, sizeof(address)) == 0 && listen(relay, 8) == 0);
    CHECK(getsockname(relay, (struct sockaddr *)&address, &size) == 0);
    char endpoint[96];
    snprintf(endpoint, sizeof(endpoint), "RIFTLIFT_PLATFORM_BROKER=127.0.0.1:%u 00ff", ntohs(address.sin_port));
    _putenv(endpoint);
    CHECK(CreateThread(NULL, 0, serve_relay, NULL, 0, NULL));

    CHECK(ovr_PlatformInitializeWindows("1477883658957255") == 0);
    CHECK(ovr_PlatformInitializeUnrealWindows("1477883658957255") == 0);
    CHECK(ovr_PlatformInitializeUnityWindows("1477883658957255") == 0);
    CHECK(ovr_UnityInitWrapperWindows("1477883658957255", NULL));
    request = ovr_UnityInitWrapperWindowsAsynchronous("1477883658957255");
    message = pop_reply(request);
    CHECK(ovr_Message_GetType(message) == MSG_PLATFORM_INITIALIZE_WINDOWS_ASYNC);
    CHECK(!ovr_Message_IsError(message));
    CHECK(ovr_PlatformInitialize_GetResult(ovr_Message_GetPlatformInitialize(message)) == PLATFORM_INITIALIZE_SUCCESS);
    ovr_FreeMessage(message);
    request = ovr_PlatformInitializeWindowsAsynchronous("1477883658957255");
    message = pop_reply(request);
    CHECK(ovr_Message_GetType(message) == MSG_PLATFORM_INITIALIZE_WINDOWS_ASYNC);
    ovr_FreeMessage(message);

    /* The identity Meta issued for the declared app. */
    CHECK(ovr_GetLoggedInUserID() == 1234);
    request = ovr_User_GetLoggedInUser();
    message = pop_reply(request);
    CHECK(ovr_Message_GetNativeMessage(message) == message);
    CHECK(ovr_Message_GetType(message) == MSG_LOGGED_IN_USER);
    CHECK(!ovr_Message_IsError(message));
    CHECK(ovr_Message_GetError(message) == NULL);
    const void *user = ovr_Message_GetUser(message);
    ovr_FreeMessage(message);
    CHECK(ovr_User_GetID(user) == 1234);
    CHECK(!strcmp(ovr_User_GetOculusID(user), "tester \"one\" \xc3\xa9"));
    CHECK(!strcmp(ovr_User_GetDisplayName(user), "Test User"));
    CHECK(!strcmp(ovr_User_GetImageUrl(user), ""));
    CHECK(!strcmp(ovr_User_GetPresence(user), ""));
    CHECK(ovr_User_GetPresenceStatus(user) == 0);
    request = ovr_User_Get(1234);
    message = pop_reply(request);
    CHECK(ovr_Message_GetType(message) == MSG_USER);
    CHECK(ovr_Message_GetUser(message) == user);
    CHECK(ovr_Message_GetString(message) == NULL);
    ovr_FreeMessage(message);
    CHECK(ovr_User_Get(4321) == 4421);
    request = ovr_User_GetAccessToken();
    message = pop_reply(request);
    CHECK(ovr_Message_GetType(message) == MSG_ACCESS_TOKEN);
    CHECK(!strcmp(ovr_Message_GetString(message), "app-scoped-token"));
    ovr_FreeMessage(message);
    request = ovr_User_GetUserProof();
    message = pop_reply(request);
    CHECK(!strcmp(ovr_UserProof_GetNonce(ovr_Message_GetUserProof(message)), "proof-nonce"));
    ovr_FreeMessage(message);
    request = ovr_User_GetOrgScopedID(1234);
    message = pop_reply(request);
    CHECK(ovr_OrgScopedID_GetID(ovr_Message_GetOrgScopedID(message)) == 777);
    ovr_FreeMessage(message);

    /* Entitlement is asked only when the game asks, and every time it does. */
    CHECK(entitlement_requests == 0);
    /* Lone Echo's pnsovr.dll CheckEntitlement(1000): ask, then start the
     * clock and poll for 1000 ms. Meta's slow first answer is already queued. */
    request = ovr_Entitlement_GetIsViewerEntitled();
    {
        LARGE_INTEGER frequency, start, now;
        QueryPerformanceFrequency(&frequency);
        QueryPerformanceCounter(&start);
        message = NULL;
        do {
            message = ovr_PopMessage();
            QueryPerformanceCounter(&now);
        } while (!message && (now.QuadPart - start.QuadPart) * 1000 / frequency.QuadPart <= 1000);
    }
    CHECK(message);
    CHECK(ovr_Message_GetRequestID(message) == request);
    CHECK(ovr_Message_GetType(message) == MSG_ENTITLEMENT);
    CHECK(!ovr_Message_IsError(message));
    ovr_FreeMessage(message);
    request = ovr_Entitlement_GetIsViewerEntitled();
    message = pop_reply(request);
    CHECK(ovr_Message_IsError(message));
    CHECK(ovr_Error_GetCode(ovr_Message_GetError(message)) == -20002);
    CHECK(strstr(ovr_Error_GetDisplayableMessage(ovr_Message_GetError(message)), "does not own"));
    ovr_FreeMessage(message);
    CHECK(!ovr_IsEntitled());
    CHECK(entitlement_requests == 3);
    CHECK(bad_requests == 0);

    request = ovr_AssetFile_GetList();
    message = pop_reply(request);
    CHECK(ovr_Message_GetType(message) == MSG_ASSET_LIST);
    CHECK(ovr_AssetDetailsArray_GetSize(ovr_Message_GetAssetDetailsArray(message)) == 0);
    ovr_FreeMessage(message);
    CHECK(ovr_Achievements_Unlock("first") == 81);
    _putenv("RIFTLIFT_PLATFORM_OFFLINE=1");
    request = ovr_Achievements_Unlock("first");
    message = pop_reply(request);
    CHECK(ovr_Message_GetType(message) == MSG_ACHIEVEMENT_UNLOCK);
    CHECK(!ovr_Message_IsError(message));
    /* Games read the unlock result: Meta's getters must never see the local reply. */
    CHECK(ovr_AchievementUpdate_GetJustUnlocked(ovr_Message_GetAchievementUpdate(message)));
    CHECK(strcmp(ovr_AchievementUpdate_GetName(ovr_Message_GetAchievementUpdate(message)), "first") == 0);
    ovr_FreeMessage(message);
    _putenv("RIFTLIFT_PLATFORM_OFFLINE=");
    /* Meta's implementation was never initialized: an empty queue must not reach it. */
    CHECK(ovr_PopMessage() == NULL);
    typedef void (*setter)(uint64_t);
    typedef void (*flag_setter)(bool);
    union { FARPROC source; setter target; } set = {real_proc("test_set_user")};
    union { FARPROC source; flag_setter target; } set_initialized = {real_proc("test_set_initialized")};
    CHECK(set.target && set_initialized.target);
    set.target(5678);
    request = ovr_AssetFile_GetList();
    message = pop_reply(request);
    CHECK(ovr_Message_GetType(message) == MSG_ASSET_LIST);
    ovr_FreeMessage(message);
    set_initialized.target(true);
    CHECK(ovr_AssetFile_GetList() == 71);
    void *real_message = ovr_PopMessage();
    CHECK(real_message && ovr_Message_GetNativeMessage(real_message) == real_message);
    uint64_t real_object = 0;
    CHECK(ovr_Message_GetNativeMessage(&real_object) == &real_object);
    CHECK(ovr_AssetDetailsArray_GetSize(&real_object) == 3);
    CHECK(!strcmp(ovr_User_GetDisplayName(&real_object), "Real user"));
    CHECK(ovr_User_GetPresenceStatus(&real_object) == 2);
    puts("Platform initialization, brokered identity and entitlement, errors without a broker, local asset enumeration, achievement unlocks, uninitialized polling and native forwarding tests passed");
    return 0;
}
