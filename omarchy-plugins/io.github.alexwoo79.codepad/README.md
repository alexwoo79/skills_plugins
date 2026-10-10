# codePad — Omarchy bar widget

The codePad print-sheet builder (the `codepad` binary), reachable from the
Omarchy bar: collect the files to typeset, open them in the real codePad window,
and export a printable A4 sheet without leaving the shell.

This is a **launcher** plugin. codePad itself stays the Go/Qt app it already is;
the plugin drives it, the same way the Odin/workspace widgets drive `omarchy-*`
commands. QML cannot host another process's window, so the plugin deliberately
does not try: the preview, the themes and the print dialog stay in the codePad
window, and the panel covers the parts that are useful from the bar.

```
󰄴              idle, nothing queued
󰄴 2            two files queued
󰄴 导出中        a sheet is rendering right now
󰄴 !            the last export failed (urgent colour)
```

## Interactions

Clicking the bar widget only opens the panel. Nothing is launched and nothing
is overwritten from a stray click on the bar — every action lives inside the
panel and is labelled with what it does.

```
󰄴 codePad                                     2 个文件
────────────────────────────────────────────────────────
文件
  note.md                                            [×]
  demo.cpp                                           [×]
  [ 添加文件… ]                  [ 清空 ]
────────────────────────────────────────────────────────
导出
  输出目录   [ /home/alex                          ]
  [ PDF ]            [ HTML ]            [ DOCX ]
  状态       完成 → /home/alex/note.pdf
  [ 打开结果 ]
────────────────────────────────────────────────────────
  [ 打开 codePad（2 个文件） ]
```

- **添加文件…** opens the desktop file chooser
  (`omarchy file select --multiple`), so the dialog is the session's own.
- **PDF / HTML / DOCX** run the binary headless and report the exit code. They
  are disabled while the list is empty: a disabled control that explains itself
  beats an error after the click.
- **输出目录** is remembered in `shell.json` on Enter, like the proxy widget
  remembers its endpoint.
- **打开结果** appears only after a successful export.
- **打开 codePad** hands the queued files to the real window.

## Exports run headless

`codepad <files> --export-pdf <out>` renders the sheet, writes the file and
exits — 0 when the file is on disk, 1 when it is not. The widget runs it with
`QT_QPA_PLATFORM=offscreen` so no codePad window flashes for the second or so
the render takes. If a build cannot export without a screen, turn **Headless
export** off in the plugin settings and the only difference is the window
appearing during the render.

## Settings

Read from this widget's entry in `~/.config/omarchy/shell.json`:

| key | meaning |
|---|---|
| `binary` | the codePad executable; empty means `codepad` from `PATH` |
| `outputDir` | where exports land; empty means `$HOME` |
| `offscreenExport` | run exports headless (default `true`) |

`binary` is resolved through `PATH` when empty, so the AppImage install
(`~/.local/bin/codepad` → `~/Applications/codepad-x86_64.AppImage`) and a
distro package both work without being named.

## Install

```bash
./install.sh                  # into the right section of the bar
./install.sh --section center # or left / center
```

The script validates the manifest, copies the folder into
`~/.config/omarchy/plugins/io.github.alexwoo79.codepad/` and enables it. Saving
any file in that folder reloads the plugin live.

## IPC

```bash
omarchy-shell io.github.alexwoo79.codepad panel     # open/close the panel
omarchy-shell io.github.alexwoo79.codepad launch    # open codePad with the queue
omarchy-shell io.github.alexwoo79.codepad addFiles "/path/a.md
/path/b.cpp"                                        # queue files, newline-separated
omarchy-shell io.github.alexwoo79.codepad exportSheet pdf
omarchy-shell io.github.alexwoo79.codepad clearFiles
```

`launch` and `exportSheet` are the two that do something irreversible (open a
window, write a file), so they are only reachable from IPC or a labelled
control — never from a bar click.
