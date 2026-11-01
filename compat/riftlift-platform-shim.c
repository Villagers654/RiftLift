/*
 * RiftLift / Wine compatibility shim for the legacy Oculus Platform SDK.
 *
 * The current Meta runtime authenticates in its Electron client under Wine, but
 * OAF never publishes that session to legacy PC SDK clients. Some applications
 * use the engine-specific initializer while others call the generic Windows
 * initializer, then block before rendering while the legacy SDK waits for that
 * unavailable session. This shim initializes locally and answers the
 * account-bound requests itself, forwarding every other export to the original
 * Meta implementation.
 *
 * The answers come from Meta, through the launcher that holds the account
 * (src/common/meta/platform.h) and riftlift-runtime's loopback relay
 * (RIFTLIFT_PLATFORM_BROKER). Meta is asked only when the game asks: its
 * entitlement check is a live query each time, and nothing here assumes an
 * owned copy or invents an identity. Without an account the game still starts,
 * and these requests complete with the error a signed-out Platform gives.
 */
#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <windows.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef _MSC_VER
#pragma comment(lib, "ws2_32.lib")
#endif

#define MSG_ENTITLEMENT UINT32_C(0x186B58B1)
#define MSG_LOGGED_IN_USER UINT32_C(0x436F345D)
#define MSG_USER UINT32_C(0x6BCF9E47)
#define MSG_ASSET_LIST UINT32_C(0x4AFC6F74)
#define MSG_ACCESS_TOKEN UINT32_C(0x06A85ABE)
#define MSG_ORG_SCOPED_ID UINT32_C(0x18F0B01B)
#define MSG_USER_PROOF UINT32_C(0x22810483)
#define MSG_ACHIEVEMENT_DEFINITIONS UINT32_C(0x03D3458D)
#define MSG_ACHIEVEMENT_PROGRESS UINT32_C(0x4F9FDE1D)
#define MSG_ACHIEVEMENT_UNLOCK UINT32_C(0x593CCBDD)
#define MSG_CLOUD_BUCKET_METADATA UINT32_C(0x7327A50D)
#define MSG_LOGGED_IN_USER_FRIENDS UINT32_C(0x587C2A8D)
#define MSG_PLATFORM_INITIALIZE_WINDOWS_ASYNC UINT32_C(0x6DA7BA8F)
#define PLATFORM_INITIALIZE_SUCCESS 0
#define FAKE_MAGIC UINT64_C(0x56414445524f5652)

/* The launcher's Platform errors (common/meta/platform.h, Venture's range). */
#define ERROR_UNAUTHENTICATED (-20001)
#define ERROR_UNAVAILABLE (-20003)
#define ERROR_INVALID_RESPONSE (-20004)

typedef struct FakeMessage {
    uint64_t magic;
    uint64_t request_id;
    uint32_t type;
    int error;          /* 0, or the launcher's Platform error */
    char name[128];     /* the achievement an unlock reply is for */
    char *text;         /* an access token or user proof nonce */
    uint64_t id;        /* an org-scoped ID */
} FakeMessage;

/* Payloads can outlive the response message that delivered them. Keep the
 * small identity objects stable for the lifetime of the process instead of
 * returning a pointer into a message that the application will free. */
static const FakeMessage identity_user = {FAKE_MAGIC, 0, MSG_LOGGED_IN_USER, 0, {0}, NULL, 0};

static SRWLOCK queue_lock = SRWLOCK_INIT;
static FakeMessage *queue[64];
static unsigned queue_head;
static unsigned queue_tail;
static volatile LONG64 next_request = 1000;
static volatile LONG empty_poll_count;

/* The app ID the game initialized with: Meta's answers are for it. */
static SRWLOCK app_lock = SRWLOCK_INIT;
static char declared_app[32];

/* The game's app-scoped identity, once Meta has issued it. */
static SRWLOCK identity_lock = SRWLOCK_INIT;
static bool identity_loaded;
static uint64_t identity_id;
static char identity_alias[256];
static char identity_name[256];
static DWORD identity_retry_at; /* the synchronous getter's next attempt after a failure */
static SRWLOCK fetch_lock = SRWLOCK_INIT; /* one identity request at a time */

static bool offline_compat(void)
{
    const char *value = getenv("RIFTLIFT_PLATFORM_OFFLINE");
    return value && *value && strcmp(value, "0") != 0;
}

static FARPROC real_proc(const char *name)
{
    HMODULE module = GetModuleHandleA("LibOVRPlatformImpl64_1_real.dll");
    if (!module) {
        module = LoadLibraryA("LibOVRPlatformImpl64_1_real.dll");
    }
    return module ? GetProcAddress(module, name) : NULL;
}

/* The local initializers above never start Meta's implementation, and its
 * context-bound calls (ovr_PopMessage, ovr_GetLoggedInUserID, ...) throw a C++
 * exception without that context. Its own ovr_IsPlatformInitialized only reads
 * the context, so ask it before handing it any such call. */
static bool real_platform_initialized(void)
{
    typedef bool(__cdecl *function_type)(void);
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_IsPlatformInitialized")};
    return convert.target && convert.target();
}

static void log_call(const char *name)
{
    char temp[MAX_PATH];
    char path[MAX_PATH];
    FILE *stream;

    if (!GetTempPathA((DWORD)sizeof(temp), temp)) {
        return;
    }
    if (snprintf(path, sizeof(path), "%sriftlift-platform-shim.log", temp) < 0) {
        return;
    }
    stream = fopen(path, "a");
    if (stream) {
        fprintf(stream, "%lu %s\n", GetTickCount(), name);
        fclose(stream);
    }
}

