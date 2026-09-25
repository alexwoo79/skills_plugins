import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model

// Proxy (proxyctl) — system proxy state in the bar plus a details panel.
//
// Bar, one line: glyph plus "off" / "on 87ms" / "on !". Colour carries the
// state, and the urgent colour means the last request through the proxy
// failed.
//
// Panel, a rectangle below the bar: the endpoint, the health line with the
// measured latency, the exit IP the proxy presents to the world, and the
// actions — the enable/disable switch, re-check, copy a status block.
//
// The plugin deliberately never opens a window of its own: the Omarchy
// presentation terminal holds itself open until a key is pressed, so a
// spawn-on-click action leaves windows behind. Everything the panel shows is
// therefore rendered in the panel, and copying is done through the clipboard
// helper with argv only.
//
// State comes from the file proxyctl manages for the desktop session
// (~/.config/environment.d/proxy.conf): present means the proxy is on,
// missing means direct. The file is watched, so `proxyctl on` / `off` /
// `restore` from a terminal moves the bar and the panel without a shell
// restart. Nothing is written except by proxyctl itself.
//
// Clicking the bar widget only opens or closes the panel — the proxy itself is
// switched from the control inside it, so a stray click on the bar can never
// change system state. (IPC callers can still ask for an explicit toggle:
// `omarchy-shell io.github.alexwoo79.proxy toggle`.)
//
// "On" and "working" are different facts: direct access on this network works
// for most sites while a LAN proxy can be slow or lossy, and a proxy that is
// on but unreachable leaves Chromium with no internet at all, because browser
// flags have no fallback. Hence the probe, and the latency in the label.

