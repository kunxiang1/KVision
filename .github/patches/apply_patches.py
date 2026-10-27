#!/usr/bin/env python3
"""Patch KVision sources for a Windows (MSVC) build. Idempotent."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# The wrapping patches below (guard an existing block in an if/endif) cannot be
# made self-detecting, so a marker keeps a second run from nesting them.
MARK = ROOT / ".github" / ".patched"
if MARK.exists():
    print("sources already patched, nothing to do")
    sys.exit(0)

WIN_SHIM = """#ifdef _WIN32
  #include <process.h>
  typedef unsigned long uid_t;
  #define getpid _getpid
  #define getuid() 0UL
#else
  #include <unistd.h>
#endif
"""

def patch(path, old, new, must=True):
    p = ROOT / path
    t = p.read_text(encoding="utf-8")
    if new in t and old not in t:
        print(f"[skip] {path} (already patched)")
        return
    if old not in t:
        print(f"[FAIL] {path}: pattern not found:\n{old[:200]}")
        if must:
            sys.exit(1)
        return
    p.write_text(t.replace(old, new, 1), encoding="utf-8")
    print(f"[ok] {path}")

# ---- CMakeLists.txt ----------------------------------------------------
cm = "CMakeLists.txt"

# 1. -pthread is a GCC flag; MSVC only warns but keep logs clean
patch(cm,
'SET(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -pthread")',
"""if(NOT WIN32)
SET(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -pthread")
endif()""")

# 2. pkg-config FFmpeg discovery -> plain dirs on Windows
pkg_old = """else()
    find_package(PkgConfig REQUIRED)
    pkg_check_modules(LIBAV REQUIRED
        libavformat
        libavcodec
        libavutil
        libswscale
        libswresample
        libavdevice
    )
    include_directories(${LIBAV_INCLUDE_DIRS})
endif()"""
pkg_new = """else()
    if(WIN32)
        include_directories(${FFMPEG_INC})
        link_directories(${FFMPEG_LIB})
    else()
        find_package(PkgConfig REQUIRED)
        pkg_check_modules(LIBAV REQUIRED
            libavformat
            libavcodec
            libavutil
            libswscale
            libswresample
            libavdevice
        )
        include_directories(${LIBAV_INCLUDE_DIRS})
    endif()
endif()"""
patch(cm, pkg_old, pkg_new)

# 3. GNU linker options (rpath) are unknown to link.exe -> guard with UNIX.
#    Note the trailing space after PRIVATE in the upstream file; build the
#    patterns with explicit \n (a literal line continuation would eat it).
for tgt, ind in (("kvision", ""),
                 ("diagnose_archive", "    "),
                 ("diagnose_live", "    "),
                 ("diagnose_compression", "    ")):
    arg = ind + "    "
    flags = (f'{arg}"LINKER:--disable-new-dtags"\n'
             f'{arg}"LINKER:-rpath-link,${{CMAKE_SOURCE_DIR}}/src/hikvision_sdk/lib"\n')
    old = f"{ind}target_link_options({tgt} PRIVATE \n{flags}{ind})"
    new = (f"{ind}if(UNIX)\n"
           f"{ind}target_link_options({tgt} PRIVATE \n{flags}{ind})\n"
           f"{ind}endif()")
    patch(cm, old, new)

# 4. windows.h before Hikvision SDK headers (they use HWND/LONG when _WIN32),
#    NOMINMAX/WIN32_LEAN_AND_MEAN so Qt sources keep compiling.
patch(cm,
"if(DEFINED VER_REL AND ${VER_REL} GREATER 0)",
"""if(WIN32)
    add_compile_definitions(NOMINMAX WIN32_LEAN_AND_MEAN)
    add_compile_options(/FIwindows.h)
endif()

if(DEFINED VER_REL AND ${VER_REL} GREATER 0)""")

# ---- unistd.h users ----------------------------------------------------
for f in ("src/main.cpp", "src/systemstats.cpp"):
    patch(f, "#include <unistd.h>\n#include <sys/types.h>", WIN_SHIM.rstrip() + "\n#include <sys/types.h>")

# 5. The unit-test target needs GTest and is an ALL target (ctest runs during
#    the build). Not worth installing GTest on Windows -> skip tests there.
patch("src/qmlav/CMakeLists.txt",
"""if (NOT ANDROID)
    add_subdirectory(src/tests)
endif()""",
"""if (NOT ANDROID AND NOT WIN32)
    add_subdirectory(src/tests)
endif()""")

# 6. These guards test the typedef name, but windows.h provides RECT/SYSTEMTIME
#    as real typedefs (not macros), so both blocks redefined tagRECT/_SYSTEMTIME.
patch("src/hcnetsdk_compat.h",
"#ifndef RECT\ntypedef struct tagRECT {",
"#if !defined(_WIN32) && !defined(RECT)\ntypedef struct tagRECT {")

patch("src/hcnetsdk_compat.h",
"#ifndef SYSTEMTIME\ntypedef struct _SYSTEMTIME {",
"#if !defined(_WIN32) && !defined(SYSTEMTIME)\ntypedef struct _SYSTEMTIME {")

# 7. <cxxabi.h> is libstdc++; MSVC needs <malloc.h> instead (alloca in av_err2str)
#    Anchor on the following include so the replacement is not self-matching.
patch("src/qmlav/src/qmlavutils.h",
"#include <cxxabi.h>\n\n#include <QLoggingCategory>",
"""#ifdef _MSC_VER
#include <malloc.h> // alloca() used by av_err2str below
#else
#include <cxxabi.h>
#endif

#include <QLoggingCategory>""")

# 8. typeid().name() is already demangled on MSVC
patch("src/qmlav/src/qmlavutils.h",
"""    static QString rttiTypeName([[maybe_unused]] const T &type) {
        char *n = abi::__cxa_demangle(typeid(T).name(), nullptr, nullptr, nullptr);
        QString ret(n);
        free(n);
        return ret;
    }""",
"""    static QString rttiTypeName([[maybe_unused]] const T &type) {
#ifdef _MSC_VER
        return QString::fromLatin1(typeid(T).name());
#else
        char *n = abi::__cxa_demangle(typeid(T).name(), nullptr, nullptr, nullptr);
        QString ret(n);
        free(n);
        return ret;
#endif
    }""")

MARK.write_text("ok\n", encoding="utf-8")
print("all patches applied")
