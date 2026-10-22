"""Observe page readiness without interacting with access-verification UI."""
from __future__ import annotations

from urllib.parse import urlsplit

from pipeline.mortgage_history import SOURCE_URL


VISIBILITY_SCRIPT = r"""
const visible = element => {
  if (!element) return false;
  const rect = element.getBoundingClientRect();
  if (!rect.width || !rect.height) return false;
  for (let node = element; node; node = node.parentElement) {
    const style = getComputedStyle(node);
    if (node.hidden || style.display === 'none' || style.visibility === 'hidden'
        || style.visibility === 'collapse' || Number(style.opacity) === 0) return false;
  }
  return true;
};
"""

ACCESS_STATE_SCRIPT = VISIBILITY_SCRIPT + r"""
const anyVisible = selector => [...document.querySelectorAll(selector)].some(visible);
const roots = '#captcha_root, #id_captcha_loaded, #id_captcha_frame_div';
const widgets = 'iframe, canvas, input, button, img';
const widgetVisible = [...document.querySelectorAll(roots)].some(root =>
  (root.matches(widgets) && visible(root)) || [...root.querySelectorAll(widgets)].some(visible));
const controlsReady = anyVisible('#base-header .main-filters')
  && !!document.querySelector('#base-header .main-filters input:checked')
  && anyVisible('#base-header .range-select')
  && document.querySelectorAll('#base-header .range-select input:checked').length >= 2;
const instruction = /подтвердите,?\s+что\s+вы\s+не\s+робот|подтвердите,?\s+что\s+вы\s+человек|verify\s+you\s+are\s+human/i;
const captchaVisible = widgetVisible
  || anyVisible('iframe[src*="captcha" i], input[name*="captcha" i]')
  || (!controlsReady && [...document.querySelectorAll('h1,h2,[role="alert"]')]
      .some(element => visible(element) && instruction.test(element.innerText || '')));
const denial = /доступ\s+заблокирован|access\s+denied|\bforbidden\b/i;
const denied = denial.test(document.title) || [...document.querySelectorAll('h1,h2,[role="alert"]')]
  .some(element => visible(element) && denial.test(element.innerText || ''));
let expectedPath = false;
try { expectedPath = decodeURIComponent(location.pathname) === arguments[1]; } catch {}
return {sameOrigin:location.origin === arguments[0], expectedPath,
  readyState:document.readyState, controlsReady, captchaVisible, denied,
  loaderVisible:anyVisible('js-challenge-loader, #id_spinner, .spinner-loader'),
  title:document.title.slice(0,160)};
"""


def read_access_state(driver, *, expected_url=SOURCE_URL):
    """Only visible widgets or explicit denial establish an access failure.

    Empty/hidden verification containers and loader scripts are not evidence of
    a human challenge. No cookies, response headers, query strings or HTML leave
    the browser through this observation.
    """
    expected = urlsplit(expected_url)
    host = expected.hostname.encode("idna").decode("ascii")
    origin = f"{expected.scheme}://{host}" + (f":{expected.port}" if expected.port else "")
    state = driver.execute_script(ACCESS_STATE_SCRIPT, origin, expected.path)
    flags = ("sameOrigin", "expectedPath", "controlsReady", "captchaVisible", "denied", "loaderVisible")
    if (not isinstance(state, dict) or any(type(state.get(key)) is not bool for key in flags)
            or state.get("readyState") not in {"loading", "interactive", "complete"}
            or not isinstance(state.get("title"), str)):
        raise RuntimeError("Mortgage page returned an invalid readiness observation")
    result = {key: state[key] for key in (*flags, "readyState")}
    result["title"] = state["title"][:160]
    result["ready"] = (result["sameOrigin"] and result["expectedPath"] and result["controlsReady"]
                       and result["readyState"] != "loading" and not result["loaderVisible"]
                       and not result["captchaVisible"] and not result["denied"])
    return result