static void log_result(const char *what, int error)
{
    char line[160];
    snprintf(line, sizeof(line), "%s: %s (%d)", what, error ? "error" : "success", error);
    log_call(line);
}

static void push(FakeMessage *message)
{
    unsigned next;
    AcquireSRWLockExclusive(&queue_lock);
    next = (queue_tail + 1) % (sizeof(queue) / sizeof(queue[0]));
    if (next == queue_head) {
        free(queue[queue_head]->text);
        free(queue[queue_head]);
        queue_head = (queue_head + 1) % (sizeof(queue) / sizeof(queue[0]));
    }
    queue[queue_tail] = message;
    queue_tail = next;
    ReleaseSRWLockExclusive(&queue_lock);
}

static FakeMessage *new_message(uint32_t type)
{
    FakeMessage *message = (FakeMessage *)calloc(1, sizeof(*message));
    if (message) {
        message->magic = FAKE_MAGIC;
        message->request_id = (uint64_t)InterlockedIncrement64(&next_request);
        message->type = type;
    }
    return message;
}

static uint64_t enqueue_named(uint32_t type, const char *name)
{
    FakeMessage *message = new_message(type);
    if (!message) {
        return 0;
    }
    if (name) {
        strncpy(message->name, name, sizeof(message->name) - 1);
    }
    push(message);
    return message->request_id;
}

static uint64_t enqueue(uint32_t type)
{
    return enqueue_named(type, NULL);
}

/* ---- The launcher's Platform broker, through the runtime's relay ---- */

/* One value of a flat JSON object (the relay's replies): a string, decoded,
 * or a number's text. Nested values are skipped. */
static const char *json_skip_space(const char *p)
{
    while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') ++p;
    return p;
}

static void put_utf8(char *out, size_t size, size_t *length, unsigned long code)
{
    unsigned char bytes[4];
    size_t count, i;
    if (code < 0x80) { bytes[0] = (unsigned char)code; count = 1; }
    else if (code < 0x800) { bytes[0] = (unsigned char)(0xC0 | (code >> 6)); bytes[1] = (unsigned char)(0x80 | (code & 0x3F)); count = 2; }
    else if (code < 0x10000) { bytes[0] = (unsigned char)(0xE0 | (code >> 12)); bytes[1] = (unsigned char)(0x80 | ((code >> 6) & 0x3F)); bytes[2] = (unsigned char)(0x80 | (code & 0x3F)); count = 3; }
    else { bytes[0] = (unsigned char)(0xF0 | (code >> 18)); bytes[1] = (unsigned char)(0x80 | ((code >> 12) & 0x3F)); bytes[2] = (unsigned char)(0x80 | ((code >> 6) & 0x3F)); bytes[3] = (unsigned char)(0x80 | (code & 0x3F)); count = 4; }
    for (i = 0; i < count; ++i) {
        if (out && *length + 1 < size) out[*length] = (char)bytes[i];
        ++*length;
    }
}

static unsigned long hex4(const char *p)
{
    char digits[5];
    char *end;
    unsigned long value;
    memcpy(digits, p, 4);
    digits[4] = '\0';
    value = strtoul(digits, &end, 16);
    return end == digits + 4 ? value : 0xFFFFFFFFul;
}

/* Reads the string at p (its opening quote) into out; returns the text after
 * it, or NULL when it is malformed. *length is the decoded size. */
static const char *json_string(const char *p, char *out, size_t size, size_t *length)
{
    *length = 0;
    if (*p++ != '"') return NULL;
    while (*p && *p != '"') {
        unsigned long code;
        if (*p != '\\') {
            if (out && *length + 1 < size) out[*length] = *p;
            ++*length;
            ++p;
            continue;
        }
        ++p;
        switch (*p) {
        case '"': case '\\': case '/': code = (unsigned char)*p; ++p; break;
        case 'b': code = '\b'; ++p; break;
        case 'f': code = '\f'; ++p; break;
        case 'n': code = '\n'; ++p; break;
        case 'r': code = '\r'; ++p; break;
        case 't': code = '\t'; ++p; break;
        case 'u':
            code = hex4(p + 1);
            if (code == 0xFFFFFFFFul) return NULL;
            p += 5;
            if (code >= 0xD800 && code < 0xDC00 && p[0] == '\\' && p[1] == 'u') {
                unsigned long low = hex4(p + 2);
                if (low >= 0xDC00 && low < 0xE000) {
                    code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00);
                    p += 6;
                }
            }
            break;
        default: return NULL;
        }
        put_utf8(out, size, length, code);
    }
    if (out && size) out[*length < size ? *length : size - 1] = '\0';
    return *p == '"' ? p + 1 : NULL;
}

static const char *json_skip_value(const char *p)
{
    size_t length;
    int depth = 0;
    p = json_skip_space(p);
    do {
        if (*p == '"') {
            p = json_string(p, NULL, 0, &length);
            if (!p) return NULL;
            continue;
        }
        if (*p == '{' || *p == '[') ++depth;
        else if (*p == '}' || *p == ']') {
            if (!depth) return p;
            --depth;
        } else if (!*p) return NULL;
        else if (!depth && *p == ',') return p;
        ++p;
    } while (depth || (*p && *p != ',' && *p != '}'));
    return p;
}

/* The value of `key` in a flat object, as text: false when it is missing.
 * A string longer than `size` is refused rather than cut. */
