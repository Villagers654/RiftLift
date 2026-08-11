#include <openxr/openxr.h>

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <dlfcn.h>
#include <unistd.h>

#include <array>
#include <cerrno>
#include <csignal>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <iostream>
#include <memory>
#include <random>
#include <stdexcept>
#include <string>
#include <string_view>

namespace {

constexpr std::uint32_t protocol_version = 2;
volatile std::sig_atomic_t stopping = 0;

void stop(int) { stopping = 1; }

class XrOwner {
  public:
    XrOwner() {
        XrInstanceCreateInfo info{};
        info.type = XR_TYPE_INSTANCE_CREATE_INFO;
        std::strncpy(info.applicationInfo.applicationName, "RiftLift",
                     XR_MAX_APPLICATION_NAME_SIZE - 1);
        info.applicationInfo.applicationVersion = 1;
        std::strncpy(info.applicationInfo.engineName, "RiftLift native runtime",
                     XR_MAX_ENGINE_NAME_SIZE - 1);
        info.applicationInfo.engineVersion = 1;
        info.applicationInfo.apiVersion = XR_CURRENT_API_VERSION;
        check(xrCreateInstance(&info, &instance_), "xrCreateInstance");

        XrInstanceProperties properties{};
        properties.type = XR_TYPE_INSTANCE_PROPERTIES;
        check(xrGetInstanceProperties(instance_, &properties),
              "xrGetInstanceProperties");
        runtime_name_ = properties.runtimeName;
        runtime_version_ = properties.runtimeVersion;
    }

    XrOwner(const XrOwner &) = delete;
    XrOwner &operator=(const XrOwner &) = delete;

    ~XrOwner() {
        if (instance_ != XR_NULL_HANDLE) {
            xrDestroyInstance(instance_);
        }
    }

    const std::string &runtime_name() const { return runtime_name_; }
    XrVersion runtime_version() const { return runtime_version_; }

  private:
    static void check(XrResult result, std::string_view operation) {
        if (XR_FAILED(result)) {
            throw std::runtime_error(std::string(operation) + " failed: " +
                                     std::to_string(result));
        }
    }

    XrInstance instance_{XR_NULL_HANDLE};
    std::string runtime_name_;
    XrVersion runtime_version_{};
};

enum class VrApplicationType : std::int32_t { background = 3 };
using VrInitError = std::int32_t;

struct VrClientCore;
struct VrClientCoreVTable {
    VrInitError (*init)(VrClientCore *, VrApplicationType, const char *);
    void (*cleanup)(VrClientCore *);
    VrInitError (*is_interface_version_valid)(VrClientCore *, const char *);
    void *(*get_generic_interface)(VrClientCore *, const char *, VrInitError *);
    bool (*is_hmd_present)(VrClientCore *);
    const char *(*error_string)(VrClientCore *, VrInitError);
    const char *(*error_symbol)(VrClientCore *, VrInitError);
};
struct VrClientCore {
    const VrClientCoreVTable *vtable;
};

class OpenVrOwner {
  public:
    OpenVrOwner() {
        const char *override_value = std::getenv("VR_OVERRIDE");
        if (!override_value || !*override_value) {
            throw std::runtime_error(
                "VR_OVERRIDE must select a native Linux OpenVR runtime");
        }
        const std::filesystem::path override_path(override_value);
        std::array candidates{
            override_path,
            override_path / "bin" / "linux64" / "vrclient.so",
            override_path / "libxrizer.so",
        };
        for (const auto &candidate : candidates) {
            if (std::filesystem::is_regular_file(candidate)) {
                library_path_ = candidate.string();
                break;
            }
        }
        if (library_path_.empty()) {
            throw std::runtime_error("native OpenVR client not found below " +
                                     override_path.string());
        }

        library_ = dlopen(library_path_.c_str(), RTLD_NOW | RTLD_LOCAL);
        if (!library_) {
            throw std::runtime_error(std::string("dlopen failed: ") + dlerror());
        }
        using Factory = void *(*)(const char *, std::int32_t *);
        auto factory = reinterpret_cast<Factory>(dlsym(library_, "VRClientCoreFactory"));
        if (!factory) {
            throw std::runtime_error(
                "native OpenVR runtime does not export VRClientCoreFactory");
        }
        std::int32_t factory_error = 0;
        core_ = static_cast<VrClientCore *>(factory("IVRClientCore_003", &factory_error));
        if (!core_ || factory_error != 0 || !core_->vtable) {
            throw std::runtime_error("native OpenVR client factory failed: " +
                                     std::to_string(factory_error));
        }
        const VrInitError error =
            core_->vtable->init(core_, VrApplicationType::background, nullptr);
        if (error != 0) {
            const char *detail = core_->vtable->error_string(core_, error);
            throw std::runtime_error(
                "native OpenVR initialization failed: " + std::to_string(error) +
                (detail ? std::string(" (") + detail + ")" : std::string{}));
        }
        initialized_ = true;
    }

    OpenVrOwner(const OpenVrOwner &) = delete;
    OpenVrOwner &operator=(const OpenVrOwner &) = delete;

