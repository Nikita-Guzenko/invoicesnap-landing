/* GA4 for invoicesnap.work — the one copy of the tag config (NIK-634, 12 Nov 2026).
   Every page loads gtag.js (async) and then this file. app.invoicesnap.work sends to the same
   measurement ID (invoicesnap-expo src/lib/ga4.web.ts). Both hosts share the _ga cookie on
   invoicesnap.work, so a visit from the site into the app stays one session: no cross-domain linker.
   Only the live host sends: local builds and gate_browser runs on 127.0.0.1 send nothing.
   Internal traffic: open any page with ?internal=1 to mark this browser (cookie on .invoicesnap.work,
   one year, read by both hosts); ?internal=0 clears it. Marked hits carry traffic_type=internal, and
   the GA4 data filter "Internal Traffic" drops them.
   tool_used: once per page view, on the first real action with a free tool (key event in GA4).
   Never send a form value to GA4: only the action, the page path and the file type. */
(function () {
  var ID = 'G-X4TCVJMVEP';
  window.dataLayer = window.dataLayer || [];
  window.gtag = function () { window.dataLayer.push(arguments); };
  var used = false;
  window.trackToolUsed = function (action, fileFormat) {
    if (used) return;
    used = true;
    var p = { tool_action: action, tool_page: location.pathname };
    if (fileFormat) p.file_format = fileFormat;
    window.gtag('event', 'tool_used', p);
  };
  if (location.hostname !== 'invoicesnap.work') return;
  var internal = false;
  try {
    var flag = new URLSearchParams(location.search).get('internal');
    var cookie = 'is_internal=1;domain=invoicesnap.work;path=/;secure;samesite=lax;max-age=';
    if (flag === '1') document.cookie = cookie + 31536000;
    if (flag === '0') document.cookie = cookie + 0;
    internal = /(?:^|;\s*)is_internal=1(?:;|$)/.test(document.cookie);
  } catch (e) { /* cookies off: count the visit as a normal visit */ }
  var config = { cookie_flags: 'SameSite=None;Secure' };
  if (internal) config.traffic_type = 'internal';
  window.gtag('js', new Date());
  window.gtag('config', ID, config);
})();