static bool json_get(const char *json, const char *key, char *out, size_t size)
{
    const char *p = json_skip_space(json);
    char name[64];
    size_t length;
    if (*p++ != '{') return false;
    for (;;) {
        p = json_skip_space(p);
        if (*p != '"') return false;
        p = json_string(p, name, sizeof(name), &length);
        if (!p) return false;
        p = json_skip_space(p);
        if (*p++ != ':') return false;
        p = json_skip_space(p);
        if (length < sizeof(name) && !strcmp(name, key)) {
            if (*p == '"') return json_string(p, out, size, &length) && length < size;
            length = 0;
            while ((*p == '-' || (*p >= '0' && *p <= '9')) && length + 1 < size) out[length++] = *p++;
            out[length] = '\0';
            return length > 0;
        }
        p = json_skip_value(p);
        if (!p) return false;
        p = json_skip_space(p);
        if (*p == ',') { ++p; continue; }
        return false;
    }
}

static int reply_error(const char *reply)
{
    char text[16];
    return json_get(reply, "error", text, sizeof(text)) ? atoi(text) : ERROR_INVALID_RESPONSE;
}

static uint64_t parse_id(const char *text)
{
    char *end = NULL;
    unsigned long long value = strtoull(text, &end, 10);
    return end && end != text && *end == '\0' ? (uint64_t)value : 0;
}

static bool numeric(const char *text, size_t maximum)
{
    size_t length = strlen(text);
    return length && length <= maximum && strspn(text, "0123456789") == length;
}

static INIT_ONCE winsock_once = INIT_ONCE_STATIC_INIT;
static BOOL CALLBACK start_winsock(PINIT_ONCE once, PVOID parameter, PVOID *context)
{
    WSADATA data;
    (void)once, (void)parameter, (void)context;
    return WSAStartup(MAKEWORD(2, 2), &data) == 0;
}

/* Asks the launcher: one request per connection, a JSON line each way.
 * `reply` receives the launcher's answer; false when the relay is unreachable. */
static bool broker_call(const char *op, uint64_t user, char *reply, size_t size)
{
    const char *endpoint = getenv("RIFTLIFT_PLATFORM_BROKER");
    char host[64], secret[96], request[512], app[32];
    unsigned port;
    struct sockaddr_in address;
    SOCKET s;
    DWORD timeout = 120000;
    size_t received = 0;
    int length;

    if (!endpoint || sscanf(endpoint, "%63[0-9.]:%u %95[0-9a-f]", host, &port, secret) != 3 || !port || port > 65535) {
        return false;
    }
    if (!InitOnceExecuteOnce(&winsock_once, start_winsock, NULL, NULL)) {
        return false;
    }
    AcquireSRWLockShared(&app_lock);
    memcpy(app, declared_app, sizeof(app));
    ReleaseSRWLockShared(&app_lock);
    length = snprintf(request, sizeof(request), "{\"secret\":\"%s\",\"op\":\"%s\"", secret, op);
    if (*app) length += snprintf(request + length, sizeof(request) - (size_t)length, ",\"appId\":\"%s\"", app);
    if (user) length += snprintf(request + length, sizeof(request) - (size_t)length, ",\"userId\":\"%llu\"", (unsigned long long)user);
    length += snprintf(request + length, sizeof(request) - (size_t)length, "}\n");
    if (length <= 0 || (size_t)length >= sizeof(request)) {
        return false;
    }

    s = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (s == INVALID_SOCKET) {
        return false;
    }
    memset(&address, 0, sizeof(address));
    address.sin_family = AF_INET;
    address.sin_port = htons((u_short)port);
    address.sin_addr.s_addr = inet_addr(host);
    /* Meta's entitlement read retries for a few seconds; never wait forever. */
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, (const char *)&timeout, sizeof(timeout));
    if (connect(s, (struct sockaddr *)&address, sizeof(address)) != 0 || send(s, request, length, 0) != length) {
        closesocket(s);
        return false;
    }
    while (received + 1 < size) {
        int n = recv(s, reply + received, (int)(size - 1 - received), 0);
        if (n <= 0) break;
        received += (size_t)n;
        if (memchr(reply + received - (size_t)n, '\n', (size_t)n)) break;
    }
    closesocket(s);
    reply[received] = '\0';
    return received && strchr(reply, '\n');
}

/* The launcher's answer for `op`, or the relay's absence as unavailable. */
static int ask(const char *op, uint64_t user, char *reply, size_t size)
{
    if (!broker_call(op, user, reply, size)) {
        return ERROR_UNAVAILABLE;
    }
    return reply_error(reply);
}

/* Loads the identity (asking the launcher, which asks Meta the first time) and
 * optionally a copy of the app-scoped access token. Holds fetch_lock. */
static int load_identity_locked(char **token)
{
    char reply[64 * 1024], id[32], alias[sizeof(identity_alias)], name[sizeof(identity_name)];
    char *access = NULL;
    int error = ask("credentials", 0, reply, sizeof(reply));
    if (!error) {
        size_t limit = strlen(reply) + 1;
        access = (char *)malloc(limit);
        if (!json_get(reply, "userId", id, sizeof(id)) || !parse_id(id) ||
            !access || !json_get(reply, "appToken", access, limit) || !*access) {
            error = ERROR_INVALID_RESPONSE;
        }
        if (!json_get(reply, "alias", alias, sizeof(alias))) *alias = '\0';
        if (!json_get(reply, "displayName", name, sizeof(name))) *name = '\0';
    }
    AcquireSRWLockExclusive(&identity_lock);
    if (!error) {
        identity_id = parse_id(id);
        memcpy(identity_alias, alias, sizeof(alias));
        memcpy(identity_name, *name ? name : alias, sizeof(name));
        identity_loaded = true;
    } else {
        identity_retry_at = GetTickCount() + 30000;
    }
    ReleaseSRWLockExclusive(&identity_lock);
    log_result("identity from Meta", error);
    if (!error && token) {
        *token = access;
    } else {
        free(access);
    }
    return error;
}

