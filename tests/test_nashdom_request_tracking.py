"""Execute the passive page-request tracker without a browser or network."""
from contextlib import ExitStack, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from pipeline.downloaders.nashdom_operational_browser import TRACK_REQUESTS


@unittest.skipUnless(shutil.which("node"), "Tracker JavaScript checks require Node.js")
class RequestTrackingTests(unittest.TestCase):
    def test_fetch_headers_do_not_finish_requests_before_resource_body(self):
        script = r"""
const vm=require('node:vm'), assert=require('node:assert/strict');
const code=JSON.parse(process.argv[1]);
function environment(fetch) {
  let observed, now=100;
  class XHR {open() {} send() {} addEventListener() {}}
  const context={window:{fetch}, performance:{now:()=>now}, URL, WeakMap,
    location:new URL('https://xn--80az8a.xn--d1aqf.xn--p1ai/'), XMLHttpRequest:XHR,
    PerformanceObserver:class {constructor(callback) {observed=callback;} observe() {}}};
  vm.runInNewContext(code,context);
  return {context, tracker:context.window.__operationalHistoryRequests, time:value=>{now=value;},
    finish:(url,start=100)=>observed({getEntries:()=>[{name:url,initiatorType:'fetch',responseEnd:200,startTime:start}]})};
}
(async()=>{
  const url='https://xn--80az8a.xn--d1aqf.xn--p1ai/api/report?repYear=2020&repMonth=1';
  const response={status:200}, headers=environment(()=>Promise.resolve(response));
  assert.equal(await headers.context.window.fetch(url),response);
  assert.equal(headers.tracker.pending,1);
  assert.equal(headers.tracker.finished.length,0);
  headers.finish(url,99);
  assert.equal(headers.tracker.pending,1,'an older resource cannot finish a new request');
  headers.finish(url);
  assert.equal(headers.tracker.pending,0);
  assert.equal(headers.tracker.finished[0].status,200);

  let resolve;
  const cached=environment(()=>new Promise(done=>{resolve=done;}));
  const pending=cached.context.window.fetch(url);
  cached.finish(url);
  assert.equal(cached.tracker.pending,1,'body completion alone needs response status');
  resolve(response); await pending;
  assert.equal(cached.tracker.pending,0);

  const repeated=environment(()=>Promise.resolve(response));
  await repeated.context.window.fetch(url); await repeated.context.window.fetch(url);
  repeated.finish(url);
  assert.equal(repeated.tracker.pending,1,'one resource cannot finish two same-URL requests');
  repeated.finish(url);
  assert.equal(repeated.tracker.pending,0);
  assert.equal(repeated.tracker.finished.length,2);
  const overlapping=environment(()=>Promise.resolve(response));
  await overlapping.context.window.fetch(url);
  overlapping.time(150);
  await overlapping.context.window.fetch(url);
  overlapping.finish(url,151);
  assert.equal(overlapping.tracker.pending,1);
  overlapping.finish(url,101);
  assert.equal(overlapping.tracker.pending,0,'out-of-order same-URL bodies must both finish');
  console.log('fetch headers/body, cached completion and repeated URLs verified');
})().catch(error=>{console.error(error);process.exit(1);});
"""
        result = subprocess.run([shutil.which("node"), "-e", script, json.dumps(TRACK_REQUESTS)],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class DailyHistoryIsolationTests(unittest.TestCase):
    def test_optional_history_refresh_cannot_change_daily_success(self):
        import nashdom_checker as source
        from pipeline.downloaders import nashdom_operational_history as history
        for cached, enabled, daily_ok, failed in ((False, True, True, False), (True, False, True, False),
                                                  (True, True, False, False), (True, True, True, True),
                                                  (True, True, True, False)):
            with self.subTest(cached=cached, enabled=enabled, daily_ok=daily_ok, failed=failed), tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
                root = Path(folder)
                if cached:
                    (root / "construction_history.json").write_text("{}", encoding="utf-8")
                daily = root / "construction_operational_20261019.json"
                job = Mock(return_value=([daily] if daily_ok else [], daily_ok))
                refresh = Mock(side_effect=RuntimeError("source unavailable")) if failed else Mock(
                    return_value={"new_files": [], "complete": False})
                stack.enter_context(patch.object(source, "DOWNLOAD_DIR", root))
                stack.enter_context(patch.object(source, "load_state", return_value={}))
                stack.enter_context(patch.object(source, "save_state"))
                stack.enter_context(patch.object(source, "SOURCE_FUNCS", {"construction_operational": job}))
                stack.enter_context(patch.object(history, "download_history", refresh))
                stack.enter_context(patch.dict(os.environ, {"CONSTRUCTION_HISTORY_REFRESH": "1" if enabled else "0"}))
                stack.enter_context(redirect_stdout(StringIO()))
                files, success = source.run(["construction_operational"])
                self.assertEqual(success, daily_ok)
                self.assertEqual(files, [daily] if daily_ok else [])
                if cached and enabled and daily_ok:
                    refresh.assert_called_once_with(root, recent=1, max_periods=6)
                else:
                    refresh.assert_not_called()


if __name__ == "__main__":
    unittest.main()
