# Binary repackaging of the baseline release layout (build_packages.sh rpm).
Name:           riftlift
Version:        @VERSION@
Release:        1
Summary:        Play your Meta Rift games on Linux
License:        MIT
URL:            https://github.com/Villagers654/RiftLift
ExclusiveArch:  x86_64
# The programs link only glibc; everything else is loaded when present.
# Automatic dependency scanning would add nothing useful.
AutoReqProv:    no
# Library capabilities, not package names: they resolve on Fedora and openSUSE.
Requires:       libc.so.6(GLIBC_2.35)(64bit)
# Proton's launcher script.
Requires:       /usr/bin/python3
Recommends:     (libwebkitgtk-6.0.so.4()(64bit) or libwebkit2gtk-4.1.so.0()(64bit))
Recommends:     libsecret-1.so.0()(64bit)
Recommends:     libvulkan.so.1()(64bit)
Recommends:     libwayland-client.so.0()(64bit)
Recommends:     libxkbcommon.so.0()(64bit)
Recommends:     libEGL.so.1()(64bit)
Recommends:     libfontconfig.so.1()(64bit)
%global debug_package %{nil}
%global __strip /bin/true
%global __brp_check_rpaths %{nil}

%description
RiftLift installs the Rift / PC VR games you own on Meta and runs them through
your OpenXR runtime or SteamVR, alongside Steam games with an Oculus mode and
Rift games you installed yourself. Games run under GE-Proton, which RiftLift
downloads the first time you play.

%install
cp -a %{stage}/. %{buildroot}/

%files
/opt/riftlift
/usr/bin/riftlift
/usr/bin/riftlift-cli
/usr/share/applications/io.github.villagers654.RiftLift.desktop
/usr/share/icons/hicolor/scalable/apps/io.github.villagers654.RiftLift.svg
/usr/share/metainfo/io.github.villagers654.RiftLift.metainfo.xml