static int load_identity(char **token)
{
    int error;
    AcquireSRWLockExclusive(&fetch_lock);
    error = load_identity_locked(token);
    ReleaseSRWLockExclusive(&fetch_lock);
    return error;
}

static bool have_identity(void)
{
    bool loaded;
    AcquireSRWLockShared(&identity_lock);
    loaded = identity_loaded;
    ReleaseSRWLockShared(&identity_lock);
    return loaded;
}

static void record_app_id(const char *id)
{
    if (!id || !numeric(id, 20)) {
        return;
    }
    AcquireSRWLockExclusive(&app_lock);
    strcpy(declared_app, id);
    ReleaseSRWLockExclusive(&app_lock);
}

typedef struct Job {
    FakeMessage *message;
    char op[32];
    uint64_t user;
} Job;

/* Completes one queued request with the launcher's answer. */
static DWORD WINAPI run_job(LPVOID parameter)
{
    Job *job = (Job *)parameter;
    FakeMessage *message = job->message;
    char reply[4096], value[1024];

    if (!strcmp(job->op, "credentials")) {
        message->error = load_identity(message->type == MSG_ACCESS_TOKEN ? &message->text : NULL);
        if (!message->error && message->type == MSG_USER) {
            AcquireSRWLockShared(&identity_lock);
            if (identity_id != job->user) message->error = ERROR_INVALID_RESPONSE;
            ReleaseSRWLockShared(&identity_lock);
        }
    } else {
        message->error = ask(job->op, job->user, reply, sizeof(reply));
        if (!message->error && !strcmp(job->op, "user_proof")) {
            if (json_get(reply, "nonce", value, sizeof(value)) && *value) {
                message->text = _strdup(value);
            }
            if (!message->text) message->error = ERROR_INVALID_RESPONSE;
        } else if (!message->error && !strcmp(job->op, "org_scoped_id")) {
            message->id = json_get(reply, "orgScopedId", value, sizeof(value)) ? parse_id(value) : 0;
            if (!message->id) message->error = ERROR_INVALID_RESPONSE;
        }
    }
    log_result(job->op, message->error);
    free(job);
    push(message);
    return 0;
}

/* Queues `op` for the launcher; its reply arrives through ovr_PopMessage. */
static uint64_t request(uint32_t type, const char *op, uint64_t user)
{
    FakeMessage *message = new_message(type);
    Job *job = (Job *)calloc(1, sizeof(*job));
    uint64_t id;
    HANDLE thread;
    if (!message || !job) {
        free(message);
        free(job);
        return 0;
    }
    id = message->request_id;
    job->message = message;
    job->user = user;
    strncpy(job->op, op, sizeof(job->op) - 1);
    thread = CreateThread(NULL, 0, run_job, job, 0, NULL);
    if (thread) {
        CloseHandle(thread);
    } else {
        run_job(job);
    }
    return id;
}

static DWORD WINAPI prefetch_identity(LPVOID parameter)
{
    (void)parameter;
    if (!have_identity()) load_identity(NULL);
    return 0;
}

/* The game initialized the Platform: like Meta's, the session's identity is
 * fetched now so ovr_GetLoggedInUserID can answer at once. Entitlement is not. */
static void initialized(const char *id)
{
    HANDLE thread;
    record_app_id(id);
    if (!getenv("RIFTLIFT_PLATFORM_BROKER")) {
        return;
    }
    AcquireSRWLockExclusive(&identity_lock);
    identity_retry_at = GetTickCount();
    ReleaseSRWLockExclusive(&identity_lock);
    thread = CreateThread(NULL, 0, prefetch_identity, NULL, 0, NULL);
    if (thread) CloseHandle(thread);
}

static const char *error_text(int error)
{
    switch (error) {
    case -20000: return "Not supported on this Platform.";
    case -20001: return "RiftLift: sign in to Meta again";
    case -20002: return "RiftLift: this Meta account does not own this app";
    case -20003: return "RiftLift: Meta service or network unavailable";
    case -20004: return "RiftLift: invalid Meta service response";
    case -20005: return "RiftLift: app or account session identity changed or mismatched";
    case -20006: return "RiftLift: Platform request cancelled";
    case -20007: return "RiftLift: Platform is not initialized";
    case -20008: return "RiftLift: incomplete entitlement response; ownership unknown";
    case -20009: return "RiftLift: invalid Platform argument";
    default: return "RiftLift: Platform service unavailable";
    }
}

__declspec(dllexport) uint64_t __cdecl ovr_AssetFile_GetList(void)
{
    typedef uint64_t(__cdecl *user_type)(void);
    typedef uint64_t(__cdecl *request_type)(void);
    union { FARPROC source; user_type target; } user = {real_proc("ovr_GetLoggedInUserID")};
    if (real_platform_initialized() && user.target && user.target()) {
        union { FARPROC source; request_type target; } request = {real_proc("ovr_AssetFile_GetList")};
        return request.target ? request.target() : 0;
    }
    /* The local compatibility session has no service-managed asset downloads.
     * Complete enumeration so applications can load their bundled content. */
    log_call("asset enumeration: no authenticated service, empty local list");
    return enqueue(MSG_ASSET_LIST);
}

