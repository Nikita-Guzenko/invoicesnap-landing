/* Analytics loader for invoicesnap.work — the one copy of the tracking setup (NIK-634, 12 Nov 2026).
   Every page loads only this file. It loads gtag.js (GA4 G-X4TCVJMVEP), PostHog and web-vitals itself,
   and only when consent allows it. The event list is data/events.json; tests/test_events.py fails on an
   event that the code sends but the list does not name.

   Consent (contract shared with app.invoicesnap.work, invoicesnap-expo src/lib/consent.web.ts):
   - needsConsent(): the browser time zone is in Europe (EEA, UK, CH and their islands; over-inclusive
     on purpose). Unknown time zone counts as Europe.
   - Cookie isnap_consent=granted|denied on .invoicesnap.work (180 days), so one choice covers both hosts.
   - Europe and no choice: the banner shows and NO tracker loads (no gtag.js, no PostHog, no request to
     Google or PostHog) until "Accept". "Decline" keeps them off. Elsewhere: trackers load at once.
   - Google Consent Mode v2 defaults go first on every load: denied in EEA+GB+CH, analytics granted
     elsewhere, ad_* denied everywhere (no ads), then an update with the stored choice.
   - "Cookie settings" in every footer reopens the banner.
   Other rules:
   - Only the live host sends: local builds and gate_browser runs on 127.0.0.1 load no tracker and show
     no banner. Both hosts share the _ga cookie on invoicesnap.work: no cross-domain linker.
   - Internal traffic: ?internal=1 marks this browser (cookie is_internal on .invoicesnap.work, one year,
     read by both hosts); ?internal=0 clears it. GA4 hits carry traffic_type=internal.
   - PostHog session replay masks every input, the tool form, the live paper, the homepage calculator
     and the invoice preview (MASK); those elements also get ph-no-capture, so autocapture skips them.
   - Never send a form value: only actions, page paths, file types, link domains. Tests: GA4 drops the
     HeadlessChrome user agent as a bot, so a Playwright check needs a normal user agent; PostHog drops
     browsers with navigator.webdriver (Playwright sets it), so the test must hide that flag. */
