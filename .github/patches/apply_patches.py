#!/usr/bin/env python3
"""Patch KVision sources for a Windows (MSVC) build. Idempotent."""
import re
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
#    /Zc:__cplusplus: without it MSVC reports __cplusplus as 199711L even in
#    C++17 mode, which trips QmlAVThread's static_assert.
patch(cm,
"if(DEFINED VER_REL AND ${VER_REL} GREATER 0)",
"""if(WIN32)
    add_compile_definitions(NOMINMAX WIN32_LEAN_AND_MEAN)
    add_compile_options(/FIwindows.h /Zc:__cplusplus)
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

# 10. Build kvision as a GUI app; the default console subsystem
#     window behind the Qt window on Windows.
patch(cm,
"""else()
    add_executable(kvision ${PROJ_FILES} ${QMLAV_FILES})
endif()""",
"""else()
    add_executable(kvision ${PROJ_FILES} ${QMLAV_FILES})
    if(WIN32)
        set_target_properties(kvision PROPERTIES WIN32_EXECUTABLE TRUE)
    endif()
endif()""")


# 12. Opening the archive window only muted the live grid; the decoders kept
#     pulling every stream behind it. Blank the source while it is open, the
#     binding restores the URL when it closes.
patch("src/ViewportsLayout.qml",
"""            player.source = Qt.binding(function() {
                var vItem = root.model.get(index);
                if (!vItem) return "";
                var d2Failed = viewportItem.d2SecondaryUrlFailed;""",
"""            player.source = Qt.binding(function() {
                var vItem = root.model.get(index);
                if (!vItem) return "";
                var d2Failed = viewportItem.d2SecondaryUrlFailed;
                // The archive window streams on its own; decoding the live grid
                // behind it burns bandwidth and CPU for pixels nobody can see.
                if (typeof rootWindow !== "undefined" && rootWindow.isPlaybackWindowOpen) {
                    return "";
                }""")

# 13. 16x playback. The NVR speed ladder is stepped by NET_DVR_PLAYFAST and
#     each step doubles: 2x=1, 4x=2, 8x=3, 16x=4. Upstream hard-disabled 8x
#     ("never smooth") and only exposed 1/2/4 in the UI.
patch("src/hikvisionarchiveplayer.cpp",
"""    if (std::abs(speedMultiplier) == 8) {
        qWarning() << "[HikArchive] setPlaybackSpeed: 8x playback speed is disabled because it is never smooth";
        return;
    }

""",
"")

patch("src/hikvisionarchiveplayer.cpp",
"            int steps = (absSpeed == 2) ? 1 : ((absSpeed == 4) ? 2 : 3);",
"""            // NVR ladder doubles per step: 2x=1, 4x=2, 8x=3, 16x=4.
            int steps = absSpeed / 2;
            if (steps > 4) steps = 4;""")

patch("src/PlaybackWindow.qml",
"                                model: [1, 2, 4]",
"                                model: [1, 2, 4, 8, 16]")

# 14. GPU decoding is already implemented in qmlav (hwaccel option -> HW device
#     ctx, and qmlavvideobuffer transfers the surface back), and the bundled
#     FFmpeg has h264_d3d11va/hevc_d3d11va compiled in - nothing ever exposed
#     it. Add a checkbox that writes hwaccel=d3d11va into the global options
#     and pushes it to every viewport straight away.
patch("src/SideBar.qml",
"""                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 12
                                enabled: activateMediaChangesCheckbox.checked
                                opacity: activateMediaChangesCheckbox.checked ? 1.0 : 0.5""",
"""                            // Hardware decoding: D3D11VA when available, otherwise
                            // the software path stays in charge.
                            CheckBox {
                                id: hwDecodeCheckbox
                                text: qsTr("硬件解码 (D3D11VA)")
                                Layout.fillWidth: true
                                checked: {
                                    var o = layoutsCollectionSettings.toJSValue("defaultAVFormatOptions");
                                    return !!o && o["hwaccel"] === "d3d11va";
                                }
                                onToggled: {
                                    var opts = layoutsCollectionSettings.toJSValue("defaultAVFormatOptions") || {};
                                    if (checked) {
                                        opts["hwaccel"] = "d3d11va";
                                    } else {
                                        delete opts["hwaccel"];
                                    }
                                    layoutsCollectionSettings.defaultAVFormatOptions = JSON.stringify(opts);
                                    for (var i = 0; i < layoutsCollectionModel.count; ++i) {
                                        var lm = layoutsCollectionModel.get(i);
                                        for (var j = 0; j < lm.count; ++j) {
                                            var item = lm.get(j);
                                            if (!item.ignoreGlobalAVFormatOptions) {
                                                item.avFormatOptions = opts;
                                            }
                                        }
                                    }
                                }
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 12
                                enabled: activateMediaChangesCheckbox.checked
                                opacity: activateMediaChangesCheckbox.checked ? 1.0 : 0.5""")


# 16. Docked camera tree, like iVMS-4200: a permanent left panel. KVision's
#     recorder/camera list only exists inside a separate utility window, which
#     is why the main window feels nothing like the official client.
patch("src/RootWindow.qml",
"""        StackLayout {
            id: stackLayout

            visible: false
            currentIndex: -1
            anchors.fill: parent""",
"""        CameraTreeDock {
            id: cameraTreeDock
            anchors { left: parent.left; top: parent.top; bottom: parent.bottom }
            width: 220
        }

        StackLayout {
            id: stackLayout

            visible: false
            currentIndex: -1
            anchors { left: cameraTreeDock.right; top: parent.top; bottom: parent.bottom; right: parent.right }""")

#     Recorders JSON must be handed in: ids from RootWindow.qml are not visible
#     inside a separate component file.
patch("src/RootWindow.qml",
"""        CameraTreeDock {
            id: cameraTreeDock
            anchors { left: parent.left; top: parent.top; bottom: parent.bottom }
            width: 220
        }""",
"""        CameraTreeDock {
            id: cameraTreeDock
            anchors { left: parent.left; top: parent.top; bottom: parent.bottom }
            recordersJson: rootWindow.hikvisionRecordersJson
            onRecorderPicked: {
                for (var i = 0; i < layoutsCollectionModel.count; ++i) {
                    var l = layoutsCollectionModel.get(i);
                    if (l && l.isNvr && l.nvrIp === ip) { stackLayout.currentIndex = i; break; }
                }
            }
        }""")

# QML compiled into the resource system only knows the files listed in the qrc.
patch("kvision.qrc",
"        <file>src/CctvButton.qml</file>",
"""        <file>src/CctvButton.qml</file>
        <file>src/CameraTreeDock.qml</file>""")

DOCK = ROOT / "src" / "CameraTreeDock.qml"
if not DOCK.exists():
    DOCK.write_text('''import QtQuick 2.12
import QtQuick.Controls 2.12
import QtQuick.Layouts 1.12
import Qt.labs.settings 1.0
import CCTV_Viewer.Core 1.0
import CCTV_Viewer.Utils 1.0

// Permanent left camera tree: recorder -> channel. Double-click a channel to
// put it in the focused viewport (same action as the "+" button in the cameras
// window).
Rectangle {
    id: dock

    color: "#121214"
    border.color: "#2a3540"
    border.width: 1

    // Collapsed by a chevron; the choice is remembered across restarts.
    Settings {
        id: dockSettings
        fileName: Context.config.fileName
        category: "Viewport"
        property bool cameraTreeOpen: true
    }

    width: dockSettings.cameraTreeOpen ? 220 : 26
    clip: true
    Behavior on width { NumberAnimation { duration: 140; easing.type: Easing.OutQuad } }

    property string recordersJson: ""

    // -1 = every recorder; otherwise index into recorders
    property int selectedRecorder: -1
    readonly property var shownRecorders: {
        if (selectedRecorder >= 0 && selectedRecorder < recorders.length)
            return [recorders[selectedRecorder]];
        return recorders;
    }

    property var recorders: {
        try { return JSON.parse(recordersJson || "[]"); } catch (e) { return []; }
    }

    signal recorderPicked(string ip)

    // Fill the current layout with this recorder's channels, in channel order.
    function applyRecorderToLayout(recIndex) {
        var layoutItem = Utils.currentLayout();
        var model = Utils.currentModel();
        if (!layoutItem || !model || recIndex < 0 || recIndex >= recorders.length) return;
        var cams = recorders[recIndex].cameras || [];
        var ip = recorders[recIndex].ip || "";
        if (ip === "" || cams.length === 0) return;
        // model.count is not exposed by this model, so walk get() until it runs out
        for (var i = 0; i < cams.length; ++i) {
            var vp = model.get(i);
            if (!vp) break;
            var url = "hikvision://" + ip + "/" + cams[i].channelId;
            vp.url = url;
            vp.secondaryUrl = url;
            vp.streamMode = 0;
        }
    }

    function assignToFocused(ip, channelId) {
        var layoutItem = Utils.currentLayout();
        var model = Utils.currentModel();
        if (!layoutItem || !model) return;
        var idx = layoutItem.focusIndex;
        if (idx < 0) idx = 0;
        var vp = model.get(idx);
        if (!vp) return;
        var url = "hikvision://" + ip + "/" + channelId;
        vp.url = url;
        vp.secondaryUrl = url;
        vp.streamMode = 0;
        Utils.log_info(qsTr("LNG_00069").arg(ip).arg(channelId).arg(idx + 1));
        console.warn("[Tree] assign ip=" + ip + " ch=" + channelId + " idx=" + idx);
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 6
        spacing: 6

        RowLayout {
            Layout.fillWidth: true
            spacing: 4

            Text {
                visible: dockSettings.cameraTreeOpen
                text: qsTr("摄像机")
                color: "#8898a6"
                font.pixelSize: 11
                font.bold: true
                Layout.fillWidth: true
            }

            Rectangle {
                Layout.preferredWidth: 18
                Layout.preferredHeight: 18
                radius: 3
                color: toggleMouse.containsMouse ? "#202c39" : "transparent"

                Text {
                    anchors.centerIn: parent
                    text: dockSettings.cameraTreeOpen ? "◀" : "▶"
                    color: toggleMouse.containsMouse ? "#00f5d4" : "#8898a6"
                    font.pixelSize: 10
                }

                MouseArea {
                    id: toggleMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    onClicked: dockSettings.cameraTreeOpen = !dockSettings.cameraTreeOpen
                }
            }
        }

        ComboBox {
            id: recorderPicker
            Layout.fillWidth: true
            visible: dockSettings.cameraTreeOpen && dock.recorders.length > 1
            font.pixelSize: 11
            palette.windowText: "#ffffff"
            palette.buttonText: "#ffffff"
            palette.text: "#ffffff"
            palette.base: "#0f151b"
            palette.window: "#1c2630"
            palette.button: "#1c2630"
            palette.highlight: "#00f5d4"
            palette.highlightedText: "#121214"
            model: {
                var labels = [qsTr("全部录像机")];
                for (var i = 0; i < dock.recorders.length; ++i)
                    labels.push(dock.recorders[i].name || dock.recorders[i].ip || ("NVR " + (i + 1)));
                return labels;
            }
            onCurrentIndexChanged: {
                dock.selectedRecorder = currentIndex - 1;
                if (currentIndex > 0 && currentIndex <= dock.recorders.length)
                    dock.recorderPicked(dock.recorders[currentIndex - 1].ip);
            }
        }

        ListView {
            id: treeView
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            visible: dockSettings.cameraTreeOpen
            model: dock.shownRecorders
            spacing: 2

            delegate: ColumnLayout {
                id: recRow
                width: treeView.width
                spacing: 2

                property string recIp: modelData.ip || ""
                property bool nodeOpen: true

                Rectangle {
                    Layout.fillWidth: true
                    height: 20
                    radius: 3
                    color: nodeMouse.containsMouse ? "#202c39" : "transparent"

                    Text {
                        anchors {
                            left: parent.left
                            right: parent.right
                            verticalCenter: parent.verticalCenter
                            leftMargin: 2
                            rightMargin: 4
                        }
                        text: (recRow.nodeOpen ? "▼ " : "▶ ") + (modelData.name || modelData.ip || "")
                        color: "#00f5d4"
                        font.pixelSize: 11
                        font.bold: true
                        elide: Text.ElideRight
                    }

                    MouseArea {
                        id: nodeMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        onClicked: recRow.nodeOpen = !recRow.nodeOpen
                    }
                }

                Repeater {
                    model: modelData.cameras || []

                    Rectangle {
                        Layout.fillWidth: true
                        height: 22
                        radius: 3
                        visible: recRow.nodeOpen
                        color: rowMouse.containsMouse ? "#1c2630" : "transparent"

                        Text {
                            anchors {
                                left: parent.left
                                right: parent.right
                                verticalCenter: parent.verticalCenter
                                leftMargin: 6
                            }
                            text: {
                                var n = modelData.customName || modelData.name || "";
                                return "Ch." + modelData.channelId + "  " + n;
                            }
                            color: "#d0d8e0"
                            font.pixelSize: 11
                            elide: Text.ElideRight
                        }

                        MouseArea {
                            id: rowMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            onDoubleClicked: dock.assignToFocused(recRow.recIp, modelData.channelId)
                        }
                    }
                }
            }
        }
    }
}
''', encoding="utf-8")
    print("[new] src/CameraTreeDock.qml")

# 17. Prototype: one viewport rendered by the Hikvision SDK itself
#     (PlayM4_Play(port, hwnd)) so decode and drawing stay on the GPU, with the
#     other tiles left on the FFmpeg path. See sdkdirectpreview.cpp.
patch("CMakeLists.txt",
"    src/hikvisionplayer.cpp src/hikvisionplayer.h",
"""    src/hikvisionplayer.cpp src/hikvisionplayer.h
    src/sdkdirectpreview.cpp src/sdkdirectpreview.h""")

patch("src/main.cpp",
'    qmlRegisterType<HikvisionPlayer>("CCTV_Viewer.Hikvision", 1, 0, "HikvisionPlayer");',
'''    qmlRegisterType<HikvisionPlayer>("CCTV_Viewer.Hikvision", 1, 0, "HikvisionPlayer");
    qmlRegisterType<SdkDirectPreview>("CCTV_Viewer.Hikvision", 1, 0, "SdkDirectPreview");''')

patch("src/main.cpp",
'#include "hikvisionplayer.h"',
'''#include "hikvisionplayer.h"
#include "sdkdirectpreview.h"''')

HDR = ROOT / "src" / "sdkdirectpreview.h"
if not HDR.exists():
    HDR.write_text('''#ifndef SDKDIRECTPREVIEW_H
#define SDKDIRECTPREVIEW_H

#include <QQuickItem>
#include <QTimer>
#include <QString>

// Hosts a native child window and lets the Hikvision SDK decode and draw into
// it (PlayM4_Play(port, hwnd)). Nothing is painted by Qt, so no frame ever
// travels through system memory.
//
// ponytail: prototype scope - tile 0 only, no audio, no zoom/1:1, geometry is
// polled at 200 ms instead of tracked from the scene graph.
class SdkDirectPreview : public QQuickItem
{
    Q_OBJECT
    Q_PROPERTY(QString recorderIp READ recorderIp WRITE setRecorderIp NOTIFY sourceChanged)
    Q_PROPERTY(int recorderPort READ recorderPort WRITE setRecorderPort NOTIFY sourceChanged)
    Q_PROPERTY(QString username READ username WRITE setUsername NOTIFY sourceChanged)
    Q_PROPERTY(QString password READ password WRITE setPassword NOTIFY sourceChanged)
    Q_PROPERTY(int channelId READ channelId WRITE setChannelId NOTIFY sourceChanged)
    Q_PROPERTY(int streamType READ streamType WRITE setStreamType NOTIFY sourceChanged)
    Q_PROPERTY(bool active READ active WRITE setActive NOTIFY activeChanged)

public:
    explicit SdkDirectPreview(QQuickItem *parent = nullptr);
    ~SdkDirectPreview() override;

    QString recorderIp() const { return m_recorderIp; }
    void setRecorderIp(const QString &v);
    int recorderPort() const { return m_recorderPort; }
    void setRecorderPort(int v);
    QString username() const { return m_username; }
    void setUsername(const QString &v);
    QString password() const { return m_password; }
    void setPassword(const QString &v);
    int channelId() const { return m_channelId; }
    void setChannelId(int v);
    int streamType() const { return m_streamType; }
    void setStreamType(int v);
    bool active() const { return m_active; }
    void setActive(bool v);

    // Called from the NET_DVR realplay callback: opens the PlayM4 stream on the
    // SYSHEAD packet and feeds every following STREAMDATA packet to the decoder.
    void feedData(unsigned long dataType, unsigned char *buffer, unsigned long size);

signals:
    void sourceChanged();
    void activeChanged();

protected:
    void componentComplete() override;

private:
    void restart();
    void stop();
    void syncGeometry();
    bool createHostWindow();
    void destroyHostWindow();

    QString m_recorderIp;
    int m_recorderPort = 8000;
    QString m_username;
    QString m_password;
    int m_channelId = 1;
    int m_streamType = 1;
    bool m_active = false;

    void *m_hwnd = nullptr;
    long m_userId = -1;
    long m_playHandle = -1;
    long m_port = -1;   // PlayM4 ports and SDK handles are LONG
    QTimer m_geometryTimer;
};
#endif // SDKDIRECTPREVIEW_H
''', encoding="utf-8")
    print("[new] src/sdkdirectpreview.h")

CPP = ROOT / "src" / "sdkdirectpreview.cpp"
if not CPP.exists():
    CPP.write_text('''#include "sdkdirectpreview.h"
#include "hcnetsdk_compat.h"

#include <QQuickWindow>
#include <QDebug>
#include <QPointF>

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>

namespace {
constexpr LONG kStreamBufferSize = 4 * 1024 * 1024;

void CALLBACK sdkDataCallback(LONG, DWORD dwDataType, BYTE *pBuffer, DWORD dwBufSize, void *pUser)
{
    if (!pUser) return;
    static_cast<SdkDirectPreview *>(pUser)->feedData(dwDataType, pBuffer, dwBufSize);
}
} // namespace

void SdkDirectPreview::feedData(unsigned long dataType, unsigned char *buffer, unsigned long size)
{
    if (m_port < 0 || !m_hwnd) return;

    if (dataType == NET_DVR_SYSHEAD) {
        PlayM4_SetStreamOpenMode(m_port, STREAME_REALTIME);
        if (!PlayM4_OpenStream(m_port, buffer, size, kStreamBufferSize)) {
            qWarning() << "[SdkDirect] PlayM4_OpenStream failed:" << PlayM4_GetLastError(m_port);
            return;
        }
        if (!PlayM4_Play(m_port, reinterpret_cast<HWND>(m_hwnd))) {
            qWarning() << "[SdkDirect] PlayM4_Play failed:" << PlayM4_GetLastError(m_port);
            return;
        }
        PlayM4_PlaySound(m_port);
        qInfo() << "[SdkDirect] stream opened, drawing into the host window";
    } else if (dataType == NET_DVR_STREAMDATA && size > 0) {
        if (!PlayM4_InputData(m_port, buffer, size)) {
            qWarning() << "[SdkDirect] PlayM4_InputData failed:" << PlayM4_GetLastError(m_port);
        }
    }
}

SdkDirectPreview::SdkDirectPreview(QQuickItem *parent)
    : QQuickItem(parent)
{
    setFlag(ItemHasContents, false);
    m_geometryTimer.setInterval(200);
    connect(&m_geometryTimer, &QTimer::timeout, this, &SdkDirectPreview::syncGeometry);
}

SdkDirectPreview::~SdkDirectPreview()
{
    stop();
    destroyHostWindow();
}

void SdkDirectPreview::setRecorderIp(const QString &v) { if (v == m_recorderIp) return; m_recorderIp = v; emit sourceChanged(); }
void SdkDirectPreview::setRecorderPort(int v) { if (v == m_recorderPort) return; m_recorderPort = v; emit sourceChanged(); }
void SdkDirectPreview::setUsername(const QString &v) { if (v == m_username) return; m_username = v; emit sourceChanged(); }
void SdkDirectPreview::setPassword(const QString &v) { if (v == m_password) return; m_password = v; emit sourceChanged(); }
void SdkDirectPreview::setChannelId(int v) { if (v == m_channelId) return; m_channelId = v; emit sourceChanged(); }
void SdkDirectPreview::setStreamType(int v) { if (v == m_streamType) return; m_streamType = v; emit sourceChanged(); }

void SdkDirectPreview::setActive(bool v)
{
    if (v == m_active) return;
    m_active = v;
    emit activeChanged();
    if (m_active) { restart(); } else { stop(); }
}

void SdkDirectPreview::componentComplete()
{
    QQuickItem::componentComplete();
    if (m_active) restart();
}

bool SdkDirectPreview::createHostWindow()
{
    if (m_hwnd) return true;
    if (!window()) return false;
    HWND parent = reinterpret_cast<HWND>(window()->winId());
    if (!parent) return false;
    m_hwnd = CreateWindowExW(0, L"STATIC", L"", WS_CHILD | WS_VISIBLE | WS_CLIPSIBLINGS,
                             0, 0, 1, 1, parent, nullptr, GetModuleHandleW(nullptr), nullptr);
    if (!m_hwnd) {
        qWarning() << "[SdkDirect] CreateWindowEx failed:" << GetLastError();
        return false;
    }
    qInfo() << "[SdkDirect] host window created";
    return true;
}

void SdkDirectPreview::destroyHostWindow()
{
    if (!m_hwnd) return;
    DestroyWindow(reinterpret_cast<HWND>(m_hwnd));
    m_hwnd = nullptr;
}

void SdkDirectPreview::syncGeometry()
{
    if (!m_hwnd || !window()) return;
    const QPointF scenePos = mapToScene(QPointF(0, 0));
    const qreal dpr = window()->devicePixelRatio();
    const int x = int(scenePos.x() * dpr);
    const int y = int(scenePos.y() * dpr);
    const int w = int(width() * dpr);
    const int h = int(height() * dpr);
    if (w <= 0 || h <= 0) return;
    SetWindowPos(reinterpret_cast<HWND>(m_hwnd), HWND_TOP, x, y, w, h,
                 SWP_NOACTIVATE | SWP_SHOWWINDOW);
}

void SdkDirectPreview::stop()
{
    m_geometryTimer.stop();
    if (m_playHandle >= 0) {
        NET_DVR_StopRealPlay(m_playHandle);
        m_playHandle = -1;
    }
    if (m_port >= 0) {
        PlayM4_Stop(m_port);
        PlayM4_CloseStream(m_port);
        PlayM4_FreePort(m_port);
        m_port = -1;
    }
    if (m_userId >= 0) {
        NET_DVR_Logout(m_userId);
        m_userId = -1;
    }
    if (m_hwnd) ShowWindow(reinterpret_cast<HWND>(m_hwnd), SW_HIDE);
}

void SdkDirectPreview::restart()
{
    stop();
    qWarning() << "[SdkDirect] restart active=" << m_active << "ip=" << m_recorderIp
               << "user=" << m_username << "ch=" << m_channelId << "port=" << m_port;
    if (!m_active || m_recorderIp.isEmpty() || m_username.isEmpty()) return;
    if (!createHostWindow()) return;

    NET_DVR_USER_LOGIN_INFO login;
    NET_DVR_DEVICEINFO_V40 devInfo;
    std::memset(&login, 0, sizeof(login));
    std::memset(&devInfo, 0, sizeof(devInfo));
    QByteArray ip = m_recorderIp.toUtf8();
    std::strncpy(login.sDeviceAddress, ip.constData(), sizeof(login.sDeviceAddress) - 1);
    QByteArray user = m_username.toUtf8();
    std::strncpy(login.sUserName, user.constData(), sizeof(login.sUserName) - 1);
    QByteArray pass = m_password.toUtf8();
    std::strncpy(login.sPassword, pass.constData(), sizeof(login.sPassword) - 1);
    login.wPort = static_cast<WORD>(m_recorderPort);

    m_userId = NET_DVR_Login_V40(&login, &devInfo);
    if (m_userId < 0) {
        qWarning() << "[SdkDirect] login failed:" << NET_DVR_GetLastError();
        return;
    }

    if (!PlayM4_GetPort(&m_port)) {
        qWarning() << "[SdkDirect] PlayM4_GetPort failed:" << PlayM4_GetLastError(-1);
        return;
    }

    NET_DVR_PREVIEWINFO previewInfo;
    std::memset(&previewInfo, 0, sizeof(previewInfo));
    previewInfo.lChannel = m_channelId;
    previewInfo.dwStreamType = static_cast<DWORD>(m_streamType);
    previewInfo.dwLinkMode = 0;
    previewInfo.bBlocked = 1;

    m_playHandle = NET_DVR_RealPlay_V40(m_userId, &previewInfo, sdkDataCallback, this);
    if (m_playHandle < 0) {
        qWarning() << "[SdkDirect] NET_DVR_RealPlay_V40 failed:" << NET_DVR_GetLastError();
        return;
    }

    qInfo() << "[SdkDirect] playing" << m_recorderIp << "ch" << m_channelId
            << (m_streamType == 0 ? "MAIN" : "SUB");
    syncGeometry();
    m_geometryTimer.start();
}
''', encoding="utf-8")
    print("[new] src/sdkdirectpreview.cpp")

# SDk prototype wiring: tile 0 is drawn by the SDK, so the FFmpeg player for
# that tile must stay shut to avoid pulling the stream twice.
patch("src/Player.qml",
"""    function updateSource() {
        if (!root.visible || root.isQuickPlayback) {
            qmlAvPlayer1.source = "";
            qmlAvPlayer1.isSubStreamOfPlayer = false;
            qmlAvPlayer2.source = "";
            qmlAvPlayer2.isSubStreamOfPlayer = false;
            activeStreamUrl = "";
            activeCameraId = "";
            return;
        }""",
"""    function updateSource() {
        if (!root.visible || root.isQuickPlayback) {
            qmlAvPlayer1.source = "";
            qmlAvPlayer1.isSubStreamOfPlayer = false;
            qmlAvPlayer2.source = "";
            qmlAvPlayer2.isSubStreamOfPlayer = false;
            activeStreamUrl = "";
            activeCameraId = "";
            return;
        }

        // ponytail: prototype - tile 0 is rendered by the SDK itself, so the
        // FFmpeg players for it must not open a second connection.
        if (root.index === 0 && root.isHikvision && hikPlayerSettings.useRealStreams) {
            qmlAvPlayer1.source = "";
            qmlAvPlayer1.isSubStreamOfPlayer = false;
            qmlAvPlayer2.source = "";
            qmlAvPlayer2.isSubStreamOfPlayer = false;
            activeStreamUrl = "";
            activeCameraId = "";
            return;
        }""")

patch("src/Player.qml",
"""            // Hikvision C++ Painted Player renders high-tech mock layout if fallback is disabled
            HikvisionPlayer {""",
"""            // Prototype: SDK decodes and draws straight into a native child
            // window (PlayM4_Play(port, hwnd)); nothing passes through Qt.
            SdkDirectPreview {
                id: sdkDirect
                visible: root.index === 0 && root.isHikvision && hikPlayerSettings.useRealStreams
                         && root.visible && !root.isQuickPlayback
                x: 0
                y: 0
                width: parent.width
                height: parent.height
                active: visible && root.recorderIp !== "" && root.username !== ""
                recorderIp: active ? root.recorderIp : ""
                recorderPort: root.recorderPort
                username: root.username
                password: root.password
                channelId: parseInt(root.channelId) || 1
                streamType: root.isSubStream ? 1 : 0
                Component.onCompleted: console.warn("[SdkDirect] created idx=" + root.index
                    + " tile=" + hikPlayerSettings.sdkDirectTile
                    + " hik=" + root.isHikvision
                    + " vis=" + root.visible
                    + " ip=" + root.recorderIp
                    + " user=" + root.username
                    + " active=" + (root.index === hikPlayerSettings.sdkDirectTile))
            }

            // Hikvision C++ Painted Player renders high-tech mock layout if fallback is disabled
            HikvisionPlayer {""")


# 19. Every CheckBox in the settings panel kept the style's default label colour,
#     i.e. black text on the dark panel. The panel already sets
#     palette.highlight but never the text roles. Patch them all at once.
SIDEBAR = ROOT / "src" / "SideBar.qml"
_sb = SIDEBAR.read_text(encoding="utf-8")
if 'palette.windowText: "#ffffff"' not in _sb:
    _sb2, n = re.subn(r'(?m)^(\s*)CheckBox \{$',
                      lambda m: (f'{m.group(1)}CheckBox {{\n'
                                 f'{m.group(1)}    palette.windowText: "#ffffff"\n'
                                 f'{m.group(1)}    palette.text: "#00f5d4"\n'
                                 f'{m.group(1)}    palette.base: "#1c2630"\n'
                                 f'{m.group(1)}    palette.light: "#2a3540"\n'
                                 f'{m.group(1)}    palette.mid: "#00f5d4"\n'
                                 f'{m.group(1)}    palette.midlight: "#2a3540"'),
                      _sb)
    SIDEBAR.write_text(_sb2, encoding="utf-8")
    print(f"[ok] src/SideBar.qml ({n} CheckBox labels set to white)")

patch("src/Player.qml",
'        property bool useRealStreams: true',
'''        property bool useRealStreams: true
        // -1 = off. Set to a viewport index to test the SDK direct-render path.
        property int sdkDirectTile: 0''')

patch("src/Player.qml",
"        if (root.index === 0 && root.isHikvision && hikPlayerSettings.useRealStreams) {",
"        if (root.index === hikPlayerSettings.sdkDirectTile && root.isHikvision && hikPlayerSettings.useRealStreams) {")

patch("src/Player.qml",
"                visible: root.index === 0 && root.isHikvision && hikPlayerSettings.useRealStreams",
"                visible: root.index === hikPlayerSettings.sdkDirectTile && root.isHikvision && hikPlayerSettings.useRealStreams")


# 21. The log line has to go into the generated .cpp, not the string that builds it.
patch("src/sdkdirectpreview.cpp",
'    if (!m_active || m_recorderIp.isEmpty() || m_username.isEmpty()) return;',
'''    qWarning() << "[SdkDirect] restart active=" << m_active << "ip=" << m_recorderIp
               << "user=" << m_username << "ch=" << m_channelId;
    if (!m_active || m_recorderIp.isEmpty() || m_username.isEmpty()) return;''')

MARK.write_text("ok\n", encoding="utf-8")
print("all patches applied")
