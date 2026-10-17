import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "Model.js" as Model

// codePad — the print-sheet builder, reachable from the bar.
//
// Bar, one line: the printer glyph alone when idle, "导出中" while a sheet
// renders, "!" in the urgent colour when the last export failed, and the
// queued-file count when there is something waiting.
//
// Panel, a rectangle below the bar: the files queued for the sheet, the
// directory the export lands in, one button per output format, and the one
// action that opens the real codePad window. Everything here shells out to the
// `codepad` binary the user already has (see "binary" in the plugin schema);
// the plugin never opens a window of its own.
//
// Clicking the bar widget only opens or closes the panel. A stray click on the
// bar can therefore never launch a program or overwrite an export — the
// actions live inside the panel, and each one is labelled with what it does.
//
// Exports run headless (QT_QPA_PLATFORM=offscreen): the binary still lays the
// sheet out with its real engine and exits with the PDF/HTML/DOCX on disk, but
// no window flashes for the second or so the render takes. A build that cannot
// render without a screen turns this off in the plugin schema ("Headless
// export"), and the only difference is a window appearing during the render.
//
// The panel is a KeyboardPanel rather than a PopupCard because it holds a text
// field: the bar's layer surface never owns the keyboard, so an xdg-popup
// child of it cannot take the keystrokes the export-directory field needs.

