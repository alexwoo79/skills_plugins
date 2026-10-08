// wk2pdf.js — HTML -> one continuous PDF page, using the WebKit already in macOS.
// No Chromium needed. A4 pagination is done afterwards by pdfsheet.py.
//
// usage: osascript -l JavaScript wk2pdf.js <in.html> <out.pdf> <viewportWidthPt>
// stdout: {"ok":true,"width":W,"height":H,"breaks":[y,...]}
ObjC.import('Foundation');
ObjC.import('AppKit');
ObjC.import('WebKit');

function run(argv) {
  if (argv.length < 3) {
    return '{"ok":false,"error":"usage: wk2pdf.js <in.html> <out.pdf> <widthPt>"}';
  }
  const inPath = argv[0];
  const outPath = argv[1];
  const vw = parseFloat(argv[2]);

  $.NSApplication.sharedApplication.setActivationPolicy(
    $.NSApplicationActivationPolicyProhibited
  );

  const web = $.WKWebView.alloc.initWithFrame($.NSMakeRect(0, 0, vw, 1000));
  const url = $.NSURL.fileURLWithPath($(inPath));
  web.loadFileURLAllowingReadAccessToURL(url, url.URLByDeletingLastPathComponent);

  const pump = (ms) =>
    $.NSRunLoop.currentRunLoop.runUntilDate($.NSDate.dateWithTimeIntervalSinceNow(ms));
  let waited = 0;
  while (web.loading && waited < 60) {
    pump(0.1);
    waited += 0.1;
  }
  for (let i = 0; i < 10; i++) pump(0.1); // let layout and webfonts settle

  const evalSync = function (js) {
    let value = null;
    let done = false;
    web.evaluateJavaScriptCompletionHandler(
      $(js),
      ObjC.block('v@@', function (v, err) {
        if (!err || err.isNil()) value = v;
        done = true;
      })
    );
    const end = Date.now() + 30000;
    while (!done && Date.now() < end) pump(0.05);
    return value && !value.isNil() ? ObjC.unwrap(value) : null;
  };

  // The HTML shows a white "page" with margins on screen; those margins are
  // added by pdfsheet.py for PDF, so strip the screen padding before capturing.
  evalSync(
    '(function(){' +
      'var s=document.createElement("style");' +
      's.textContent="@media screen {' +
      'html, body { background:#fff !important; padding:0 !important; }' +
      '.page { width:auto !important; margin:0 !important; padding:0 !important;' +
      ' box-shadow:none !important; background:#fff !important; } }";' +
      'document.head.appendChild(s);' +
    '})()'
  );

  // Break candidates: start/end of block elements, so pages split between blocks.
  const probe = evalSync(
    '(function(){' +
      'var ys=[];' +
      'var sel="tr,p,li,h1,h2,h3,h4,h5,h6,pre,blockquote,table,.file-head,.doc-head,.sheet";' +
      // A table must not be cut between its header row and the first data row,
      // so that boundary is not offered as a break position.
      'document.querySelectorAll("table").forEach(function(t){' +
      '  var rows=t.querySelectorAll("tr");' +
      '  if(rows.length>1){' +
      '    rows[0].setAttribute("data-ps-skip","1");' +
      '    rows[1].setAttribute("data-ps-skip-head","1");' +
      '  }' +
      '});' +
      'document.querySelectorAll(sel).forEach(function(el){' +
      '  var skip=el.getAttribute&&el.getAttribute("data-ps-skip");' +
      '  if(skip) return;' +
      '  var r=el.getBoundingClientRect();' +
      '  var headTail=el.getAttribute&&el.getAttribute("data-ps-skip-head");' +
      '  if(headTail){ ys.push(Math.round(r.bottom)); return; }' +
      '  ys.push(Math.round(r.top), Math.round(r.bottom));' +
      '});' +
      // Forced page breaks (CSS break-before: page) must be honoured exactly,
      // otherwise a multi-file sheet does not start each file on a new page.
      'var forced=[];' +
      'document.querySelectorAll("body *").forEach(function(el){' +
      '  var cs=window.getComputedStyle(el);' +
      '  var br=cs.breakBefore||cs.pageBreakBefore;' +
      '  if(br==="page"||br==="always"){' +
      '    var t=el.getBoundingClientRect().top;' +
      '    if(t>0.5) forced.push(Math.round(t));' +
      '  }' +
      '});' +
      'return JSON.stringify({' +
      '  h: Math.ceil(document.documentElement.scrollHeight),' +
      '  w: Math.ceil(document.documentElement.scrollWidth),' +
      '  forced: forced,' +
      '  breaks: ys' +
      '});' +
    '})()'
  );
  if (!probe) return '{"ok":false,"error":"cannot read layout from WKWebView"}';
  const layout = JSON.parse(probe);
  // scrollHeight never goes below the viewport height, so trim it to the last
  // block element; otherwise the sheet ends with a blank page.
  let contentH = Math.max(layout.h, 1);
  if (layout.breaks && layout.breaks.length) {
    const bottom = Math.max.apply(null, layout.breaks);
    contentH = Math.min(contentH, Math.max(bottom + 2, 1));
  }

  const cfg = $.WKPDFConfiguration.alloc.init;
  cfg.rect = $.NSMakeRect(0, 0, vw, contentH);
  let done = false;
  let data = null;
  let errMsg = null;
  web.createPDFWithConfigurationCompletionHandler(
    cfg,
    ObjC.block('v@@', function (d, err) {
      if (err && !err.isNil()) errMsg = ObjC.unwrap(err.localizedDescription);
      else data = d;
      done = true;
    })
  );
  waited = 0;
  while (!done && waited < 120) {
    pump(0.1);
    waited += 0.1;
  }
  if (errMsg) return JSON.stringify({ ok: false, error: 'WKWebView: ' + errMsg });
  if (!data || data.isNil()) return '{"ok":false,"error":"WKWebView returned no PDF data"}';
  if (!data.writeToFileAtomically($(outPath), true)) {
    return '{"ok":false,"error":"cannot write ' + outPath + '"}';
  }
  return JSON.stringify({
    ok: true,
    width: vw,
    height: contentH,
    breaks: layout.breaks,
    forced: layout.forced,
  });
}