    ~OpenVrOwner() {
        if (initialized_) {
            core_->vtable->cleanup(core_);
        }
        if (library_) {
            dlclose(library_);
        }
    }

    std::string runtime_name() const {
        return "Linux OpenVR (" + library_path_ + ")";
    }

  private:
    void *library_{};
    VrClientCore *core_{};
    bool initialized_{};
    std::string library_path_;
};

class SocketOwner {
  public:
    SocketOwner() : descriptor_(::socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0)) {
        if (descriptor_ < 0) {
            throw std::runtime_error(std::string("socket failed: ") +
                                     std::strerror(errno));
        }
    }

    SocketOwner(const SocketOwner &) = delete;
    SocketOwner &operator=(const SocketOwner &) = delete;

    ~SocketOwner() {
        if (descriptor_ >= 0) {
            ::close(descriptor_);
        }
    }

    int get() const { return descriptor_; }

  private:
    int descriptor_;
};

std::string token() {
    std::random_device random;
    constexpr char alphabet[] = "0123456789abcdef";
    std::string result(32, '0');
    for (char &character : result) {
        character = alphabet[random() & 0xf];
    }
    return result;
}

std::uint16_t listen_local(int descriptor) {
    const int enabled = 1;
    if (setsockopt(descriptor, SOL_SOCKET, SO_REUSEADDR, &enabled,
                   sizeof(enabled)) < 0) {
        throw std::runtime_error(std::string("setsockopt failed: ") +
                                 std::strerror(errno));
    }

    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    address.sin_port = 0;
    if (bind(descriptor, reinterpret_cast<sockaddr *>(&address),
             sizeof(address)) < 0 ||
        listen(descriptor, 4) < 0) {
        throw std::runtime_error(std::string("listen failed: ") +
                                 std::strerror(errno));
    }

    socklen_t length = sizeof(address);
    if (getsockname(descriptor, reinterpret_cast<sockaddr *>(&address),
                    &length) < 0) {
        throw std::runtime_error(std::string("getsockname failed: ") +
                                 std::strerror(errno));
    }
    return ntohs(address.sin_port);
}

void serve(int descriptor, const std::string &secret,
           const std::string &runtime_name) {
    while (!stopping) {
        fd_set readers;
        FD_ZERO(&readers);
        FD_SET(descriptor, &readers);
        timeval timeout{0, 250000};
        const int ready = select(descriptor + 1, &readers, nullptr, nullptr, &timeout);
        if (ready < 0) {
            if (errno == EINTR) {
                continue;
            }
            throw std::runtime_error(std::string("select failed: ") +
                                     std::strerror(errno));
        }
        if (ready == 0) {
            continue;
        }

        const int accepted = accept4(descriptor, nullptr, nullptr, SOCK_CLOEXEC);
        if (accepted < 0) {
            if (errno == EINTR) {
                continue;
            }
            throw std::runtime_error(std::string("accept failed: ") +
                                     std::strerror(errno));
        }

        std::array<char, 256> request{};
        const ssize_t length = recv(accepted, request.data(), request.size() - 1, 0);
        std::string response = "ERR unauthorized\n";
        if (length > 0) {
            const std::string_view line(request.data(), static_cast<std::size_t>(length));
            const std::string ping = "PING " + secret;
            const std::string shutdown = "SHUTDOWN " + secret;
            if (line.starts_with(ping)) {
                response = "OK " + std::to_string(protocol_version) + " " +
                           runtime_name + "\n";
            } else if (line.starts_with(shutdown)) {
                response = "OK shutting-down\n";
                stopping = 1;
            }
        }
        static_cast<void>(send(accepted, response.data(), response.size(), MSG_NOSIGNAL));
        ::close(accepted);
    }
}

} // namespace

int main(int argc, char **argv) {
    try {
        std::signal(SIGINT, stop);
        std::signal(SIGTERM, stop);
        std::string backend = "openxr";
        if (argc == 2 && std::string_view(argv[1]) == "--backend=openvr") {
            backend = "openvr";
        } else if (argc != 1 &&
                   !(argc == 2 && std::string_view(argv[1]) == "--backend=openxr")) {
            throw std::runtime_error("usage: riftlift-runtime-host [--backend=openxr|openvr]");
        }
        std::string runtime_name;
        XrVersion runtime_version = 0;
        std::unique_ptr<XrOwner> xr;
        std::unique_ptr<OpenVrOwner> openvr;
        if (backend == "openvr") {
            openvr = std::make_unique<OpenVrOwner>();
            runtime_name = openvr->runtime_name();
        } else {
            xr = std::make_unique<XrOwner>();
            runtime_name = xr->runtime_name();
            runtime_version = xr->runtime_version();
        }
        SocketOwner listener;
        const std::uint16_t port = listen_local(listener.get());
        const std::string secret = token();

        std::cout << "RIFTLIFT_RUNTIME\t" << protocol_version << "\t" << backend
                  << "\t127.0.0.1\t" << port << "\t" << secret << "\t"
                  << runtime_version << "\t" << runtime_name << std::endl;
        serve(listener.get(), secret, runtime_name);
        return 0;
    } catch (const std::exception &error) {
        std::cerr << "riftlift-runtime-host: " << error.what() << '\n';
        return 1;
    }
}