__declspec(dllexport) void *__cdecl ovr_Message_GetAssetDetailsArray(const void *object)
{
    typedef void *(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) return (void *)object;
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_Message_GetAssetDetailsArray")};
    return convert.target ? convert.target(object) : NULL;
}

__declspec(dllexport) size_t __cdecl ovr_AssetDetailsArray_GetSize(const void *object)
{
    typedef size_t(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) return 0;
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_AssetDetailsArray_GetSize")};
    return convert.target ? convert.target(object) : 0;
}

__declspec(dllexport) int __cdecl ovr_PlatformInitializeUnrealWindows(const char *app_id)
{
    initialized(app_id);
    log_call("initialize: success");
    return 0;
}

__declspec(dllexport) int __cdecl ovr_PlatformInitializeWindows(const char *app_id)
{
    initialized(app_id);
    log_call("initialize windows: success");
    return 0;
}

__declspec(dllexport) int __cdecl ovr_PlatformInitializeUnityWindows(const char *app_id)
{
    initialized(app_id);
    log_call("initialize unity windows: success");
    return 0;
}

/* Older Unity Platform SDKs (such as Blocks') initialize through this wrapper.
 * Meta's own sets the logging callback, calls the Unity initializer above
 * and always reports true; forwarded, it waits for an Oculus service that
 * never answers under Wine. */
__declspec(dllexport) bool __cdecl ovr_UnityInitWrapperWindows(const char *app_id, void *logging)
{
    (void)logging;
    log_call("unity init wrapper: success");
    ovr_PlatformInitializeUnityWindows(app_id);
    return true;
}

/* Unity's Core.AsyncInitialize waits for a PlatformInitialize response instead
 * of a return code. Forwarding it lets Meta's implementation wait for the
 * unavailable session until the game's authentication times out and it quits.
 * Extra trailing arguments (logging options) are ignored, which is safe for
 * the x64 caller-cleanup calling convention. */
static uint64_t initialize_async(const char *description)
{
    log_call(description);
    return enqueue(MSG_PLATFORM_INITIALIZE_WINDOWS_ASYNC);
}

__declspec(dllexport) uint64_t __cdecl ovr_PlatformInitializeWindowsAsynchronous(const char *app_id)
{
    initialized(app_id);
    return initialize_async("initialize windows async: success queued");
}

__declspec(dllexport) uint64_t __cdecl ovr_UnityInitWrapperWindowsAsynchronous(const char *app_id)
{
    initialized(app_id);
    return initialize_async("unity init wrapper windows async: success queued");
}

__declspec(dllexport) void *__cdecl ovr_Message_GetPlatformInitialize(const void *object)
{
    typedef void *(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) return (void *)object;
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_Message_GetPlatformInitialize")};
    return convert.target ? convert.target(object) : NULL;
}

__declspec(dllexport) int __cdecl ovr_PlatformInitialize_GetResult(const void *object)
{
    typedef int(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) return PLATFORM_INITIALIZE_SUCCESS;
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_PlatformInitialize_GetResult")};
    return convert.target ? convert.target(object) : PLATFORM_INITIALIZE_SUCCESS;
}

__declspec(dllexport) bool __cdecl ovr_IsPlatformInitialized(void)
{
    return true;
}

/* The legacy synchronous check: the game is asking, so Meta is asked. */
__declspec(dllexport) bool __cdecl ovr_IsEntitled(void)
{
    char reply[256];
    int error = ask("entitlement", 0, reply, sizeof(reply));
    log_result("entitlement check (synchronous)", error);
    return error == 0;
}

/* Answered from the session's identity, as Meta's own getter is: 0 until
 * Meta has issued one (no account, or offline). */
__declspec(dllexport) uint64_t __cdecl ovr_GetLoggedInUserID(void)
{
    uint64_t id;
    bool retry;
    AcquireSRWLockShared(&identity_lock);
    id = identity_loaded ? identity_id : 0;
    ReleaseSRWLockShared(&identity_lock);
    if (id || !getenv("RIFTLIFT_PLATFORM_BROKER")) {
        return id;
    }
    /* Waits for the fetch initialization started; a failed one is retried at
     * most every 30 s, since games poll this getter. */
    AcquireSRWLockExclusive(&fetch_lock);
    AcquireSRWLockShared(&identity_lock);
    retry = !identity_loaded && (LONG)(GetTickCount() - identity_retry_at) >= 0;
    ReleaseSRWLockShared(&identity_lock);
    if (retry) {
        load_identity_locked(NULL);
    }
    AcquireSRWLockShared(&identity_lock);
    id = identity_loaded ? identity_id : 0;
    ReleaseSRWLockShared(&identity_lock);
    ReleaseSRWLockExclusive(&fetch_lock);
    return id;
}

__declspec(dllexport) uint64_t __cdecl ovr_User_GetLoggedInUser(void)
{
    log_call("login request: asking Meta");
    return request(MSG_LOGGED_IN_USER, "credentials", 0);
}

__declspec(dllexport) uint64_t __cdecl ovr_User_Get(uint64_t user_id)
{
    typedef uint64_t(__cdecl *function_type)(uint64_t);
    if (user_id && user_id == ovr_GetLoggedInUserID()) {
        return request(MSG_USER, "credentials", user_id);
    }
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_User_Get")};
    return convert.target ? convert.target(user_id) : 0;
}