BarWidget {
  id: root
  moduleName: "io.github.alexwoo79.codepad"

  readonly property string homeDir: Quickshell.env("HOME") || ""
  readonly property string omarchyPath: Quickshell.env("OMARCHY_PATH") || "/usr/share/omarchy"
  readonly property string fileSelectBin: root.omarchyPath + "/bin/omarchy-file-select"

  // Empty means "whatever `codepad` the session finds first": the command is run
  // by name so it resolves through PATH like it would in a terminal. A
  // hardcoded ~/.local/bin/codepad would assume one install layout and break on
  // machines that put it elsewhere (mise shims, /usr/local/bin, a distro
  // package) — the AppImage install is found through the same lookup.
  readonly property string codepadPath: {
    var configured = String(root.setting("binary", "") || "").trim()
    return configured !== "" ? configured : "codepad"
  }
  readonly property bool headlessExport: root.setting("offscreenExport", true) !== false

  // The files codePad will typeset, in the order they were added. Replaced with
  // a fresh array on every change so the Repeater and the buttons re-evaluate.
  property var files: []
  property bool popupOpen: false
  property bool exporting: false
  property string statusText: ""
  property bool statusError: false
  // The path of the last successful export, so the panel can offer to open it.
  property string resultPath: ""

  // Bar.summonBarWidget / hideBarWidget / isBarWidgetOpen look for exactly
  // these on the widget root.
  readonly property bool opened: root.popupOpen
  readonly property bool popoutSwitchClosing: false

  readonly property color foreground: bar ? bar.barForeground : Commons.Color.foreground
  readonly property color dimColor: Qt.darker(root.foreground, 1.5)
  readonly property color urgentColor: bar ? bar.urgent : Commons.Color.urgent
  readonly property color successColor: "#a3be8c"
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property string label: Model.barLabel(root.exporting, root.files.length, root.statusError)
  readonly property color stateColor: root.exporting || root.files.length > 0
    ? root.foreground
    : (root.statusError ? root.urgentColor : root.dimColor)
  readonly property string tooltip: Model.tooltipText({
    exporting: root.exporting,
    fileCount: root.files.length,
    status: root.statusText
  })

  // Where exports land: what the field holds while the panel is open, what the
  // plugin schema remembers otherwise, and the home directory as the last
  // resort. The field is the source of truth so the buttons always act on the
  // path the user can read on screen.
  readonly property string configuredOutputDir: {
    var configured = String(root.setting("outputDir", "") || "").trim()
    return configured !== "" ? Model.expandHome(configured, root.homeDir) : root.homeDir
  }
  readonly property string outputDir: {
    var typed = exportDirField.text.trim()
    if (typed !== "") return Model.expandHome(typed, root.homeDir)
    return root.configuredOutputDir
  }

  // An external change to the configured directory — `omarchy bar set … outputDir`,
  // the shell's settings UI — has to reach the field, or the panel keeps
  // exporting to whatever it was seeded with the first time it opened. Guarded
  // because the property is also evaluated while the field is still being built.
  onConfiguredOutputDirChanged: if (exportDirField) exportDirField.text = root.configuredOutputDir

  implicitWidth: content.implicitWidth + Style.space(16)
  implicitHeight: root.barSize

  // The exact argv for one export. Argument-list execution means a path with
  // spaces, quotes or a leading dash is handed over as one argument instead of
  // being re-split by a shell.
  //
  // Headless mode goes through `env` rather than the Process `environment`
  // property: the variable has to be set before codePad's Qt platform is
  // chosen, `env` execs the binary with it in place, and the exit code that
  // comes back is codePad's own.
  function exportCommand(ext) {
    var argv = []
    if (root.headlessExport) argv.push("env", "QT_QPA_PLATFORM=offscreen")
    argv.push(root.codepadPath)
    for (var i = 0; i < root.files.length; i++) argv.push(root.files[i])
    argv.push("--export-" + ext)
    argv.push(root.outputPath(ext))
    return argv
  }

  function outputPath(ext) {
    return Model.join(root.outputDir, Model.outputName(root.files, ext))
  }

  function addPaths(paths) {
    if (!paths || paths.length === 0) return 0
    var next = root.files.slice()
    var added = 0
    for (var i = 0; i < paths.length; i++) {
      if (next.indexOf(paths[i]) === -1) {
        next.push(paths[i])
        added++
      }
    }
    root.files = next
    return added
  }

  function removeFile(index) {
    var next = root.files.slice()
    next.splice(index, 1)
    root.files = next
  }

  function clearFiles() {
    root.files = []
    root.resultPath = ""
    if (!root.exporting) {
      root.statusText = ""
      root.statusError = false
    }
  }

  // Persist the export directory the Omarchy way: updateEntryInline writes this
  // widget's entry in ~/.config/omarchy/shell.json, the same API the clock, tray
  // and power widgets use. That API replaces the whole entry with the object it
  // is handed, so the remaining settings have to be carried over — sending only
  // id+outputDir would drop the user's configured binary on the next edit.
  function persistOutputDir(dir) {
    if (!root.bar || !root.bar.shell || typeof root.bar.shell.updateEntryInline !== "function") return false
    var value = Model.expandHome(String(dir == null ? "" : dir), root.homeDir)
    // Enter on the value the field was seeded with is not a choice. Writing it
    // would put a directory the user never picked into shell.json — and shell.json
    // is not this widget's to churn on a stray Return.
    if (value === root.configuredOutputDir) return false
    var entry = { id: root.moduleName, outputDir: value }
    var current = root.settings || {}
    for (var key in current) {
      if (key !== "id" && key !== "outputDir") entry[key] = current[key]
    }
    root.bar.shell.updateEntryInline(root.moduleName, entry)
    return true
  }

  // The picker is `omarchy file select`, so the dialog is the desktop's own
  // (and its "recent places" are the user's). Its exit codes are decisions, not
  // faults: 0 with paths is a pick, 1 is a cancelled dialog, 2 is a chooser
  // that never ran.
  function addFiles() {
    if (pickProc.running) return
    root.statusError = false
    root.statusText = "选择文件…"
    pickProc.command = [root.fileSelectBin, "--title", "选择要排版的文件", "--multiple"]
    pickProc.running = true
  }

  function exportSheet(ext) {
    if (root.exporting || ext === "") return
    if (root.files.length === 0) {
      root.statusError = true
      root.statusText = "先加一个文件 —— 导出的是这些文件排成的稿"
      return
    }
    root.statusError = false
    root.resultPath = ""
    root.exporting = true
    root.statusText = "排版中… " + Model.baseName(root.files[0]) + " → " + Model.formatLabel(ext)
    exportProc.target = root.outputPath(ext)
    exportProc.command = root.exportCommand(ext)
    exportProc.running = true
  }

  // Hand the queued files to the real window. Detached, because codePad is a
  // long-running GUI app: waiting on it would keep a Process alive for as long
  // as the user leaves the window open.
  function launch() {
    Quickshell.execDetached([root.codepadPath].concat(root.files))
    root.statusError = false
    root.statusText = root.files.length > 0
      ? "已把 " + root.files.length + " 个文件交给 codePad"
      : "已打开 codePad"
  }

  function openResult() {
    if (root.resultPath === "") return
    Quickshell.execDetached(["xdg-open", root.resultPath])
  }

  function open() {
    if (root.popupOpen) return
    root.popupOpen = true
    // Seed only an empty field: a directory the user is part-way through typing
    // has to survive a reopen instead of being replaced by the remembered one.
    // A configured value that *changes* is a different matter and is handled by
    // onConfiguredOutputDirChanged below, so `omarchy bar set … outputDir` takes
    // effect even in a session where the panel was already opened once.
    if (exportDirField.text.trim() === "") exportDirField.text = root.configuredOutputDir
  }

  function close() {
    root.popupOpen = false
  }

  function togglePanel() {
    if (root.popupOpen) root.close()
    else root.open()
  }

  // Popout arbitration: another widget's panel is taking over the popout slot.
  function closeForPopoutSwitch() {
    root.popupOpen = false
  }

  // The file chooser. `omarchy file select` holds one D-Bus connection across
  // the call and the answer, so this runs as a real Process and reads its
  // stdout when it exits.
  Process {
    id: pickProc
    command: []
    property string outText: ""
    property string errText: ""
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: pickProc.outText = text
    }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: pickProc.errText = text
    }
    onExited: function(exitCode) {
      var picked = Model.parseFileSelect(pickProc.outText)
      var detail = Model.firstLine(pickProc.errText)
      pickProc.outText = ""
      pickProc.errText = ""
      if (exitCode === 0 && picked.length > 0) {
        var added = root.addPaths(picked)
        root.statusError = false
        root.statusText = added === picked.length
          ? "已加入 " + added + " 个文件"
          : "已加入 " + added + " 个文件（" + (picked.length - added) + " 个重复）"
      } else if (exitCode === 1) {
        root.statusError = false
        root.statusText = "没有选择文件"
      } else {
        root.statusError = true
        root.statusText = "打不开文件选择器（退出码 " + exitCode + "）" + (detail !== "" ? "：" + detail : "")
      }
    }
  }

  // The export. codePad renders the sheet, writes the file and exits: 0 means
  // the file is on disk, 1 means it is not. The output is captured because the
  // failure reason is the only thing worth showing when it goes wrong.
  Process {
    id: exportProc
    command: []
    property string target: ""
    property string outText: ""
    property string errText: ""
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: exportProc.outText = text
    }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: exportProc.errText = text
    }
    onExited: function(exitCode) {
      root.exporting = false
      var detail = Model.firstLine(exportProc.errText)
      exportProc.outText = ""
      exportProc.errText = ""
      if (exitCode === 0) {
        root.statusError = false
        root.resultPath = exportProc.target
        root.statusText = "完成 → " + root.resultPath
      } else {
        root.statusError = true
        root.statusText = "导出失败（退出码 " + exitCode + "）" + (detail !== "" ? "：" + detail : "")
      }
    }
  }

  IpcHandler {
    target: root.moduleName

    function open(): void {
      root.open()
    }

    function close(): void {
      root.close()
    }

    function panel(): void {
      root.togglePanel()
    }

    function launch(): void {
      root.launch()
    }

    // Quickshell refuses untyped IPC arguments ("QVariant cannot be used across
    // IPC"), so both arguments are declared as strings. `paths` is newline- or
    // NUL-separated, the same shape `omarchy file select` prints.
    function addFiles(paths: string): void {
      var parsed = Model.parseFileSelect(String(paths).split("\u0000").join("\n"))
      root.addPaths(parsed)
      if (!root.popupOpen) root.open()
    }

    // Scripted queues need a way back to empty: the panel's Clear button is the
    // only other one, and a queue left behind holds the bar badge up.
    function clearFiles(): void {
      root.clearFiles()
    }

    function exportSheet(format: string): void {
      root.exportSheet(Model.normalizeFormat(format))
    }
  }

  // The panel. KeyboardPanel anchors it under the bar, owns outside-click
  // dismissal and the popout slot, and routes owner.close() back here. It is
  // the keyboard-capable sibling of PopupCard: the panel takes the keyboard
  // while it is open, which is what makes the export-directory field editable.
  KeyboardPanel {
    id: popup
    anchorItem: root
    bar: root.bar
    owner: root
    open: root.popupOpen
    focusTarget: exportDirField
    contentWidth: popup.fittedContentWidth(Style.space(360))
    contentHeight: popup.fittedContentHeight(panel.implicitHeight)

    Column {
      id: panel
      anchors.fill: parent
      spacing: Style.space(10)

      // Esc leaves the panel from anywhere in it: the field and the buttons all
      // bubble the key up to here. (Outside-click and a second click on the
      // widget are handled by KeyboardPanel and togglePanel().)
      Keys.onEscapePressed: root.close()

      // Header: glyph, title, state on the right.
      Item {
        width: panel.width
        height: Math.max(headerGlyph.implicitHeight, headerTitle.implicitHeight, headerState.implicitHeight)

        Text {
          id: headerGlyph
          anchors.left: parent.left
          anchors.verticalCenter: parent.verticalCenter
          text: Model.GLYPH
          color: root.stateColor
          font.family: root.fontFamily
          font.pixelSize: Style.font.iconLarge
        }

        Text {
          id: headerTitle
          anchors.left: headerGlyph.right
          anchors.leftMargin: Style.space(6)
          anchors.verticalCenter: parent.verticalCenter
          text: "codePad"
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.title
          font.bold: true
        }

        Text {
          id: headerState
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          text: root.exporting ? "排版中" : (root.files.length > 0 ? root.files.length + " 个文件" : "空闲")
          color: root.statusError ? root.urgentColor : root.dimColor
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
        }
      }

      PanelSeparator { foreground: root.foreground }

      PanelSectionHeader {
        text: "文件"
        foreground: root.foreground
        fontFamily: root.fontFamily
      }

      Text {
        width: panel.width
        visible: root.files.length === 0
        text: "还没有文件。加进来的文件会按顺序排成一份稿：Markdown、源码或纯文本都行。"
        color: root.dimColor
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
      }

      Repeater {
        model: root.files

        Item {
          width: panel.width
          height: Math.max(fileLabel.implicitHeight, removeButton.height)

          Text {
            id: fileLabel
            anchors.left: parent.left
            anchors.right: removeButton.left
            anchors.rightMargin: Style.space(8)
            anchors.verticalCenter: parent.verticalCenter
            text: Model.baseName(modelData)
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            textFormat: Text.PlainText
            // The middle is the part nobody reads: the file name is at one end
            // and the directory at the other.
            elide: Text.ElideMiddle
          }

          PanelActionButton {
            id: removeButton
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            iconText: "\uf00d"
            tooltipText: "移除 " + modelData
            foreground: root.foreground
            hoverColor: root.urgentColor
            onClicked: root.removeFile(index)
          }
        }
      }

      Row {
        width: panel.width
        spacing: Style.space(8)

        Button {
          width: (panel.width - Style.space(8)) / 2
          text: "添加文件…"
          iconText: "\uf067"
          tooltipText: "打开桌面文件选择器，可以一次选多个"
          foreground: root.foreground
          fontFamily: root.fontFamily
          focusable: true
          opacity: pickProc.running ? 0.55 : 1.0
          onClicked: root.addFiles()
        }

        Button {
          width: (panel.width - Style.space(8)) / 2
          text: "清空"
          iconText: "\uf00d"
          tooltipText: "清空排队列表和上一次的导出结果"
          foreground: root.foreground
          fontFamily: root.fontFamily
          focusable: true
          onClicked: root.clearFiles()
        }
      }

      PanelSeparator { foreground: root.foreground }

      PanelSectionHeader {
        text: "导出"
        foreground: root.foreground
        fontFamily: root.fontFamily
      }

      // The output directory is the one thing the user edits here. Enter
      // remembers it for the next session; the field stays the source of truth
      // while the panel is open either way.
      Row {
        width: panel.width
        spacing: Style.space(8)

        Text {
          id: exportDirLabel
          anchors.verticalCenter: parent.verticalCenter
          width: Style.space(58)
          text: "输出目录"
          color: Qt.darker(root.foreground, 1.6)
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          textFormat: Text.PlainText
        }

        TextField {
          id: exportDirField
          width: panel.width - exportDirLabel.width - Style.space(8)
          placeholderText: root.homeDir
          foreground: root.foreground
          font.family: root.fontFamily
          onAccepted: root.persistOutputDir(text)
          Keys.onEscapePressed: root.close()
        }
      }

      // One button per format codePad can write. Disabled without files: a
      // disabled control that explains itself beats an error after the click.
      Row {
        width: panel.width
        spacing: Style.space(8)

        Repeater {
          model: Model.FORMATS

          Button {
            width: (panel.width - Style.space(8) * (Model.FORMATS.length - 1)) / Model.FORMATS.length
            text: Model.formatLabel(modelData)
            tooltipText: root.files.length === 0
              ? "先加一个文件"
              : "把排好的稿写成 " + root.outputPath(modelData)
            foreground: root.foreground
            fontFamily: root.fontFamily
            focusable: true
            opacity: (root.exporting || root.files.length === 0) ? 0.55 : 1.0
            onClicked: root.exportSheet(modelData)
          }
        }
      }

      InfoRow {
        width: panel.width
        visible: root.statusText !== ""
        label: "状态"
        value: root.statusText
        labelColor: root.foreground
        valueColor: root.statusError
          ? root.urgentColor
          : (root.resultPath !== "" ? root.successColor : root.foreground)
        fontFamily: root.fontFamily
      }

      Button {
        width: panel.width
        visible: root.resultPath !== ""
        text: "打开结果"
        iconText: "\uf08e"
        tooltipText: root.resultPath
        foreground: root.foreground
        fontFamily: root.fontFamily
        focusable: true
        onClicked: root.openResult()
      }

      PanelSeparator { foreground: root.foreground }

      // The way into the real window: the preview, the theme, the print
      // dialog and every other part of codePad live there, and the queued
      // files go along so the sheet is the one being built here.
      Button {
        width: panel.width
        text: root.files.length > 0 ? "打开 codePad（" + root.files.length + " 个文件）" : "打开 codePad"
        iconText: Model.GLYPH
        tooltipText: root.codepadPath
        foreground: root.foreground
        fontFamily: root.fontFamily
        focusable: true
        onClicked: root.launch()
      }
    }
  }

  // Interaction layer first, visuals on top: Text does not consume mouse
  // events, so hover/press/tooltip still land on the button.
  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: " "
    labelVisible: false
    tooltipText: root.tooltip
    onPressed: function(buttonCode) {
      // Any button opens the panel: nothing is launched or exported from a bar
      // click, only from a labelled control inside the panel.
      root.togglePanel()
    }
  }

  Row {
    id: content
    anchors.centerIn: parent
    spacing: Style.space(5)

    Text {
      text: Model.GLYPH
      color: root.stateColor
      font.family: root.fontFamily
      font.pixelSize: Style.font.body

      Behavior on color {
        ColorAnimation { duration: 160 }
      }
    }

    // Vertical bars keep the glyph only: there is no room for text.
    Text {
      text: root.label
      visible: !root.vertical && root.label !== ""
      color: root.stateColor
      font.family: root.fontFamily
      font.pixelSize: Style.font.body

      Behavior on color {
        ColorAnimation { duration: 160 }
      }
    }
  }
}
