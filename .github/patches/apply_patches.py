#!/usr/bin/env python3
"""Patch KVision sources for a Windows (MSVC) build. Idempotent."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

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

# 3. GNU linker options (rpath) are unknown to link.exe -> guard with UNIX
for tgt, ind in (("kvision", ""), ("diagnose_archive", "    "), ("diagnose_live", "    ")):
    old = f"""{ind}target_link_options({tgt} PRIVATE 
{ind}    "LINKER:--disable-new-dtags"
{ind}    "LINKER:-rpath-link,${{CMAKE_SOURCE_DIR}}/src/hikvision_sdk/lib"
{ind})"""
    new = f"""{ind}if(UNIX)
{ind}target_link_options({tgt} PRIVATE 
{ind}    "LINKER:--disable-new-dtags"
{ind}    "LINKER:-rpath-link,${{CMAKE_SOURCE_DIR}}/src/hikvision_sdk/lib"
{ind})
{ind}endif()"""
    patch(cm, old, new, must=False)

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

print("all patches applied")