__declspec(dllexport) uint64_t __cdecl ovr_User_GetAccessToken(void)
{
    log_call("access token request: asking Meta");
    return request(MSG_ACCESS_TOKEN, "credentials", 0);
}

__declspec(dllexport) uint64_t __cdecl ovr_User_GetOrgScopedID(uint64_t user_id)
{
    log_call("org scoped id request: asking Meta");
    return request(MSG_ORG_SCOPED_ID, "org_scoped_id", user_id);
}

__declspec(dllexport) uint64_t __cdecl ovr_User_GetUserProof(void)
{
    log_call("user proof request: asking Meta");
    return request(MSG_USER_PROOF, "user_proof", 0);
}

/* Asked of Meta only now, when the game calls for it, and every time. */
__declspec(dllexport) uint64_t __cdecl ovr_Entitlement_GetIsViewerEntitled(void)
{
    log_call("entitlement request: asking Meta");
    return request(MSG_ENTITLEMENT, "entitlement", 0);
}

__declspec(dllexport) uint64_t __cdecl ovr_Achievements_GetAllDefinitions(void)
{
    typedef uint64_t(__cdecl *function_type)(void);
    if (!offline_compat()) {
        union { FARPROC source; function_type target; } convert = {real_proc("ovr_Achievements_GetAllDefinitions")};
        return convert.target ? convert.target() : 0;
    }
    log_call("achievements definitions request: empty success queued");
    return enqueue(MSG_ACHIEVEMENT_DEFINITIONS);
}

__declspec(dllexport) uint64_t __cdecl ovr_Achievements_GetAllProgress(void)
{
    typedef uint64_t(__cdecl *function_type)(void);
    if (!offline_compat()) {
        union { FARPROC source; function_type target; } convert = {real_proc("ovr_Achievements_GetAllProgress")};
        return convert.target ? convert.target() : 0;
    }
    log_call("achievements progress request: empty success queued");
    return enqueue(MSG_ACHIEVEMENT_PROGRESS);
}

/* Some titles unlock an achievement during startup and wait for its response
 * before entering their render loop. */
__declspec(dllexport) uint64_t __cdecl ovr_Achievements_Unlock(const char *name)
{
    typedef uint64_t(__cdecl *function_type)(const char *);
    if (!offline_compat()) {
        union { FARPROC source; function_type target; } convert = {real_proc("ovr_Achievements_Unlock")};
        return convert.target ? convert.target(name) : 0;
    }
    log_call("achievement unlock request: local success queued");
    return enqueue_named(MSG_ACHIEVEMENT_UNLOCK, name);
}

/* The unlock reply above is local: answer its update getters here, or Meta's
 * would read the fake message as one of its own and crash. */
__declspec(dllexport) void *__cdecl ovr_Message_GetAchievementUpdate(const void *object)
{
    typedef void *(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) return (void *)object;
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_Message_GetAchievementUpdate")};
    return convert.target ? convert.target(object) : NULL;
}

__declspec(dllexport) bool __cdecl ovr_AchievementUpdate_GetJustUnlocked(const void *object)
{
    typedef bool(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) return true;
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_AchievementUpdate_GetJustUnlocked")};
    return convert.target ? convert.target(object) : false;
}

__declspec(dllexport) const char *__cdecl ovr_AchievementUpdate_GetName(const void *object)
{
    typedef const char *(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) return message->name;
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_AchievementUpdate_GetName")};
    return convert.target ? convert.target(object) : NULL;
}

__declspec(dllexport) uint64_t __cdecl ovr_CloudStorage_LoadBucketMetadata(const char *bucket)
{
    typedef uint64_t(__cdecl *function_type)(const char *);
    if (!offline_compat()) {
        union { FARPROC source; function_type target; } convert = {real_proc("ovr_CloudStorage_LoadBucketMetadata")};
        return convert.target ? convert.target(bucket) : 0;
    }
    (void)bucket;
    log_call("cloud bucket metadata request: empty success queued");
    return enqueue(MSG_CLOUD_BUCKET_METADATA);
}

__declspec(dllexport) uint64_t __cdecl ovr_CloudStorage_Load(const char *bucket, const char *key)
{
    typedef uint64_t(__cdecl *function_type)(const char *, const char *);
    union {
        FARPROC source;
        function_type target;
    } convert = {real_proc("ovr_CloudStorage_Load")};
    function_type function = convert.target;
    log_call("cloud load request forwarded");
    return function ? function(bucket, key) : 0;
}

__declspec(dllexport) uint64_t __cdecl ovr_User_GetLoggedInUserFriends(void)
{
    typedef uint64_t(__cdecl *function_type)(void);
    if (!offline_compat()) {
        union { FARPROC source; function_type target; } convert = {real_proc("ovr_User_GetLoggedInUserFriends")};
        return convert.target ? convert.target() : 0;
    }
    log_call("friends request: empty success queued");
    return enqueue(MSG_LOGGED_IN_USER_FRIENDS);
}

/*
 * Unreal's Oculus online subsystem treats these as paged collections.  Empty,
 * successful collections are sufficient for optional achievements, cloud-save
 * enumeration and friends.  Returning the owning message as the collection
 * handle keeps the lifetime tied to ovr_FreeMessage without extra allocation.
 */
__declspec(dllexport) void *__cdecl ovr_Message_GetAchievementDefinitionArray(const void *object)
{
    typedef void *(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) return (void *)object;
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_Message_GetAchievementDefinitionArray")};
    return convert.target ? convert.target(object) : NULL;
}

