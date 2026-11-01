# Linux release baseline (scripts/packaging/linux/Containerfile, glibc 2.35).
# The C++ runtime (libc++/libc++abi), libgcc and libatomic are linked
# statically, so shipped binaries need only glibc and kernel/desktop libraries.
# Archive symbols stay local: no libc++ or libatomic symbol is exported to
# dlopen'ed drivers, OpenXR runtimes or the JVM, which bring their own runtime.
set(CMAKE_C_COMPILER clang)
set(CMAKE_CXX_COMPILER clang++)
set(CMAKE_CXX_FLAGS_INIT "-stdlib=libc++")
set(riftlift_baseline_link "-stdlib=libc++ -static-libstdc++ -static-libgcc -fuse-ld=lld -Wl,--exclude-libs,ALL -Wl,--as-needed")
# The Containerfile links libatomic.a alone into this directory, searched
# before the system one, so -latomic resolves to the static archive.
if(EXISTS /opt/riftlift-baseline/static)
  string(APPEND riftlift_baseline_link " -L/opt/riftlift-baseline/static")
endif()
set(CMAKE_EXE_LINKER_FLAGS_INIT "${riftlift_baseline_link}")
set(CMAKE_SHARED_LINKER_FLAGS_INIT "${riftlift_baseline_link}")
set(CMAKE_MODULE_LINKER_FLAGS_INIT "${riftlift_baseline_link}")
# OpenSSL (libcurl's TLS and digests) is linked from its static archives.
set(OPENSSL_USE_STATIC_LIBS TRUE)
