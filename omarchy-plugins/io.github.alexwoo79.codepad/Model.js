// Pure helpers for the codePad bar widget: no state, no Qt, no side effects.
// Kept out of the QML so the string handling stays testable.
//
// Shared by two runtimes:
//   - BarWidget.qml imports it as a QML JS module;
//   - test-model.js requires it from Node (module.exports guard at the bottom).

// The icon on the bar: the Font Awesome printer (U+F02F), which JetBrainsMono
// Nerd Font covers — the same range the proxy widget's globe comes from.
var GLYPH = "\uf02f";

// The formats codePad can write, in the order the panel shows them. The key is
// the --export-<key> flag and the file extension at once, which is what makes
// `codepad f.md --export-pdf out.pdf` the whole export contract.
var FORMATS = ["pdf", "html", "docx"];

var FORMAT_LABELS = {
  pdf: "PDF",
  html: "HTML",
  docx: "DOCX"
};

// `omarchy file select` prints one absolute path per line on stdout and nothing
// else, so the parse is a split — but a trailing newline and stray blank lines
// are normal and must not turn into files.
function parseFileSelect(text) {
  if (!text) return [];
  var out = [];
  var lines = String(text).split("\n");
  for (var i = 0; i < lines.length; i++) {
    var line = lines[i].trim();
    if (line !== "") out.push(line);
  }
  return out;
}

// Accept "pdf", "PDF" and ".pdf" alike, and refuse anything else rather than
// guessing: an unknown format would run `--export-<junk>` and fail downstream
// with a message that says nothing about the real mistake.
function normalizeFormat(value) {
  var name = String(value == null ? "" : value).trim().toLowerCase();
  if (name.charAt(0) === ".") name = name.slice(1);
  return FORMATS.indexOf(name) === -1 ? "" : name;
}

function formatLabel(name) {
  return FORMAT_LABELS[name] || String(name).toUpperCase();
}

// A configured or typed "~" is the user's home, not a literal directory named
// "~". Only a leading "~/" (or a bare "~") expands; a "~user" form is left
// alone rather than mis-expanded.
function expandHome(path, home) {
  var p = String(path == null ? "" : path).trim();
  if (p === "~") return home;
  if (p.indexOf("~/") === 0) return home + p.slice(1);
  return p;
}

function join(dir, name) {
  var d = String(dir == null ? "" : dir);
  if (d === "") return name;
  return d.charAt(d.length - 1) === "/" ? d + name : d + "/" + name;
}

function baseName(path) {
  var p = String(path == null ? "" : path);
  var slash = p.lastIndexOf("/");
  return slash >= 0 ? p.slice(slash + 1) : p;
}

function stripExtension(name) {
  var s = String(name == null ? "" : name);
  var dot = s.lastIndexOf(".");
  // A leading dot is a hidden file, not an extension: ".gitignore" keeps its
  // whole name so the export is not called ".pdf".
  return dot > 0 ? s.slice(0, dot) : s;
}

// The exported file is named after the first input: `note.md` -> `note.pdf`.
// codePad merges every input into one sheet, so one name is right — and the
// first file is the one the user put at the top of the list.
function outputName(inputs, ext) {
  var base = inputs && inputs.length > 0 ? stripExtension(baseName(inputs[0])) : "";
  if (base === "") base = "codepad";
  return base + "." + ext;
}

// The first non-empty line of stderr, for the one-line status. codePad's
// failures are single sentences; anything after that is noise in a bar panel.
function firstLine(text) {
  if (!text) return "";
  var lines = String(text).split("\n");
  for (var i = 0; i < lines.length; i++) {
    var line = lines[i].trim();
    if (line !== "") return line;
  }
  return "";
}

// What sits next to the glyph on the bar. Empty while idle: the glyph already
// says what the widget is, and the bar is short on room.
function barLabel(exporting, fileCount, failed) {
  if (exporting) return "导出中";
  if (failed) return "!";
  if (fileCount > 0) return String(fileCount);
  return "";
}

// The bar tooltip. The state line comes first because it is the thing that
// changed; the "what does clicking do" line is the standing reminder.
function tooltipText(state) {
  var lines = [];
  if (state.exporting) lines.push("正在排版…");
  else if (state.status && state.status !== "") lines.push(state.status);
  if (state.fileCount > 0) lines.push(state.fileCount + " 个文件已排队");
  lines.push("点击打开面板：加文件、打开 codePad、导出 PDF / HTML / DOCX");
  return lines.join("\n");
}

if (typeof module !== "undefined") {
  module.exports = {
    GLYPH: GLYPH,
    FORMATS: FORMATS,
    parseFileSelect: parseFileSelect,
    normalizeFormat: normalizeFormat,
    formatLabel: formatLabel,
    expandHome: expandHome,
    join: join,
    baseName: baseName,
    stripExtension: stripExtension,
    outputName: outputName,
    firstLine: firstLine,
    barLabel: barLabel,
    tooltipText: tooltipText
  }
}