__declspec(dllexport) size_t __cdecl ovr_AchievementDefinitionArray_GetSize(const void *object)
{
    typedef size_t(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) return 0;
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_AchievementDefinitionArray_GetSize")};
    return convert.target ? convert.target(object) : 0;
}

__declspec(dllexport) bool __cdecl ovr_AchievementDefinitionArray_HasNextPage(const void *object)
{
    typedef bool(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) return false;
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_AchievementDefinitionArray_HasNextPage")};
    return convert.target ? convert.target(object) : false;
}

__declspec(dllexport) void *__cdecl ovr_Message_GetAchievementProgressArray(const void *object)
{
    typedef void *(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) return (void *)object;
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_Message_GetAchievementProgressArray")};
    return convert.target ? convert.target(object) : NULL;
}

__declspec(dllexport) size_t __cdecl ovr_AchievementProgressArray_GetSize(const void *object)
{
    typedef size_t(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) return 0;
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_AchievementProgressArray_GetSize")};
    return convert.target ? convert.target(object) : 0;
}

__declspec(dllexport) bool __cdecl ovr_AchievementProgressArray_HasNextPage(const void *object)
{
    typedef bool(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) return false;
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_AchievementProgressArray_HasNextPage")};
    return convert.target ? convert.target(object) : false;
}

__declspec(dllexport) void *__cdecl ovr_Message_GetCloudStorageMetadataArray(const void *object)
{
    typedef void *(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) return (void *)object;
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_Message_GetCloudStorageMetadataArray")};
    return convert.target ? convert.target(object) : NULL;
}

__declspec(dllexport) size_t __cdecl ovr_CloudStorageMetadataArray_GetSize(const void *object)
{
    typedef size_t(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) return 0;
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_CloudStorageMetadataArray_GetSize")};
    return convert.target ? convert.target(object) : 0;
}

__declspec(dllexport) bool __cdecl ovr_CloudStorageMetadataArray_HasNextPage(const void *object)
{
    typedef bool(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) return false;
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_CloudStorageMetadataArray_HasNextPage")};
    return convert.target ? convert.target(object) : false;
}

__declspec(dllexport) void *__cdecl ovr_Message_GetUserArray(const void *object)
{
    typedef void *(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) return (void *)object;
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_Message_GetUserArray")};
    return convert.target ? convert.target(object) : NULL;
}

__declspec(dllexport) size_t __cdecl ovr_UserArray_GetSize(const void *object)
{
    typedef size_t(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) return 0;
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_UserArray_GetSize")};
    return convert.target ? convert.target(object) : 0;
}

__declspec(dllexport) bool __cdecl ovr_UserArray_HasNextPage(const void *object)
{
    typedef bool(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) return false;
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_UserArray_HasNextPage")};
    return convert.target ? convert.target(object) : false;
}

__declspec(dllexport) void *__cdecl ovr_PopMessage(void)
{
    typedef void *(__cdecl *function_type)(void);
    FakeMessage *message = NULL;

    AcquireSRWLockExclusive(&queue_lock);
    if (queue_head != queue_tail) {
        message = queue[queue_head];
        queue[queue_head] = NULL;
        queue_head = (queue_head + 1) % (sizeof(queue) / sizeof(queue[0]));
    }
    ReleaseSRWLockExclusive(&queue_lock);

    if (message) {
        log_call("message popped");
    } else if (InterlockedIncrement(&empty_poll_count) <= 3) {
        log_call("empty message poll");
    }
    if (message) {
        return message;
    }
    if (!real_platform_initialized()) {
        return NULL;
    }
    union {
        FARPROC source;
        function_type target;
    } convert = {real_proc("ovr_PopMessage")};
    return convert.target ? convert.target() : NULL;
}

__declspec(dllexport) void *__cdecl ovr_Message_GetNativeMessage(const void *object)
{
    typedef void *(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) {
        return (void *)object;
    }
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_Message_GetNativeMessage")};
    return convert.target ? convert.target(object) : NULL;
}

__declspec(dllexport) uint64_t __cdecl ovr_Message_GetRequestID(const void *object)
{
    typedef uint64_t(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) {
        log_call("message request id read");
    }
    if (message && message->magic == FAKE_MAGIC) {
        return message->request_id;
    }
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_Message_GetRequestID")};
    return convert.target ? convert.target(object) : 0;
}

__declspec(dllexport) uint32_t __cdecl ovr_Message_GetType(const void *object)
{
    typedef uint32_t(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) {
        log_call("message type read");
    }
    if (message && message->magic == FAKE_MAGIC) {
        return message->type;
    }
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_Message_GetType")};
    return convert.target ? convert.target(object) : 0;
}

__declspec(dllexport) bool __cdecl ovr_Message_IsError(const void *object)
{
    typedef bool(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) {
        return message->error != 0;
    }
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_Message_IsError")};
    return convert.target ? convert.target(object) : false;
}

/* A failed local request is its own error object: the code is the
 * launcher's, the text says what to do about it. */
__declspec(dllexport) void *__cdecl ovr_Message_GetError(const void *object)
{
    typedef void *(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) {
        return message->error ? (void *)object : NULL;
    }
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_Message_GetError")};
    return convert.target ? convert.target(object) : NULL;
}

__declspec(dllexport) int __cdecl ovr_Error_GetCode(const void *object)
{
    typedef int(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) {
        return message->error;
    }
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_Error_GetCode")};
    return convert.target ? convert.target(object) : 0;
}