BarWidget {
  id: root
  moduleName: "io.github.alexwoo79.proxy"

  readonly property string homeDir: Quickshell.env("HOME") || ""
  readonly property string configHome: Quickshell.env("XDG_CONFIG_HOME") || (root.homeDir + "/.config")
  readonly property string envFilePath: root.configHome + "/environment.d/proxy.conf"

  readonly property string proxyctlPath: {
    var configured = String(root.setting("proxyctlPath", "") || "").trim()
    return configured !== "" ? configured : root.homeDir + "/.local/bin/proxyctl"
  }
  readonly property string configuredAddress: Model.normalizeAddress(root.setting("address", Model.DEFAULT_ADDRESS), Model.DEFAULT_ADDRESS)
  readonly property int intervalSeconds: Model.checkIntervalSeconds(root.setting("checkInterval", Model.DEFAULT_CHECK_INTERVAL_SECONDS))
  readonly property bool probeEnabled: root.setting("healthCheck", true) !== false

  property bool proxyOn: false
  property string endpoint: ""
  property bool busy: false
  property var health: null
  property string lastError: ""

  property bool popupOpen: false
  property string exitIp: ""
  property bool exitIpBusy: false
  property bool copied: false
  property string copyError: ""

  // proxyctl's own view of the layers (parsed from `proxyctl status`) and the
  // dev-tool snapshot (`proxyctl tools list`).
  property var status: null
  property var tools: null
  property bool tunBusy: false

  // Bar.summonBarWidget / hideBarWidget / isBarWidgetOpen look for exactly
  // these on the widget root.
  readonly property bool opened: root.popupOpen
  readonly property bool popoutSwitchClosing: false

  readonly property string liveAddress: root.endpoint !== "" ? root.endpoint : root.configuredAddress
  // What the endpoint field holds. The field is the source of truth while the
  // panel is open, so the switch always acts on what the user can see.
  readonly property string draftAddress: endpointField.text.trim()
  readonly property bool draftValid: Model.isAddress(root.draftAddress)
  readonly property bool draftPending: root.proxyOn && root.draftValid && root.draftAddress !== root.endpoint
  readonly property color foreground: bar ? bar.barForeground : Color.foreground
  readonly property color dimColor: Qt.darker(root.foreground, 1.5)
  readonly property color urgentColor: bar ? bar.urgent : Color.urgent
  readonly property color successColor: "#a3be8c"
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property string label: Model.barLabel(root.proxyOn, root.busy, root.health)
  readonly property color stateColor: !root.proxyOn
    ? root.dimColor
    : (Model.healthFailed(root.health) ? root.urgentColor : root.foreground)
  readonly property string tooltip: Model.tooltipText({
    on: root.proxyOn,
    busy: root.busy,
    endpoint: root.liveAddress,
    configured: root.configuredAddress,
    health: root.health,
    probeEnabled: root.probeEnabled,
    tun: root.status ? root.status.tun.service : "",
    error: root.lastError
  })

  // Panel strings.
  readonly property string panelStateText: !root.proxyOn
    ? "Off"
    : (Model.healthFailed(root.health) ? "On \u00b7 failing" : (Model.healthOk(root.health) ? "On \u00b7 working" : "On"))
  readonly property color panelStateColor: !root.proxyOn
    ? root.dimColor
    : (Model.healthFailed(root.health) ? root.urgentColor : root.successColor)
  readonly property string healthText: Model.healthLine(root.proxyOn, root.busy, root.health, root.probeEnabled)
  readonly property string exitIpText: Model.exitIpLine(root.proxyOn, root.exitIpBusy, root.exitIp)
  readonly property color healthColor: !root.proxyOn
    ? root.dimColor
    : (Model.healthFailed(root.health) ? root.urgentColor : root.foreground)
  readonly property bool tunRunning: !!root.status && root.status.tun.service === "running"
  readonly property var layerRows: Model.layerRows(root.status, root.tools)

  implicitWidth: content.implicitWidth + Style.space(16)
  implicitHeight: root.barSize

  // Seed the field so scripted toggles (IPC) work before the panel was ever
  // opened; open() re-seeds from the live endpoint each time.
  Component.onCompleted: endpointField.text = root.liveAddress

  // One read of the managed env file. A missing file is not an error: it is
  // exactly what "direct" looks like after `proxyctl off`.
  function applyEnvFile(text) {
    var state = Model.parseEnvFile(text)
    var changed = state.on !== root.proxyOn || state.endpoint !== root.endpoint
    root.proxyOn = state.on
    root.endpoint = state.endpoint
    if (!state.on) {
      root.health = null
      root.exitIp = ""
      return
    }
    if (changed || root.health === null) {
      root.health = null
      if (root.probeEnabled) Qt.callLater(root.checkNow)
    }
  }

  function refresh() {
    envView.reload()
  }

  // Persist the address the Omarchy way: updateEntryInline merges settings
  // into this widget's entry in ~/.config/omarchy/shell.json, the same API
  // the clock, tray and power widgets use. Returns false when the shell API is
  // unavailable, in which case the value still holds for this session.
  function persistAddress(address) {
    if (!root.bar || !root.bar.shell || typeof root.bar.shell.updateEntryInline !== "function") return false
    root.bar.shell.updateEntryInline(root.moduleName, { id: root.moduleName, address: address })
    return true
  }

  // Enter in the endpoint field: validate, remember, and if the proxy is
  // already on, re-apply it to the new endpoint.
  function applyAddress(value) {
    var address = String(value == null ? "" : value).trim()
    if (!Model.isAddress(address)) {
      root.lastError = "invalid address: " + (address === "" ? "(empty)" : address)
      return false
    }
    root.lastError = ""
    root.persistAddress(address)
    endpointField.text = address
    if (root.proxyOn) {
      root.busy = true
      toggleProc.command = [root.proxyctlPath, "on", "--address", address]
      toggleProc.running = true
    }
    return true
  }

  function toggle() {
    if (root.busy) return
    // Enabling uses the address in the field — the one the user just read.
    if (!root.proxyOn && root.draftAddress === "") endpointField.text = root.configuredAddress
    if (!root.proxyOn && !root.draftValid) {
      root.lastError = "invalid address: " + (root.draftAddress === "" ? "(empty)" : root.draftAddress)
      return
    }
    var target = root.proxyOn ? root.liveAddress : root.draftAddress
    if (!root.proxyOn) root.persistAddress(target)
    var args = Model.toggleArgs(root.proxyOn, target, root.proxyctlPath)
    if (args.length === 0) {
      root.lastError = "invalid proxy address: " + target
      return
    }
    root.lastError = ""
    root.busy = true
    toggleProc.command = args
    toggleProc.running = true
  }

  function checkNow() {
    if (!root.proxyOn || root.busy || probeProc.running) return
    if (!Model.isAddress(root.liveAddress)) return
    probeProc.command = Model.probeArgs(root.liveAddress)
    probeProc.running = true
  }

  // Only asked for while the panel is open (or after a manual re-check): the
  // exit IP costs one more request through a proxy that may be slow.
  function checkExitIp() {
    if (!root.proxyOn || root.exitIpBusy || ipProc.running) return
    if (!Model.isAddress(root.liveAddress)) return
    root.exitIpBusy = true
    ipProc.command = Model.exitIpArgs(root.liveAddress)
    ipProc.running = true
  }

  // One `proxyctl status` (plus its tools snapshot) whenever the panel opens
  // or after anything changes a layer.
  function refreshStatus() {
    if (statusProc.running) return
    statusProc.command = Model.statusArgs(root.proxyctlPath)
    statusProc.running = true
    if (!toolsProc.running) {
      toolsProc.command = Model.toolsArgs(root.proxyctlPath)
      toolsProc.running = true
    }
  }

  // TUN is the network-layer switch from the skill: it routes every app
  // through the local mihomo, so it takes the same upstream the panel shows.
  function toggleTun() {
    if (root.tunBusy) return
    if (!root.tunRunning && root.draftAddress === "") endpointField.text = root.configuredAddress
    if (!root.tunRunning && !root.draftValid) {
      root.lastError = "invalid address: " + (root.draftAddress === "" ? "(empty)" : root.draftAddress)
      return
    }
    var args = Model.tunArgs(root.tunRunning, root.draftAddress, root.proxyctlPath)
    if (args.length === 0) {
      root.lastError = "cannot run proxyctl tun"
      return
    }
    root.lastError = ""
    root.tunBusy = true
    tunProc.command = args
    tunProc.running = true
  }

  function copyStatus() {
    var omarchyPath = Quickshell.env("OMARCHY_PATH")
    var binDir = omarchyPath ? omarchyPath + "/bin" : "/usr/bin"
    root.copied = false
    root.copyError = ""
    copyProc.command = [binDir + "/omarchy-clipboard-paste-text", "--copy-only", Model.statusSummary({
      on: root.proxyOn,
      endpoint: root.liveAddress,
      configured: root.configuredAddress,
      busy: root.busy,
      health: root.health,
      exitIp: root.exitIp,
      exitIpBusy: root.exitIpBusy,
      probeEnabled: root.probeEnabled,
      error: root.lastError
    })]
    copyProc.running = true
  }

  function open() {
    if (root.popupOpen) return
    root.popupOpen = true
    root.copied = false
    root.copyError = ""
    endpointField.text = root.liveAddress
    root.checkExitIp()
    root.checkNow()
    root.refreshStatus()
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

  // The managed session env file, watched, so proxyctl from a terminal moves
  // the bar immediately.
  FileView {
    id: envView
    path: root.envFilePath
    watchChanges: true
    atomicWrites: true
    printErrors: false
    onLoaded: root.applyEnvFile(text())
    onFileChanged: reload()
    onLoadFailed: root.applyEnvFile("")
  }

  // Health probe: one bounded curl through the proxy. The probe only starts
  // when the previous one has exited (probeProc.running gate) and curl itself
  // is capped by -m.
  Process {
    id: probeProc
    command: []
    property string outText: ""
    property string errText: ""
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: probeProc.outText = text
    }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: probeProc.errText = text
    }
    onExited: function(exitCode) {
      var result = Model.parseProbe(exitCode, probeProc.outText, probeProc.errText)
      result.at = Date.now()
      probeProc.outText = ""
      probeProc.errText = ""
      root.health = result
    }
  }

  // Exit-IP probe: the same tunnel, a different question.
  Process {
    id: ipProc
    command: []
    property string outText: ""
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: ipProc.outText = text
    }
    onExited: function(exitCode) {
      root.exitIpBusy = false
      root.exitIp = exitCode === 0 ? Model.cleanExitIp(ipProc.outText) : ""
      ipProc.outText = ""
    }
  }

  // Toggle: proxyctl owns every layer (gsettings, session env, browser flags,
  // git, dev tools), so the widget only runs it and re-reads state.
  Process {
    id: toggleProc
    command: []
    property string errText: ""
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: toggleProc.errText = text
    }
    onExited: function(exitCode) {
      root.busy = false
      var detail = toggleProc.errText.trim()
      root.lastError = exitCode === 0
        ? ""
        : ("proxyctl exit " + exitCode + (detail !== "" ? ": " + detail : ""))
      toggleProc.errText = ""
      Qt.callLater(root.refresh)
      Qt.callLater(root.refreshStatus)
    }
  }

  Process {
    id: copyProc
    command: []
    onExited: function(exitCode) {
      root.copied = exitCode === 0
      root.copyError = exitCode === 0 ? "" : ("clipboard exit " + exitCode)
    }
  }

  // proxyctl's own report: the source of every layer row in the panel.
  Process {
    id: statusProc
    command: []
    property string outText: ""
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: statusProc.outText = text
    }
    onExited: function(exitCode) {
      if (exitCode === 0) root.status = Model.parseStatus(statusProc.outText)
      statusProc.outText = ""
    }
  }

  Process {
    id: toolsProc
    command: []
    property string outText: ""
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: toolsProc.outText = text
    }
    onExited: function(exitCode) {
      if (exitCode === 0) root.tools = Model.parseTools(toolsProc.outText)
      toolsProc.outText = ""
    }
  }

  // TUN is the slow one on purpose: mihomo validates the generated config
  // before the service starts, so the stderr text is worth showing verbatim.
  Process {
    id: tunProc
    command: []
    property string errText: ""
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: tunProc.errText = text
    }
    onExited: function(exitCode) {
      root.tunBusy = false
      var detail = tunProc.errText.trim()
      root.lastError = exitCode === 0
        ? ""
        : ("proxyctl tun exit " + exitCode + (detail !== "" ? ": " + detail : ""))
      tunProc.errText = ""
      Qt.callLater(root.refreshStatus)
    }
  }

  // Periodic refresh doubles as the health cadence: re-read the env file so an
  // external toggle is noticed even if the watch missed it, then probe.
  Timer {
    interval: root.intervalSeconds * 1000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: {
      root.refresh()
      if (root.proxyOn && root.probeEnabled && !root.busy) root.checkNow()
      if (root.popupOpen && !root.busy) root.checkExitIp()
      if (root.popupOpen) root.refreshStatus()
    }
  }

  IpcHandler {
    target: root.moduleName

    function refresh(): void {
      root.refresh()
    }

    function toggle(): void {
      root.toggle()
    }

    function check(): void {
      root.checkNow()
    }

    // Quickshell refuses untyped IPC arguments ("QVariant cannot be used
    // across IPC"), so the address is declared as a string.
    function apply(address: string): void {
      root.applyAddress(address)
    }

    function open(): void {
      root.open()
    }

    function close(): void {
      root.close()
    }

    function panel(): void {
      root.togglePanel()
    }
  }

  // The details panel. PopupCard anchors it under the bar, owns outside-click
  // dismissal and the popout slot, and routes owner.close() back here.
  PopupCard {
    id: popup
    anchorItem: root
    bar: root.bar
    owner: root
    open: root.popupOpen
    contentWidth: popup.fittedContentWidth(Style.space(330))
    contentHeight: popup.fittedContentHeight(panel.implicitHeight)

    Column {
      id: panel
      anchors.fill: parent
      spacing: Style.space(10)

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
          text: "Proxy"
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.title
          font.bold: true
        }

        Text {
          id: headerState
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          text: root.panelStateText
          color: root.panelStateColor
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
        }
      }

      PanelSeparator { foreground: root.foreground }

      // The endpoint is the one thing the user edits here: type host:port,
      // press Enter to apply it (and re-apply it to a running proxy), or just
      // flip the switch below to enable/disable with this address.
      Row {
        width: panel.width
        spacing: Style.space(8)

        Text {
          id: endpointLabel
          anchors.verticalCenter: parent.verticalCenter
          width: Style.space(78)
          text: "Endpoint"
          color: Qt.darker(root.foreground, 1.6)
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          textFormat: Text.PlainText
        }

        TextField {
          id: endpointField
          width: panel.width - endpointLabel.width - Style.space(8)
          placeholderText: "host:port"
          foreground: root.draftValid ? root.foreground : root.urgentColor
          font.family: root.fontFamily
          // Enter applies; losing focus does not, so clicking around the panel
          // can never re-point a running proxy by accident.
          onAccepted: root.applyAddress(text)
        }
      }

      Text {
        width: panel.width
        visible: !root.draftValid || root.draftPending
        text: !root.draftValid
          ? "Enter an address as host:port \u2014 the switch enables it"
          : "Press Enter to apply " + root.draftAddress + " to the running proxy"
        color: !root.draftValid ? root.urgentColor : root.dimColor
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
      }

      InfoRow {
        width: panel.width
        label: "Health"
        value: root.healthText
        labelColor: root.foreground
        valueColor: root.healthColor
        fontFamily: root.fontFamily
      }

      InfoRow {
        width: panel.width
        label: "Exit IP"
        value: root.exitIpText
        labelColor: root.foreground
        valueColor: root.proxyOn ? root.foreground : root.dimColor
        fontFamily: root.fontFamily
      }

      InfoRow {
        width: panel.width
        visible: root.lastError !== ""
        label: "Error"
        value: root.lastError
        labelColor: root.foreground
        valueColor: root.urgentColor
        fontFamily: root.fontFamily
      }

      PanelSeparator { foreground: root.foreground }

      // The one control that changes system state, and it lives in the panel.
      Toggle {
        width: panel.width
        label: "System proxy"
        description: root.proxyOn
          ? "Enabled \u2014 browsers, terminals, git and dev tools use " + root.liveAddress
          : "Disabled \u2014 everything connects directly"
        checked: root.proxyOn
        foreground: root.foreground
        fontFamily: root.fontFamily
        opacity: root.busy ? 0.55 : 1.0
        onClicked: root.toggle()
      }

      // TUN mode: the skill's network-layer switch. It routes every app
      // through the local mihomo using the endpoint above, so a wrong or dead
      // upstream cuts the machine off — hence the explicit wording.
      Toggle {
        width: panel.width
        label: "TUN mode"
        description: root.tunRunning
          ? "Running \u2014 every app routed at the network layer"
          : "Stopped \u2014 verify the endpoint before enabling"
        checked: root.tunRunning
        foreground: root.foreground
        fontFamily: root.fontFamily
        opacity: root.tunBusy ? 0.55 : 1.0
        onClicked: root.toggleTun()
      }

      PanelSeparator { foreground: root.foreground }

      PanelSectionHeader {
        text: "Layers"
        foreground: root.foreground
        fontFamily: root.fontFamily
      }

      Repeater {
        model: root.layerRows

        InfoRow {
          width: panel.width
          labelWidth: Style.space(104)
          label: modelData.label
          value: modelData.value
          labelColor: root.foreground
          valueColor: modelData.ok ? root.foreground : root.dimColor
          fontFamily: root.fontFamily
        }
      }

      PanelSeparator { foreground: root.foreground }

      Row {
        width: panel.width
        spacing: Style.space(8)

        Button {
          width: (panel.width - Style.space(8)) / 2
          text: "Check"
          iconText: "\uf021"
          tooltipText: "Re-run the health and exit-IP probes"
          foreground: root.foreground
          fontFamily: root.fontFamily
          focusable: true
          onClicked: {
            root.checkNow()
            root.checkExitIp()
          }
        }

        Button {
          width: (panel.width - Style.space(8)) / 2
          text: root.copied ? "Copied \u2713" : (root.copyError !== "" ? "Failed" : "Copy")
          iconText: "\uf0c5"
          tooltipText: "Copy a status block to the clipboard"
          foreground: root.foreground
          fontFamily: root.fontFamily
          focusable: true
          onClicked: root.copyStatus()
        }
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
      // Any button opens the panel: state changes never happen from a bar
      // click, only from the switch inside the panel.
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
      visible: !root.vertical
      color: root.stateColor
      font.family: root.fontFamily
      font.pixelSize: Style.font.body

      Behavior on color {
        ColorAnimation { duration: 160 }
      }
    }
  }
}
