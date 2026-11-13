"""File chooser for Colab that trims FB-MultiPier .out files in the browser.

A .out file can be hundreds of MB, far too much to push through a notebook
upload.  The tools need only the input echo (nodes, elements, combinations)
and, per load combination, the pile-head, connector and column-base force rows.
The browser reads the file locally, keeps those lines (a few MB) and sends
only them to the notebook.  .in files are sent whole, byte for byte.
"""
from __future__ import annotations

import base64
import contextlib
import io
import os
import uuid
from typing import Callable, Dict, List

# The filter must keep exactly the lines outfile.read_model / read_results use.
# tests/test_fbmp_tools.py runs it under node and compares against the full file.
FILTER_JS = r"""
function makeOutFilter() {
  let stage2 = false, mode = '', need = false;
  return function (line) {
    if (!stage2) {
      if (line.indexOf('OUTPUT STAGE 2') >= 0) stage2 = true;
      return true;
    }
    if (line.indexOf('RESULTS FOR LOAD COMBINATION') >= 0) { mode = ''; return true; }
    if (line.startsWith(' -') || line.startsWith(' *') || line.startsWith(' _')) {
      if (/- Pile Number\s+\d+/.test(line)) { mode = 'pile'; need = true; return true; }
      if (line.indexOf('- Connector Beam Sections') >= 0) { mode = 'connector'; return true; }
      if (/- Column Sections For Column Number\s+\d+/.test(line)) { mode = 'column'; need = true; return true; }
      if (line.startsWith(' - ') || line.startsWith(' **')) {
        const was = mode; mode = ''; return was !== '';
      }
      return false;
    }
    if (mode === 'connector') return /^\s*\d/.test(line);
    if ((mode === 'pile' || mode === 'column') && need) {
      if (/^\s*\d+\s+\d+\s+\d+\s+\d+\s+-?\d/.test(line)) { need = false; return true; }
    }
    return false;
  };
}
"""

_PAGE = r"""
<div id="ROOT" style="font-family:sans-serif;font-size:13px;margin:4px 0">
  <div style="margin-bottom:6px">PROMPT</div>
  <input type="file" MULTIPLE accept="ACCEPT">
  <div class="st" style="margin-top:6px;color:#555"></div>
  <pre class="log" style="margin-top:6px;white-space:pre-wrap"></pre>
</div>
<script>
(function () {
FILTER
  const root = document.getElementById('ROOT');
  const input = root.querySelector('input');
  const st = root.querySelector('.st');
  const log = root.querySelector('.log');
  const call = (name, args) => google.colab.kernel.invokeFunction(name, args, {});
  const mb = n => (n / 1048576).toFixed(1) + ' MB';

  async function sendIn(file) {
    const buf = new Uint8Array(await file.arrayBuffer());
    let i = 0, idx = 0;
    while (i < buf.length || idx === 0) {
      const part = buf.subarray(i, i + 300000);
      let bin = '';
      for (let k = 0; k < part.length; k += 8192) bin += String.fromCharCode.apply(null, part.subarray(k, k + 8192));
      await call('CB_PART', ['TOKEN', file.name, idx++, btoa(bin), true]);
      i += 300000;
    }
  }

  async function sendOut(file) {
    const keep = makeOutFilter();
    const reader = file.stream().getReader();
    const dec = new TextDecoder('windows-1252');
    let carry = '', out = [], size = 0, idx = 0, read = 0, kept = 0;
    const flush = async () => {
      if (!out.length && idx > 0) return;
      await call('CB_PART', ['TOKEN', file.name, idx++, out.join('\n') + '\n', false]);
      kept += size; out = []; size = 0;
    };
    for (;;) {
      const r = await reader.read();
      if (r.done) break;
      read += r.value.length;
      const text = carry + dec.decode(r.value, { stream: true });
      const lines = text.split('\n');
      carry = lines.pop();
      for (let line of lines) {
        if (line.endsWith('\r')) line = line.slice(0, -1);
        if (keep(line)) { out.push(line); size += line.length + 1; }
      }
      if (size > 400000) await flush();
      st.textContent = file.name + ': read ' + mb(read) + ' of ' + mb(file.size) + ' in the browser, sending ' + mb(kept + size);
    }
    if (carry && keep(carry)) out.push(carry);
    await flush();
  }

  input.addEventListener('change', async () => {
    const files = Array.from(input.files);
    if (!files.length) return;
    input.disabled = true; log.textContent = '';
    try {
      const names = [];
      for (const file of files) {
        st.textContent = file.name + ' ...';
        if (/\.out$/i.test(file.name)) await sendOut(file); else await sendIn(file);
        names.push(file.name);
      }
      st.textContent = 'Reading ...';
      const res = await call('CB_DONE', ['TOKEN', names]);
      const data = res.data || {};
      log.textContent = (data['application/json'] && data['application/json'].text) || data['text/plain'] || '';
      st.textContent = 'Done. Choose files again to replace them.';
    } catch (err) {
      st.textContent = 'Upload failed: ' + err;
    }
    input.disabled = false; input.value = '';
  });
})();
</script>
"""


def in_colab() -> bool:
    try:
        import google.colab  # type: ignore  # noqa: F401
        return True
    except Exception:
        return False


def show_uploader(prompt: str, on_done: Callable[[Dict[str, str]], None], accept: str = ".in,.out",
                  multiple: bool = True, folder: str = "fbmp_uploads") -> None:
    """Show the chooser.  on_done receives {file name: saved path}; what it prints is shown under the chooser."""
    from google.colab import output  # type: ignore
    from IPython.display import HTML, JSON, display

    token = uuid.uuid4().hex[:10]
    os.makedirs(folder, exist_ok=True)
    paths: Dict[str, str] = {}

    def part(tok: str, name: str, index: int, payload: str, is_b64: bool):
        if tok != token:
            return
        path = os.path.join(folder, os.path.basename(name))
        paths[name] = path
        data = base64.b64decode(payload) if is_b64 else payload.encode("latin-1", "replace")
        with open(path, "wb" if index == 0 else "ab") as fh:
            fh.write(data)

    def done(tok: str, names: List[str]):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                on_done({n: paths[n] for n in names if n in paths})
            except Exception as exc:
                print(f"Could not read the files: {exc}")
        return JSON({"text": buf.getvalue()})

    output.register_callback(f"fbmp.part.{token}", part)
    output.register_callback(f"fbmp.done.{token}", done)
    page = (_PAGE.replace("FILTER", FILTER_JS).replace("ROOT", f"fbmp-up-{token}").replace("PROMPT", prompt)
            .replace("MULTIPLE", "multiple" if multiple else "").replace("ACCEPT", accept)
            .replace("CB_PART", f"fbmp.part.{token}").replace("CB_DONE", f"fbmp.done.{token}").replace("TOKEN", token))
    display(HTML(page))