__declspec(dllexport) int __cdecl ovr_Error_GetHttpCode(const void *object)
{
    typedef int(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) {
        return 0;
    }
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_Error_GetHttpCode")};
    return convert.target ? convert.target(object) : 0;
}

#define ERROR_STRING_GETTER(name) \
__declspec(dllexport) const char *__cdecl name(const void *object) \
{ \
    typedef const char *(__cdecl *function_type)(const void *); \
    const FakeMessage *message = (const FakeMessage *)object; \
    if (message && message->magic == FAKE_MAGIC) { return error_text(message->error); } \
    union { FARPROC source; function_type target; } convert = {real_proc(#name)}; \
    return convert.target ? convert.target(object) : NULL; \
}

ERROR_STRING_GETTER(ovr_Error_GetMessage)
ERROR_STRING_GETTER(ovr_Error_GetDisplayableMessage)

__declspec(dllexport) void *__cdecl ovr_Message_GetUser(const void *object)
{
    typedef void *(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) {
        return message->error || (message->type != MSG_LOGGED_IN_USER && message->type != MSG_USER)
                   ? NULL : (void *)&identity_user;
    }
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_Message_GetUser")};
    return convert.target ? convert.target(object) : NULL;
}

__declspec(dllexport) const char *__cdecl ovr_Message_GetString(const void *object)
{
    typedef const char *(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) {
        return !message->error && message->type == MSG_ACCESS_TOKEN ? message->text : NULL;
    }
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_Message_GetString")};
    return convert.target ? convert.target(object) : NULL;
}

__declspec(dllexport) void *__cdecl ovr_Message_GetOrgScopedID(const void *object)
{
    typedef void *(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) {
        return message->error || message->type != MSG_ORG_SCOPED_ID ? NULL : (void *)object;
    }
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_Message_GetOrgScopedID")};
    return convert.target ? convert.target(object) : NULL;
}

__declspec(dllexport) uint64_t __cdecl ovr_OrgScopedID_GetID(const void *object)
{
    typedef uint64_t(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) {
        return message->id;
    }
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_OrgScopedID_GetID")};
    return convert.target ? convert.target(object) : 0;
}

__declspec(dllexport) void *__cdecl ovr_Message_GetUserProof(const void *object)
{
    typedef void *(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) {
        return message->error || message->type != MSG_USER_PROOF ? NULL : (void *)object;
    }
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_Message_GetUserProof")};
    return convert.target ? convert.target(object) : NULL;
}

__declspec(dllexport) const char *__cdecl ovr_UserProof_GetNonce(const void *object)
{
    typedef const char *(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) {
        return message->text;
    }
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_UserProof_GetNonce")};
    return convert.target ? convert.target(object) : NULL;
}

__declspec(dllexport) uint64_t __cdecl ovr_User_GetID(const void *object)
{
    typedef uint64_t(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) {
        return ovr_GetLoggedInUserID();
    }
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_User_GetID")};
    return convert.target ? convert.target(object) : 0;
}

/* The identity Meta issued is fixed once loaded, so these stay valid. */
__declspec(dllexport) const char *__cdecl ovr_User_GetOculusID(const void *object)
{
    typedef const char *(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) {
        return identity_alias;
    }
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_User_GetOculusID")};
    return convert.target ? convert.target(object) : NULL;
}

/* Newer Unity SDKs read every User field while constructing the managed
 * response. Never pass a local identity object into Meta's native model ABI. */
#define USER_STRING_GETTER(name, fallback) \
__declspec(dllexport) const char *__cdecl name(const void *object) \
{ \
    typedef const char *(__cdecl *function_type)(const void *); \
    const FakeMessage *message = (const FakeMessage *)object; \
    if (message && message->magic == FAKE_MAGIC) { return fallback; } \
    union { FARPROC source; function_type target; } convert = {real_proc(#name)}; \
    return convert.target ? convert.target(object) : NULL; \
}

USER_STRING_GETTER(ovr_User_GetDisplayName, identity_name)
USER_STRING_GETTER(ovr_User_GetImageUrl, "")
USER_STRING_GETTER(ovr_User_GetSmallImageUrl, "")
USER_STRING_GETTER(ovr_User_GetPresence, "")
USER_STRING_GETTER(ovr_User_GetPresenceDeeplinkMessage, "")
USER_STRING_GETTER(ovr_User_GetPresenceDestinationApiName, "")
USER_STRING_GETTER(ovr_User_GetPresenceLobbySessionId, "")
USER_STRING_GETTER(ovr_User_GetPresenceMatchSessionId, "")

__declspec(dllexport) int __cdecl ovr_User_GetPresenceStatus(const void *object)
{
    typedef int(__cdecl *function_type)(const void *);
    const FakeMessage *message = (const FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) {
        return 0;
    }
    union { FARPROC source; function_type target; } convert = {real_proc("ovr_User_GetPresenceStatus")};
    return convert.target ? convert.target(object) : 0;
}

__declspec(dllexport) void __cdecl ovr_FreeMessage(void *object)
{
    typedef void(__cdecl *function_type)(void *);
    FakeMessage *message = (FakeMessage *)object;
    if (message && message->magic == FAKE_MAGIC) {
        log_call("message freed");
        message->magic = 0;
        free(message->text);
        free(message);
        return;
    }
    union {
        FARPROC source;
        function_type target;
    } convert = {real_proc("ovr_FreeMessage")};
    if (convert.target) {
        convert.target(object);
    }
}