(function () {
  var ID = 'G-X4TCVJMVEP';
  // PostHog project key: public, ingest-only, the same project as the iOS app (EAS EXPO_PUBLIC_POSTHOG_KEY).
  var PH_KEY = 'phc_pzZ5rXsdxHPRg2Thu2FBwvkFATmPvxRru4LMf3ZtDNbp';
  var PH_HOST = 'https://us.i.posthog.com';
  var MASK = 'form, #paper, .paper-shell, .calc-card, #invoiceModal';
  var REGIONS = ['AT', 'BE', 'BG', 'HR', 'CY', 'CZ', 'DK', 'EE', 'FI', 'FR', 'DE', 'GR', 'HU', 'IE', 'IT', 'LV',
    'LT', 'LU', 'MT', 'NL', 'PL', 'PT', 'RO', 'SK', 'SI', 'ES', 'SE', 'IS', 'LI', 'NO', 'GB', 'CH'];
  var EU_ZONES = ['Atlantic/Canary', 'Atlantic/Madeira', 'Atlantic/Azores', 'Atlantic/Reykjavik', 'Atlantic/Faroe',
    'Arctic/Longyearbyen', 'Asia/Nicosia', 'Asia/Famagusta'];
  var live = location.hostname === 'invoicesnap.work';

  window.dataLayer = window.dataLayer || [];
  // After "Decline" no event of ours enters the queue (gtag ignores ga-disable after config).
  var declined = false;
  window.gtag = function () { if (declined && arguments[0] === 'event') return; window.dataLayer.push(arguments); };

  // tool_used: once per page view, on the first real action with a free tool (key event in GA4).
  var used = false;
  window.trackToolUsed = function (action, fileFormat) {
    if (used) return;
    used = true;
    var p = { tool_action: action, tool_page: location.pathname };
    if (fileFormat) p.file_format = fileFormat;
    window.gtag('event', 'tool_used', p);
  };

  // Link clicks on every page. Only the link's domain leaves the page, never a full URL.
  document.addEventListener('click', function (event) {
    var link = event.target && event.target.closest && event.target.closest('a[href]');
    if (!link || !/^https?:/.test(link.href)) return;
    var host = link.hostname;
    var base = { event_category: 'conversion', tool_page: location.pathname };
    if (host === 'app.invoicesnap.work') window.gtag('event', 'click_web_app', base);
    else if (host === 'apps.apple.com') window.gtag('event', 'click_app_store', base);
    else if (host !== location.hostname && host !== 'invoicesnap.work') {
      window.gtag('event', 'click_outbound', { link_domain: host, tool_page: location.pathname });
    }
  }, true);

  // scroll_depth: 25 / 50 / 75 / 90 %, each once per page view.
  var marks = [25, 50, 75, 90];
  function onScroll() {
    var doc = document.documentElement;
    var depth = (window.scrollY + window.innerHeight) / Math.max(doc.scrollHeight, 1) * 100;
    while (marks.length && depth >= marks[0]) {
      window.gtag('event', 'scroll_depth', { percent_scrolled: marks.shift(), tool_page: location.pathname });
    }
    if (!marks.length) window.removeEventListener('scroll', onScroll);
  }
  window.addEventListener('scroll', onScroll, { passive: true });

  if (!live) return;

  function readCookie(name) {
    var m = document.cookie.match(new RegExp('(?:^|;\\s*)' + name + '=([^;]*)'));
    return m ? m[1] : '';
  }
  function writeCookie(name, value, maxAge) {
    document.cookie = name + '=' + value + ';domain=invoicesnap.work;path=/;secure;samesite=lax;max-age=' + maxAge;
  }
  var internal = false;
  try {
    var flag = new URLSearchParams(location.search).get('internal');
    if (flag === '1') writeCookie('is_internal', '1', 31536000);
    if (flag === '0') writeCookie('is_internal', '1', 0);
    internal = readCookie('is_internal') === '1';
  } catch (e) { /* cookies off: count the visit as a normal visit */ }

  function needsConsent() {
    var zone = '';
    try { zone = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (e) { /* unknown */ }
    return !zone || zone.indexOf('Europe/') === 0 || EU_ZONES.indexOf(zone) >= 0;
  }
  var choice = '';
  try { choice = readCookie('isnap_consent'); } catch (e) { /* cookies off */ }
  if (choice !== 'granted' && choice !== 'denied') choice = '';
  declined = choice === 'denied';

  var denied = { analytics_storage: 'denied', ad_storage: 'denied', ad_user_data: 'denied', ad_personalization: 'denied' };
  window.gtag('consent', 'default', Object.assign({ region: REGIONS }, denied));
  window.gtag('consent', 'default', Object.assign({}, denied, { analytics_storage: 'granted' }));
  if (choice) window.gtag('consent', 'update', { analytics_storage: choice });

  function whenReady(fn) {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', fn);
    else fn();
  }
  function addScript(src, onload) {
    var s = document.createElement('script');
    s.async = true;
    s.src = src;
    if (onload) s.onload = onload;
    document.head.appendChild(s);
  }

  var started = false;
  function start() {
    if (started) return;
    started = true;
    var config = { cookie_flags: 'SameSite=None;Secure' };
    if (internal) config.traffic_type = 'internal';
    window.gtag('js', new Date());
    window.gtag('config', ID, config);
    addScript('https://www.googletagmanager.com/gtag/js?id=' + ID);
    // Web vitals of real visits to GA4; CLS is sent x1000 so metric_value is a whole number.
    addScript('/assets/web-vitals.iife.js', function () {
      var wv = window.webVitals;
      if (!wv) return;
      function report(m) {
        window.gtag('event', 'web_vital', {
          metric_name: m.name, metric_rating: m.rating, tool_page: location.pathname,
          metric_value: Math.round(m.name === 'CLS' ? m.value * 1000 : m.value)
        });
      }
      ['onLCP', 'onINP', 'onCLS', 'onFCP', 'onTTFB'].forEach(function (fn) { if (wv[fn]) wv[fn](report); });
    });
    // PostHog starts after the body exists, so the mask classes are on the page before the recorder.
    // maskTextSelector also matches descendants (rrweb checks ancestors), so typed text never leaves.
    whenReady(function () {
      var hidden = document.querySelectorAll(MASK);
      for (var i = 0; i < hidden.length; i++) hidden[i].classList.add('ph-no-capture');
      addScript('https://us-assets.i.posthog.com/static/array.js', function () {
        if (!window.posthog || !window.posthog.init) return;
        window.posthog.init(PH_KEY, {
          api_host: PH_HOST, ui_host: 'https://us.posthog.com', person_profiles: 'identified_only',
          autocapture: true, capture_pageview: true, capture_pageleave: true, enable_heatmaps: true,
          capture_performance: { web_vitals: true }, mask_all_element_attributes: true,
          cross_subdomain_cookie: true,
          // The project records console logs by default; console text can hold page data, so keep it off.
          enable_recording_console_log: false,
          session_recording: { maskAllInputs: true, maskTextSelector: MASK },
          loaded: function (ph) { if (internal) ph.register({ traffic_type: 'internal' }); }
        });
      });
    });
  }

  // ---- consent banner (39 locales: the tags in data/locales.json; tests/test_events.py checks them) ----
  var T = {
    en: ['We use cookies to measure how the site is used. Accept or decline analytics cookies.', 'Accept', 'Decline', 'Privacy', 'Cookie settings'],
    ar: ['نستخدم ملفات تعريف الارتباط لقياس كيفية استخدام الموقع. اقبل ملفات تعريف الارتباط التحليلية أو ارفضها.', 'قبول', 'رفض', 'الخصوصية', 'إعدادات ملفات تعريف الارتباط'],
    ca: ['Fem servir galetes per mesurar com s’utilitza el lloc. Accepta o rebutja les galetes d’analítica.', 'Accepta', 'Rebutja', 'Privadesa', 'Configuració de galetes'],
    cs: ['Používáme soubory cookie k měření používání webu. Přijměte nebo odmítněte analytické cookies.', 'Přijmout', 'Odmítnout', 'Soukromí', 'Nastavení cookies'],
    da: ['Vi bruger cookies til at måle, hvordan siden bruges. Accepter eller afvis analysecookies.', 'Accepter', 'Afvis', 'Privatliv', 'Cookieindstillinger'],
    de: ['Wir verwenden Cookies, um zu messen, wie die Website genutzt wird. Analyse-Cookies akzeptieren oder ablehnen.', 'Akzeptieren', 'Ablehnen', 'Datenschutz', 'Cookie-Einstellungen'],
    el: ['Χρησιμοποιούμε cookies για να μετράμε πώς χρησιμοποιείται ο ιστότοπος. Αποδεχτείτε ή απορρίψτε τα cookies ανάλυσης.', 'Αποδοχή', 'Απόρριψη', 'Απόρρητο', 'Ρυθμίσεις cookies'],
    es: ['Usamos cookies para medir cómo se usa el sitio. Acepta o rechaza las cookies de análisis.', 'Aceptar', 'Rechazar', 'Privacidad', 'Configuración de cookies'],
    fi: ['Käytämme evästeitä sivuston käytön mittaamiseen. Hyväksy tai hylkää analytiikkaevästeet.', 'Hyväksy', 'Hylkää', 'Tietosuoja', 'Evästeasetukset'],
    fr: ['Nous utilisons des cookies pour mesurer l’utilisation du site. Acceptez ou refusez les cookies d’analyse.', 'Accepter', 'Refuser', 'Confidentialité', 'Paramètres des cookies'],
    he: ['אנו משתמשים בעוגיות כדי למדוד את השימוש באתר. אשרו או דחו עוגיות ניתוח.', 'אישור', 'דחייה', 'פרטיות', 'הגדרות עוגיות'],
    hi: ['साइट के उपयोग को मापने के लिए हम कुकीज़ का उपयोग करते हैं। एनालिटिक्स कुकीज़ स्वीकार या अस्वीकार करें।', 'स्वीकार करें', 'अस्वीकार करें', 'गोपनीयता', 'कुकी सेटिंग्स'],
    hr: ['Koristimo kolačiće kako bismo mjerili korištenje stranice. Prihvatite ili odbijte analitičke kolačiće.', 'Prihvati', 'Odbij', 'Privatnost', 'Postavke kolačića'],
    hu: ['Sütiket használunk a webhely használatának méréséhez. Fogadja el vagy utasítsa el az analitikai sütiket.', 'Elfogadom', 'Elutasítom', 'Adatvédelem', 'Sütibeállítások'],
    id: ['Kami menggunakan cookie untuk mengukur cara situs digunakan. Terima atau tolak cookie analitik.', 'Terima', 'Tolak', 'Privasi', 'Pengaturan cookie'],
    it: ['Usiamo i cookie per misurare come viene usato il sito. Accetta o rifiuta i cookie di analisi.', 'Accetta', 'Rifiuta', 'Privacy', 'Impostazioni cookie'],
    ja: ['サイトの利用状況を測定するために Cookie を使用します。分析用 Cookie を許可または拒否してください。', '許可する', '拒否する', 'プライバシー', 'Cookie 設定'],
    ko: ['사이트 이용 방식을 측정하기 위해 쿠키를 사용합니다. 분석 쿠키를 수락하거나 거부하세요.', '수락', '거부', '개인정보', '쿠키 설정'],
    ms: ['Kami menggunakan kuki untuk mengukur cara laman ini digunakan. Terima atau tolak kuki analitik.', 'Terima', 'Tolak', 'Privasi', 'Tetapan kuki'],
    nb: ['Vi bruker informasjonskapsler for å måle hvordan nettstedet brukes. Godta eller avvis analysekapsler.', 'Godta', 'Avvis', 'Personvern', 'Innstillinger for informasjonskapsler'],
    nl: ['We gebruiken cookies om te meten hoe de site wordt gebruikt. Accepteer of weiger analytische cookies.', 'Accepteren', 'Weigeren', 'Privacy', 'Cookie-instellingen'],
    pl: ['Używamy plików cookie, aby mierzyć sposób korzystania ze strony. Zaakceptuj lub odrzuć analityczne pliki cookie.', 'Akceptuję', 'Odrzucam', 'Prywatność', 'Ustawienia plików cookie'],
    'pt-BR': ['Usamos cookies para medir como o site é usado. Aceite ou recuse os cookies de análise.', 'Aceitar', 'Recusar', 'Privacidade', 'Configurações de cookies'],
    'pt-PT': ['Utilizamos cookies para medir a forma como o site é utilizado. Aceite ou recuse os cookies de análise.', 'Aceitar', 'Recusar', 'Privacidade', 'Definições de cookies'],
    ro: ['Folosim cookie-uri pentru a măsura modul în care este folosit site-ul. Acceptați sau refuzați cookie-urile de analiză.', 'Accept', 'Refuz', 'Confidențialitate', 'Setări cookie'],
    ru: ['Мы используем файлы cookie, чтобы измерять, как используется сайт. Примите или отклоните аналитические cookie.', 'Принять', 'Отклонить', 'Конфиденциальность', 'Настройки cookie'],
    sk: ['Používame súbory cookie na meranie používania stránky. Prijmite alebo odmietnite analytické cookies.', 'Prijať', 'Odmietnuť', 'Súkromie', 'Nastavenia cookies'],
    sv: ['Vi använder cookies för att mäta hur webbplatsen används. Godkänn eller avvisa analyscookies.', 'Godkänn', 'Avvisa', 'Integritet', 'Cookie-inställningar'],
    th: ['เราใช้คุกกี้เพื่อวัดการใช้งานเว็บไซต์ ยอมรับหรือปฏิเสธคุกกี้เพื่อการวิเคราะห์', 'ยอมรับ', 'ปฏิเสธ', 'ความเป็นส่วนตัว', 'การตั้งค่าคุกกี้'],
    tr: ['Sitenin nasıl kullanıldığını ölçmek için çerezler kullanıyoruz. Analiz çerezlerini kabul edin veya reddedin.', 'Kabul et', 'Reddet', 'Gizlilik', 'Çerez ayarları'],
    uk: ['Ми використовуємо файли cookie, щоб вимірювати, як використовується сайт. Прийміть або відхиліть аналітичні cookie.', 'Прийняти', 'Відхилити', 'Конфіденційність', 'Налаштування cookie'],
    vi: ['Chúng tôi dùng cookie để đo cách trang web được sử dụng. Chấp nhận hoặc từ chối cookie phân tích.', 'Chấp nhận', 'Từ chối', 'Quyền riêng tư', 'Cài đặt cookie'],
    'zh-Hans': ['我们使用 Cookie 来衡量网站的使用情况。请接受或拒绝分析 Cookie。', '接受', '拒绝', '隐私', 'Cookie 设置'],
    'zh-Hant': ['我們使用 Cookie 來衡量網站的使用情況。請接受或拒絕分析 Cookie。', '接受', '拒絕', '隱私權', 'Cookie 設定']
  };
  var ALIAS = { 'en-AU': 'en', 'en-CA': 'en', 'en-GB': 'en', 'es-MX': 'es', 'fr-CA': 'fr' };
  function words() {
    var lang = document.documentElement.lang || 'en';
    return T[lang] || T[ALIAS[lang]] || T[lang.split('-')[0]] || T.en;
  }

  var banner = null;
  function choose(value) {
    try { writeCookie('isnap_consent', value, 15552000); } catch (e) { /* cookies off: this page only */ }
    choice = value;
    window.gtag('consent', 'update', { analytics_storage: value });
    if (banner) { banner.remove(); banner = null; }
    declined = value === 'denied';
    if (value === 'granted') start();
    else if (window.posthog && window.posthog.opt_out_capturing) window.posthog.opt_out_capturing();
    // A tracker that already runs (a US visitor who declines later) keeps only gtag's own cookieless
    // denied-mode pings for the rest of this page; the next page loads nothing.
  }
  function showBanner() {
    if (banner) return;
    var w = words();
    var style = document.createElement('style');
    style.textContent = '.isnap-consent{position:fixed;inset-inline:16px;bottom:16px;z-index:2147483000;max-width:560px;margin-inline:auto;' +
      'background:#fff;color:#0b1220;border:1px solid #dbe4f0;border-radius:14px;box-shadow:0 12px 40px #0b122026;padding:14px 16px;' +
      'font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}.isnap-consent p{margin:0 0 10px}' +
      '.isnap-consent a{color:#1749b2}.isnap-consent .row{display:flex;gap:10px}.isnap-consent button{flex:1;min-height:44px;' +
      'border-radius:10px;border:1px solid #2563eb;background:#fff;color:#1749b2;font-family:inherit;font-size:15px;font-weight:600;cursor:pointer}' +
      '.isnap-consent button:focus-visible{outline:3px solid #2563eb;outline-offset:2px}';
    document.head.appendChild(style);
    banner = document.createElement('div');
    banner.className = 'isnap-consent ph-no-capture';
    banner.setAttribute('role', 'region');
    banner.setAttribute('aria-label', w[4]);
    var p = document.createElement('p');
    p.textContent = w[0] + ' ';
    var a = document.createElement('a');
    a.href = '/privacy/';
    a.textContent = w[3];
    p.appendChild(a);
    var row = document.createElement('div');
    row.className = 'row';
    [['granted', w[1]], ['denied', w[2]]].forEach(function (b) {
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.textContent = b[1];
      btn.setAttribute('data-consent', b[0]);
      btn.addEventListener('click', function () { choose(b[0]); });
      row.appendChild(btn);
    });
    banner.appendChild(p);
    banner.appendChild(row);
    document.body.appendChild(banner);
  }
  function addSettingsLink() {
    var place = document.querySelector('footer nav, footer .footer-links') || document.querySelector('footer');
    if (!place) return;
    var a = document.createElement('a');
    a.href = '#cookie-settings';
    a.textContent = words()[4];
    a.setAttribute('data-cookie-settings', '');
    if (place.querySelector('.footer-link')) a.className = 'footer-link';
    a.addEventListener('click', function (e) { e.preventDefault(); showBanner(); });
    place.appendChild(a);
  }

  if (choice === 'granted' || (!choice && !needsConsent())) start();
  whenReady(function () {
    addSettingsLink();
    if (!choice && needsConsent()) showBanner();
  });
})();
