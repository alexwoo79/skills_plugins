// Pure logic for the Proxy (proxyctl) bar widget: reading the proxyctl-managed
// session env file, shaping the bar label and tooltip, and parsing the curl
// health probe. Kept out of the QML so the string handling stays testable.
//
// Shared by two runtimes:
//   - BarWidget.qml imports it as a QML JS module;
//   - test-model.js requires it from Node (module.exports guard at the bottom).

var DEFAULT_ADDRESS = "10.10.10.111:7890"
var DEFAULT_CHECK_INTERVAL_SECONDS = 60
var MIN_CHECK_INTERVAL_SECONDS = 30
var MAX_CHECK_INTERVAL_SECONDS = 3600
var PROBE_TIMEOUT_SECONDS = 8
var PROBE_URL = "https://www.gstatic.com/generate_204"
var PROBE_EXIT_IP_URL = "https://api.ipify.org"
var GLYPH = "\uf0ac" // globe, covered by JetBrainsMono Nerd Font

// ---------------------------------------------------------------------------
// Addresses
// ---------------------------------------------------------------------------

// "http://10.0.0.5:7890/" -> "10.0.0.5:7890" (drops scheme, userinfo, path).
function endpointFromURL(url) {
  var text = String(url == null ? "" : url).trim()
  if (text === "") return ""
  text = text.replace(/^[A-Za-z][A-Za-z0-9+.\-]*:\/\//, "")
  var at = text.lastIndexOf("@")
  if (at >= 0) text = text.substring(at + 1)
  var slash = text.indexOf("/")
  if (slash >= 0) text = text.substring(0, slash)
  return text.trim()
}

function isAddress(value) {
  return /^[A-Za-z0-9._\-]+:[0-9]{1,5}$/.test(String(value == null ? "" : value).trim())
}

// Never hand a malformed address to proxyctl; fall back to the known good one.
function normalizeAddress(value, fallback) {
  var text = String(value == null ? "" : value).trim()
  return isAddress(text) ? text : String(fallback == null ? "" : fallback)
}

function checkIntervalSeconds(value) {
  var seconds = parseInt(value, 10)
  if (isNaN(seconds)) return DEFAULT_CHECK_INTERVAL_SECONDS
  if (seconds < MIN_CHECK_INTERVAL_SECONDS) return MIN_CHECK_INTERVAL_SECONDS
  if (seconds > MAX_CHECK_INTERVAL_SECONDS) return MAX_CHECK_INTERVAL_SECONDS
  return seconds
}

// ---------------------------------------------------------------------------
// proxyctl session env file
// ---------------------------------------------------------------------------

// The file proxyctl writes while the proxy is on
// (~/.config/environment.d/proxy.conf). Missing or empty text means direct:
// `proxyctl off` deletes the file, `proxyctl restore` writes it back.
function parseEnvFile(text) {
  var state = { on: false, endpoint: "", entries: 0 }
  var lines = String(text == null ? "" : text).split("\n")
  for (var i = 0; i < lines.length; i++) {
    var line = lines[i].trim()
    if (line === "" || line.charAt(0) === "#") continue
    var eq = line.indexOf("=")
    if (eq < 0) continue
    var key = line.substring(0, eq).trim().toLowerCase()
    if (key !== "http_proxy" && key !== "https_proxy") continue
    var endpoint = endpointFromURL(line.substring(eq + 1))
    if (endpoint === "") continue
    state.entries++
    if (state.endpoint === "") {
      state.on = true
      state.endpoint = endpoint
    }
  }
  return state
}

// ---------------------------------------------------------------------------
// Health probe
// ---------------------------------------------------------------------------

// curl -w '%{http_code} %{time_total}' prints "<code> <seconds>" on stdout;
// curl's own message lands on stderr and is surfaced only on failure.
function parseProbe(exitCode, output, errorOutput) {
  var stdout = String(output == null ? "" : output).trim()
  var stderr = String(errorOutput == null ? "" : errorOutput).trim()
  if (exitCode !== 0) {
    return {
      ok: false,
      ms: -1,
      code: "",
      error: stderr !== "" ? stderr : "curl exit " + exitCode,
      at: 0
    }
  }
  var parts = stdout.split(/\s+/)
  var code = parts.length > 0 ? parts[0] : ""
  var seconds = parseFloat(parts.length > 1 ? parts[1] : "")
  var ok = code === "204" || code === "200"
  return {
    ok: ok,
    ms: isNaN(seconds) ? -1 : Math.round(seconds * 1000),
    code: code,
    error: ok ? "" : "HTTP " + (code === "" ? "?" : code),
    at: 0
  }
}

// Explicit -x plus an empty --noproxy: the probe must go through the proxy
// even if this shell inherited a no_proxy list.
function probeArgs(endpoint) {
  return [
    "curl", "-sS",
    "-m", String(PROBE_TIMEOUT_SECONDS),
    "--noproxy", "",
    "-x", "http://" + endpoint,
    "-o", "/dev/null",
    "-w", "%{http_code} %{time_total}",
    PROBE_URL
  ]
}

// Same shape, different question: which address does the world see when
// traffic leaves through this proxy?
function exitIpArgs(endpoint) {
  return [
    "curl", "-sS",
    "-m", String(PROBE_TIMEOUT_SECONDS),
    "--noproxy", "",
    "-x", "http://" + endpoint,
    PROBE_EXIT_IP_URL
  ]
}

// The exit-IP endpoint answers with a bare address; anything else (an error
// page, an empty body) is not an address and must not reach the panel.
function cleanExitIp(text) {
  var value = String(text == null ? "" : text).trim()
  if (value === "" || value.length > 45) return ""
  if (!/^[0-9a-fA-F.:]+$/.test(value)) return ""
  if (value.indexOf(":") < 0 && !/^(\d{1,3}\.){3}\d{1,3}$/.test(value)) return ""
  return value
}

// Empty result means "refused to run" (no binary or a bad address); the
// caller reports that instead of invoking proxyctl with junk.
function toggleArgs(proxyOn, endpoint, proxyctl) {
  var binary = String(proxyctl == null ? "" : proxyctl).trim()
  if (binary === "") return []
  if (proxyOn) return [binary, "off"]
  var address = String(endpoint == null ? "" : endpoint).trim()
  if (!isAddress(address)) return []
  return [binary, "on", "--address", address]
}

// TUN is network-layer: one upstream for every app, so the same endpoint the
// panel shows is the one it hands to mihomo.
function tunArgs(on, endpoint, proxyctl) {
  var binary = String(proxyctl == null ? "" : proxyctl).trim()
  if (binary === "") return []
  if (!on) return [binary, "tun", "off"]
  var address = String(endpoint == null ? "" : endpoint).trim()
  if (!isAddress(address)) return []
  return [binary, "tun", "on", "--address", address]
}

function statusArgs(proxyctl) {
  var binary = String(proxyctl == null ? "" : proxyctl).trim()
  return binary === "" ? [] : [binary, "status"]
}

function toolsArgs(proxyctl) {
  var binary = String(proxyctl == null ? "" : proxyctl).trim()
  return binary === "" ? [] : [binary, "tools", "list"]
}

// ---------------------------------------------------------------------------
// proxyctl status / tools list
// ---------------------------------------------------------------------------

// proxyctl reports in Chinese with a stable shape; these parsers read the
// layer facts the panel shows. Anything unrecognised stays "unknown" rather
// than guessing — a wrong layer row is worse than a missing one.
function parseStatus(text) {
  var output = String(text == null ? "" : text)
  var state = {
    desktop: { on: false, endpoint: "" },
    git: { on: false, endpoint: "" },
    sessionEnv: false,
    browser: { on: false, endpoint: "" },
    tun: { service: "unknown", iface: "", config: "" }
  }

  var match = output.match(/HTTP\s+代理已启用:\s*(\S+)/)
  if (match) {
    state.desktop.on = true
    state.desktop.endpoint = endpointFromURL(match[1])
  }

  match = output.match(/http\.proxy\s*=\s*(\S+)/)
  if (match && match[1] !== "<未设置>") {
    state.git.on = true
    state.git.endpoint = endpointFromURL(match[1])
  }

  // "会话环境文件:" is followed by either 不存在 or 存在: <path>. The
  // negative case has to be checked first: 不存在 contains 存在.
  var envLabel = output.indexOf("会话环境文件")
  if (envLabel >= 0) {
    var envTail = output.substring(envLabel, envLabel + 80)
    state.sessionEnv = envTail.indexOf("不存在") < 0 && envTail.indexOf("存在") >= 0
  }

  match = output.match(/--proxy-server=(\S+)/)
  if (match) {
    state.browser.on = true
    state.browser.endpoint = endpointFromURL(match[1])
  }

  var tunStart = output.indexOf("TUN/mihomo:")
  var tunText = tunStart >= 0 ? output.substring(tunStart) : output
  match = tunText.match(/mihomo-tun\.service:\s*(\S+)/)
  if (match) {
    if (match[1].indexOf("运行") >= 0) state.tun.service = "running"
    else if (match[1].indexOf("停止") >= 0) state.tun.service = "stopped"
  }
  match = tunText.match(/虚拟网卡 Meta:\s*(\S+)/)
  if (match && match[1] !== "未创建") state.tun.iface = match[1]
  match = tunText.match(/mihomo 配置:\s*(\S+)/)
  if (match && match[1] !== "不存在") state.tun.config = match[1]

  return state
}

// "npm      /home/u/.npmrc" starts a tool block; any "= http://…" line inside
// it means that tool carries a proxy.
function parseTools(text) {
  var lines = String(text == null ? "" : text).split("\n")
  var total = 0
  var configured = 0
  var currentHasProxy = false
  for (var i = 0; i < lines.length; i++) {
    var line = lines[i]
    if (/^[A-Za-z][A-Za-z0-9_-]*\s+\//.test(line)) {
      if (total > 0 && currentHasProxy) configured++
      total++
      currentHasProxy = false
      continue
    }
    if (/=\s*(https?|socks5h?):\/\//.test(line)) currentHasProxy = true
  }
  if (total > 0 && currentHasProxy) configured++
  return { configured: configured, total: total }
}

// Rows for the panel's layer section: one per thing proxyctl writes, so a
// half-applied state is visible without reading proxyctl's report by hand.
function layerRows(state, tools) {
  var rows = []
  if (!state) return rows
  rows.push({
    label: "Desktop proxy",
    value: state.desktop.on ? state.desktop.endpoint : "not set",
    ok: state.desktop.on
  })
  rows.push({
    label: "Session env",
    value: state.sessionEnv ? "proxy.conf present" : "missing",
    ok: state.sessionEnv
  })
  rows.push({
    label: "Browser flags",
    value: state.browser.on ? state.browser.endpoint : "not set",
    ok: state.browser.on
  })
  rows.push({
    label: "Git",
    value: state.git.on ? state.git.endpoint : "not set",
    ok: state.git.on
  })
  if (tools && tools.total > 0) {
    rows.push({
      label: "Dev tools",
      value: tools.configured + "/" + tools.total + " configured",
      ok: tools.configured > 0
    })
  }
  rows.push({
    label: "TUN",
    value: state.tun.service === "running"
      ? ("running" + (state.tun.iface !== "" ? " (" + state.tun.iface + ")" : ""))
      : state.tun.service,
    ok: state.tun.service === "running"
  })
  return rows
}

// ---------------------------------------------------------------------------
// Display
// ---------------------------------------------------------------------------

function healthOk(health) {
  return !!health && health.ok === true
}

function healthFailed(health) {
  return !!health && health.ok !== true
}

function barLabel(proxyOn, busy, health) {
  if (!proxyOn) return "off"
  if (busy) return "\u2026"
  if (!health) return "on"
  if (health.ok) return health.ms >= 0 ? "on " + health.ms + "ms" : "on"
  return "on !"
}

function clockTime(epochMs) {
  var date = new Date(Number(epochMs))
  if (isNaN(date.getTime())) return ""
  function pad(value) {
    return (value < 10 ? "0" : "") + value
  }
  return pad(date.getHours()) + ":" + pad(date.getMinutes()) + ":" + pad(date.getSeconds())
}

// Panel line for the probe result. "on" and "working" are different facts, so
// the panel states the measured latency rather than a bare checkmark.
function healthLine(proxyOn, busy, health, probeEnabled) {
  if (!proxyOn) return "proxy is off"
  if (busy) return "checking\u2026"
  if (probeEnabled === false) return "health check disabled in settings"
  if (!health) return "not checked yet"
  if (health.ok) {
    var detail = health.ms >= 0 ? health.ms + "ms" : "reachable"
    var at = clockTime(health.at)
    return at === "" ? detail : detail + " (checked " + at + ")"
  }
  return "failed: " + (health.error || "unknown error")
}

function exitIpLine(proxyOn, busy, exitIp) {
  if (!proxyOn) return "\u2014"
  if (busy) return "checking\u2026"
  return exitIp !== "" ? exitIp : "\u2014"
}

// Clipboard block for the panel's copy action: the same facts the panel
// shows, in a shape that reads well pasted into a chat or an issue.
function statusSummary(state) {
  var on = !!(state && state.on)
  var lines = ["Proxy (proxyctl): " + (on ? "on" : "off")]
  lines.push("Endpoint: " + String((state && (state.endpoint || state.configured)) || "\u2014"))
  if (on) {
    lines.push("Health: " + healthLine(true, !!(state && state.busy), state ? state.health : null,
      !state || state.probeEnabled !== false))
    lines.push("Exit IP: " + exitIpLine(true, !!(state && state.exitIpBusy), state ? state.exitIp : ""))
  }
  if (state && state.error) lines.push("Error: " + state.error)
  return lines.join("\n")
}

// One line: state, then the freshest detail the widget actually has, then
// what the mouse buttons do.
function tooltipText(state) {
  var on = !!(state && state.on)
  var endpoint = String((state && (state.endpoint || state.configured)) || "")
  var parts = [on ? "Proxy on " + endpoint : "Proxy off"]
  if (state && state.error) parts.push(state.error)
  if (state && state.tun === "running") parts.push("TUN on")
  if (on) {
    if (state.busy) {
      parts.push("checking\u2026")
    } else if (!state.health) {
      parts.push(state.probeEnabled === false ? "health check off" : "no check yet")
    } else if (state.health.ok) {
      var detail = state.health.ms >= 0 ? state.health.ms + "ms" : "reachable"
      var at = clockTime(state.health.at)
      parts.push(at === "" ? detail : detail + " (checked " + at + ")")
    } else {
      parts.push("last check failed: " + (state.health.error || "unknown"))
    }
    parts.push("left-click opens the panel")
  } else {
    parts.push("enable " + endpoint + " from the panel")
    parts.push("left-click opens the panel")
  }
  return parts.join(" \u00b7 ")
}

if (typeof module !== "undefined") {
  module.exports = {
    DEFAULT_ADDRESS: DEFAULT_ADDRESS,
    DEFAULT_CHECK_INTERVAL_SECONDS: DEFAULT_CHECK_INTERVAL_SECONDS,
    MIN_CHECK_INTERVAL_SECONDS: MIN_CHECK_INTERVAL_SECONDS,
    MAX_CHECK_INTERVAL_SECONDS: MAX_CHECK_INTERVAL_SECONDS,
    PROBE_TIMEOUT_SECONDS: PROBE_TIMEOUT_SECONDS,
    PROBE_URL: PROBE_URL,
    PROBE_EXIT_IP_URL: PROBE_EXIT_IP_URL,
    GLYPH: GLYPH,
    endpointFromURL: endpointFromURL,
    isAddress: isAddress,
    normalizeAddress: normalizeAddress,
    checkIntervalSeconds: checkIntervalSeconds,
    parseEnvFile: parseEnvFile,
    parseProbe: parseProbe,
    probeArgs: probeArgs,
    exitIpArgs: exitIpArgs,
    cleanExitIp: cleanExitIp,
    toggleArgs: toggleArgs,
    tunArgs: tunArgs,
    statusArgs: statusArgs,
    toolsArgs: toolsArgs,
    parseStatus: parseStatus,
    parseTools: parseTools,
    layerRows: layerRows,
    healthOk: healthOk,
    healthFailed: healthFailed,
    barLabel: barLabel,
    clockTime: clockTime,
    healthLine: healthLine,
    exitIpLine: exitIpLine,
    statusSummary: statusSummary,
    tooltipText: tooltipText
  }
}
