// Node test suite for Model.js — plain assert, no framework.
// Run: node test-model.js
const assert = require("assert")
const M = require("./Model.js")

const eq = (a, b, msg) => assert.strictEqual(a, b, msg)
const ok = (v, msg) => assert.ok(v, msg)
const has = (haystack, needle, msg) => assert.ok(String(haystack).includes(needle), msg)

// --- the file chooser's stdout ---------------------------------------------
// `omarchy file select` prints one absolute path per line; a trailing newline
// and blank lines are normal and must not become files.
assert.deepStrictEqual(M.parseFileSelect("/a.md\n/b c.cpp\n"), ["/a.md", "/b c.cpp"], "two paths")
assert.deepStrictEqual(M.parseFileSelect("/a.md\n\n/b.cpp\n\n"), ["/a.md", "/b.cpp"], "blank lines are skipped")
assert.deepStrictEqual(M.parseFileSelect(""), [], "empty stdout is no files")
assert.deepStrictEqual(M.parseFileSelect(null), [], "null stdout does not throw")
assert.deepStrictEqual(M.parseFileSelect("  /a.md  \n"), ["/a.md"], "surrounding space is trimmed")

// --- argument shape --------------------------------------------------------
// The key is both the flag and the extension, which is what makes
// `codepad f.md --export-pdf out.pdf` the whole export contract.
assert.deepStrictEqual(M.FORMATS, ["pdf", "html", "docx"], "the formats codePad can write")
eq(M.normalizeFormat("pdf"), "pdf", "lower case")
eq(M.normalizeFormat("PDF"), "pdf", "upper case is normalized")
eq(M.normalizeFormat(".docx"), "docx", "a leading dot is accepted")
eq(M.normalizeFormat(" docx "), "docx", "surrounding space is trimmed")
eq(M.normalizeFormat("exe"), "", "an unknown format is refused, not guessed")
eq(M.normalizeFormat(""), "", "empty format is refused")
eq(M.normalizeFormat(null), "", "null format does not throw")
eq(M.formatLabel("pdf"), "PDF", "pdf label")
eq(M.formatLabel("docx"), "DOCX", "docx label")

// --- paths -----------------------------------------------------------------
// A literal directory named "~" is never what the user meant; "~user" is and
// is left alone rather than mis-expanded.
eq(M.expandHome("~", "/home/alex"), "/home/alex", "bare tilde")
eq(M.expandHome("~/docs", "/home/alex"), "/home/alex/docs", "tilde slash")
eq(M.expandHome("~bob/x", "/home/alex"), "~bob/x", "~user is not expanded")
eq(M.expandHome("/abs", "/home/alex"), "/abs", "absolute paths pass through")
eq(M.expandHome("", "/home/alex"), "", "empty stays empty")
eq(M.expandHome(null, "/home/alex"), "", "null does not throw")
eq(M.join("/home/alex", "note.pdf"), "/home/alex/note.pdf", "join")
eq(M.join("/home/alex/", "note.pdf"), "/home/alex/note.pdf", "no double slash")
eq(M.join("", "note.pdf"), "note.pdf", "empty dir")
eq(M.baseName("/a/b c.cpp"), "b c.cpp", "spaces survive")
eq(M.baseName("plain.md"), "plain.md", "no directory")
eq(M.stripExtension("note.md"), "note", "extension is dropped")
eq(M.stripExtension(".gitignore"), ".gitignore", "a leading dot is a hidden file, not an extension")
eq(M.stripExtension("noext"), "noext", "no extension")

// --- the exported file name ------------------------------------------------
// codePad merges every input into one sheet, and the first file is the one the
// user put at the top of the list.
eq(M.outputName(["/a/note.md", "/a/b.cpp"], "pdf"), "note.pdf", "named after the first input")
eq(M.outputName(["/a/.gitignore"], "html"), ".gitignore.html", "a dotfile keeps its name")
eq(M.outputName([], "pdf"), "codepad.pdf", "no inputs fall back to a placeholder")
eq(M.outputName(null, "pdf"), "codepad.pdf", "null inputs do not throw")

// --- failure reporting -----------------------------------------------------
// codePad's failures are single sentences; a bar panel has room for one line.
eq(M.firstLine("boom"), "boom", "single line")
eq(M.firstLine("\n\n  boom  \nmore"), "boom", "leading blanks and trailing detail are dropped")
eq(M.firstLine(""), "", "empty stderr")
eq(M.firstLine(null), "", "null stderr does not throw")

// --- bar and tooltip copy --------------------------------------------------
eq(M.barLabel(false, 0, false), "", "idle shows the glyph alone")
eq(M.barLabel(false, 2, false), "2", "queued files show a count")
eq(M.barLabel(true, 2, false), "导出中", "a running export wins over the count")
eq(M.barLabel(false, 0, true), "!", "a failed export is marked on the bar")
eq(M.barLabel(true, 0, true), "导出中", "still running beats the previous failure")

ok(M.tooltipText({ exporting: false, fileCount: 0, status: "" }).includes("点击打开面板"),
  "the idle tooltip says what clicking does")
ok(M.tooltipText({ exporting: true, fileCount: 1, status: "" }).includes("正在排版"),
  "a running export is named")
ok(M.tooltipText({ exporting: false, fileCount: 3, status: "完成 → /tmp/a.pdf" })
  .includes("完成 → /tmp/a.pdf"), "the last result is reported")
ok(M.tooltipText({ exporting: false, fileCount: 3, status: "完成" }).includes("3 个文件已排队"),
  "queued files are counted")
ok(M.tooltipText({}).includes("点击打开面板"), "an empty state does not throw")

eq(M.GLYPH, "\uf02f", "the bar glyph is the Font Awesome printer")

console.log("Model.js: all assertions passed")
