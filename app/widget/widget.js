/*
 * Embeddable chat widget.
 *
 * Add to any page:
 *   <script src="https://<service>/widget.js" data-title="Assistant"></script>
 *
 * Configuration is read from data-* attributes on the script tag; the API
 * base is the script's own origin. Styles live in a Shadow DOM so the host
 * page and the widget never affect each other. No dependencies.
 */
(function () {
  'use strict';

  var script = document.currentScript;
  if (!script || window.ChatWidget) return;

  var ds = script.dataset;
  var API = (ds.api || new URL(script.src, location.href).origin).replace(/\/+$/, '');
  var SITE = ds.site || '';
  var cfg = {
    title: ds.title || 'Assistant',
    greeting: ds.greeting || '',
    accent: ds.accent || '#2563eb',
    theme: ds.theme || 'auto',
    position: ds.position === 'left' ? 'left' : 'right',
    locale: ds.locale === 'zh' ? 'zh' : 'en'
  };
  var STORAGE_KEY = 'chat-widget:' + (SITE || location.origin);

  var LABELS = {
    en: {
      open: 'Chat with us',
      close: 'Close',
      reset: 'New conversation',
      lang: '中文',
      placeholder: 'Type your message…',
      send: 'Send',
      stop: 'Stop',
      greeting: 'Hello! How can I help you today?',
      you: 'You',
      error: 'Something went wrong. Please try again.',
      retry: 'Retry',
      privacy: 'General inquiries only. Do not share sensitive information.'
    },
    zh: {
      open: '在线咨询',
      close: '关闭',
      reset: '新对话',
      lang: 'EN',
      placeholder: '输入您的问题…',
      send: '发送',
      stop: '停止',
      greeting: '您好！请问有什么可以帮您？',
      you: '您',
      error: '出了点问题，请重试。',
      retry: '重试',
      privacy: '仅限一般咨询，请勿分享敏感信息。'
    }
  };

  /* ---------------------------------------------------------------- state */

  var state = load() || { messages: [], sessionId: newSessionId(), locale: cfg.locale };
  var open = false;
  var streaming = false;
  var controller = null;
  var lastFailedInput = null;

  function newSessionId() {
    return Date.now() + '-' + Math.random().toString(36).slice(2, 9);
  }

  function load() {
    try {
      var raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      var data = JSON.parse(raw);
      return {
        messages: Array.isArray(data.messages) ? data.messages : [],
        sessionId: data.sessionId || newSessionId(),
        locale: data.locale === 'zh' ? 'zh' : cfg.locale
      };
    } catch (e) {
      return null;
    }
  }

  function save() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch (e) { /* storage unavailable: conversation is per page load */ }
  }

  function t(key) {
    return LABELS[state.locale][key];
  }

  /* ---------------------------------------------------------------- theme */

  function resolveTheme() {
    if (cfg.theme === 'dark' || cfg.theme === 'light') return cfg.theme;
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }

  function accentForeground(hex) {
    var m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
    if (!m) return '#ffffff';
    var n = parseInt(m[1], 16);
    var r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
    var luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
    return luminance > 0.6 ? '#111111' : '#ffffff';
  }

  var CSS = [
    ':host{all:initial;--cw-accent:' + cfg.accent + ';--cw-accent-fg:' + accentForeground(cfg.accent) + ';',
    'font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,"Noto Sans","PingFang SC","Microsoft YaHei",sans-serif;font-size:14px;line-height:1.5;}',
    ':host([data-theme="light"]){--cw-bg:#ffffff;--cw-fg:#111827;--cw-muted:#6b7280;--cw-border:#e5e7eb;--cw-input:#f9fafb;--cw-bot:#f3f4f6;--cw-user:var(--cw-accent);--cw-user-fg:var(--cw-accent-fg);--cw-shadow:0 12px 40px rgba(0,0,0,.18);}',
    ':host([data-theme="dark"]){--cw-bg:#0f1115;--cw-fg:#f3f4f6;--cw-muted:#9ca3af;--cw-border:#262a33;--cw-input:#171a21;--cw-bot:#1c2028;--cw-user:var(--cw-accent);--cw-user-fg:var(--cw-accent-fg);--cw-shadow:0 12px 40px rgba(0,0,0,.5);}',
    '*{box-sizing:border-box;margin:0;padding:0;}',
    'button{font:inherit;cursor:pointer;border:0;background:none;color:inherit;}',
    'button:focus-visible,input:focus-visible{outline:2px solid var(--cw-accent);outline-offset:2px;}',
    '.launcher{position:fixed;bottom:20px;' + cfg.position + ':20px;z-index:2147483000;width:56px;height:56px;border-radius:50%;background:var(--cw-accent);color:var(--cw-accent-fg);display:flex;align-items:center;justify-content:center;box-shadow:0 6px 20px rgba(0,0,0,.25);transition:transform .15s;}',
    '.launcher:hover{transform:scale(1.06);}',
    '.launcher svg{width:26px;height:26px;}',
    '.panel{position:fixed;bottom:88px;' + cfg.position + ':20px;z-index:2147483001;width:380px;height:560px;max-height:calc(100vh - 108px);display:flex;flex-direction:column;background:var(--cw-bg);color:var(--cw-fg);border:1px solid var(--cw-border);border-radius:16px;box-shadow:var(--cw-shadow);overflow:hidden;opacity:0;transform:translateY(12px) scale(.98);pointer-events:none;transition:opacity .18s,transform .18s;}',
    '.panel.open{opacity:1;transform:none;pointer-events:auto;}',
    '@media (max-width:640px){.panel{left:8px;right:8px;bottom:8px;width:auto;height:min(80vh,calc(100vh - 16px));max-height:none;border-radius:14px;}.panel.open~.launcher{display:none;}}',
    '.header{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:12px 14px;border-bottom:1px solid var(--cw-border);}',
    '.title{display:flex;align-items:center;gap:10px;font-weight:600;font-size:15px;min-width:0;}',
    '.title .dot{width:10px;height:10px;border-radius:50%;background:var(--cw-accent);flex-shrink:0;}',
    '.title span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}',
    '.actions{display:flex;align-items:center;gap:2px;flex-shrink:0;}',
    '.icon{width:32px;height:32px;border-radius:8px;display:flex;align-items:center;justify-content:center;color:var(--cw-muted);}',
    '.icon:hover{background:var(--cw-bot);color:var(--cw-fg);}',
    '.icon svg{width:18px;height:18px;}',
    '.lang{padding:0 8px;font-size:12px;font-weight:600;width:auto;}',
    '.messages{flex:1;overflow-y:auto;padding:16px 14px;display:flex;flex-direction:column;gap:12px;scroll-behavior:smooth;}',
    '.empty{margin:auto;text-align:center;color:var(--cw-muted);padding:0 20px;font-size:14px;}',
    '.msg{display:flex;flex-direction:column;max-width:88%;}',
    '.msg.user{align-self:flex-end;align-items:flex-end;}',
    '.msg.bot{align-self:flex-start;align-items:flex-start;}',
    '.who{font-size:11px;color:var(--cw-muted);margin:0 4px 3px;}',
    '.bubble{padding:10px 14px;border-radius:14px;white-space:pre-wrap;overflow-wrap:anywhere;font-size:14px;}',
    '.msg.user .bubble{background:var(--cw-user);color:var(--cw-user-fg);border-bottom-right-radius:4px;}',
    '.msg.bot .bubble{background:var(--cw-bot);border-bottom-left-radius:4px;}',
    '.bubble a{color:inherit;text-decoration:underline;}',
    '.typing{display:inline-flex;gap:4px;align-items:center;height:18px;}',
    '.typing i{width:6px;height:6px;border-radius:50%;background:var(--cw-muted);animation:cw-bounce 1.2s infinite ease-in-out;}',
    '.typing i:nth-child(2){animation-delay:.15s}.typing i:nth-child(3){animation-delay:.3s}',
    '@keyframes cw-bounce{0%,80%,100%{transform:translateY(0)}40%{transform:translateY(-5px)}}',
    '.notice{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:8px 14px;font-size:12px;color:#ef4444;background:rgba(239,68,68,.08);border-top:1px solid rgba(239,68,68,.25);}',
    '.notice button{font-size:12px;font-weight:600;color:#ef4444;}',
    '.compose{display:flex;gap:8px;padding:10px 12px;border-top:1px solid var(--cw-border);}',
    '.compose input{flex:1;min-width:0;padding:10px 12px;border-radius:10px;border:1px solid var(--cw-border);background:var(--cw-input);color:var(--cw-fg);font:inherit;}',
    '.compose input::placeholder{color:var(--cw-muted);}',
    '.compose input:disabled{opacity:.6;}',
    '.send{padding:0 16px;border-radius:10px;background:var(--cw-accent);color:var(--cw-accent-fg);font-weight:600;font-size:13px;}',
    '.send:disabled{opacity:.4;cursor:not-allowed;}',
    '.send.stop{background:var(--cw-bot);color:var(--cw-fg);}',
    '.footer{padding:6px 14px 8px;font-size:11px;color:var(--cw-muted);text-align:center;}',
    '[hidden]{display:none !important;}'
  ].join('');

  var ICONS = {
    chat: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12c0 4.418-4.03 8-9 8a9.9 9.9 0 0 1-4.26-.95L3 20l1.4-3.72C3.51 15.04 3 13.57 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"/></svg>',
    close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 18L18 6M6 6l12 12"/></svg>',
    reset: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4v5h5M20 20v-5h-5"/><path d="M20 9A8 8 0 0 0 5.6 6.6L4 9M4 15a8 8 0 0 0 14.4 2.4L20 15"/></svg>'
  };

  /* ------------------------------------------------------------------ dom */

  var host = document.createElement('div');
  host.setAttribute('data-chat-widget', '');
  host.setAttribute('data-theme', resolveTheme());
  var root = host.attachShadow({ mode: 'open' });
  root.innerHTML =
    '<style>' + CSS + '</style>' +
    '<div class="panel" role="dialog" aria-modal="false" aria-label="' + esc(cfg.title) + '">' +
      '<div class="header">' +
        '<div class="title"><i class="dot"></i><span>' + esc(cfg.title) + '</span></div>' +
        '<div class="actions">' +
          '<button class="icon lang" type="button"></button>' +
          '<button class="icon reset" type="button">' + ICONS.reset + '</button>' +
          '<button class="icon close" type="button">' + ICONS.close + '</button>' +
        '</div>' +
      '</div>' +
      '<div class="messages" aria-live="polite"></div>' +
      '<div class="notice" role="alert" hidden><span class="notice-text"></span><button class="retry" type="button"></button></div>' +
      '<form class="compose"><input type="text" maxlength="500" autocomplete="off"><button class="send" type="submit"></button></form>' +
      '<div class="footer"></div>' +
    '</div>' +
    '<button class="launcher" type="button">' + ICONS.chat + '</button>';
  document.body.appendChild(host);

  var $ = function (sel) { return root.querySelector(sel); };
  var el = {
    panel: $('.panel'), launcher: $('.launcher'), lang: $('.lang'), reset: $('.reset'),
    close: $('.close'), messages: $('.messages'), notice: $('.notice'), noticeText: $('.notice-text'),
    retry: $('.retry'), form: $('.compose'), input: $('.compose input'), send: $('.send'), footer: $('.footer')
  };

  if (cfg.theme === 'auto' && window.matchMedia) {
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function () {
      host.setAttribute('data-theme', resolveTheme());
    });
  }

  /* -------------------------------------------------------------- render */

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function format(text) {
    var html = esc(text);
    html = html.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/(https?:\/\/[^\s<]+[^\s<.,;:!?)\]])/g, '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');
    return html;
  }

  function applyLabels() {
    el.launcher.setAttribute('aria-label', t('open'));
    el.close.setAttribute('aria-label', t('close'));
    el.reset.setAttribute('aria-label', t('reset'));
    el.reset.title = t('reset');
    el.lang.textContent = t('lang');
    el.lang.setAttribute('aria-label', state.locale === 'en' ? 'Switch to Chinese' : '切换到英文');
    el.input.placeholder = t('placeholder');
    el.send.textContent = streaming ? t('stop') : t('send');
    el.retry.textContent = t('retry');
    el.footer.textContent = t('privacy');
  }

  function renderMessages() {
    var html = '';
    if (!state.messages.length) {
      html = '<div class="empty">' + esc(cfg.greeting || t('greeting')) + '</div>';
    } else {
      state.messages.forEach(function (m) {
        var who = m.role === 'user' ? t('you') : cfg.title;
        var body = m.content
          ? format(m.content)
          : '<span class="typing"><i></i><i></i><i></i></span>';
        html += '<div class="msg ' + (m.role === 'user' ? 'user' : 'bot') + '">' +
          '<div class="who">' + esc(who) + '</div><div class="bubble">' + body + '</div></div>';
      });
    }
    el.messages.innerHTML = html;
    el.messages.scrollTop = el.messages.scrollHeight;
    el.reset.hidden = !state.messages.length;
  }

  function updateLastBubble(text) {
    var bubbles = el.messages.querySelectorAll('.msg.bot .bubble');
    var last = bubbles[bubbles.length - 1];
    if (last) {
      last.innerHTML = format(text);
      el.messages.scrollTop = el.messages.scrollHeight;
    }
  }

  function setStreaming(on) {
    streaming = on;
    el.input.disabled = on;
    el.send.classList.toggle('stop', on);
    el.send.disabled = false;
    el.send.textContent = on ? t('stop') : t('send');
    if (!on) updateSendState();
  }

  function updateSendState() {
    if (!streaming) el.send.disabled = !el.input.value.trim();
  }

  function showError(message) {
    el.noticeText.textContent = message || t('error');
    el.notice.hidden = false;
  }

  function hideError() {
    el.notice.hidden = true;
  }

  /* ------------------------------------------------------------ network */

  function streamChat(messages, onToken) {
    controller = new AbortController();
    var body = {
      session_id: state.sessionId,
      messages: messages,
      locale: state.locale,
      page_url: location.href
    };
    if (SITE) body.site = SITE;

    return fetch(API + '/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: controller.signal
    }).then(function (res) {
      if (!res.ok) {
        return res.json().catch(function () { return {}; }).then(function (data) {
          throw new Error(data.detail || ('HTTP ' + res.status));
        });
      }
      var reader = res.body.getReader();
      var decoder = new TextDecoder();
      var buffer = '';
      var event = 'token';

      function pump() {
        return reader.read().then(function (result) {
          if (result.done) return;
          buffer += decoder.decode(result.value, { stream: true }).replace(/\r\n?/g, '\n');
          var lines = buffer.split('\n');
          buffer = lines.pop() || '';
          for (var i = 0; i < lines.length; i++) {
            var line = lines[i];
            if (line === '' || line.charAt(0) === ':') continue;
            if (line.indexOf('event:') === 0) {
              event = line.slice(6).trim();
              if (event === 'done') { reader.cancel(); return; }
              continue;
            }
            if (line.indexOf('data:') === 0) {
              var data = line.slice(5);
              if (data.charAt(0) === ' ') data = data.slice(1);
              if (event === 'token') onToken(data);
              else if (event === 'error') throw new Error(data || 'error');
            }
          }
          return pump();
        });
      }
      return pump();
    });
  }

  function send(text) {
    text = String(text || '').trim();
    if (!text || streaming) return;
    hideError();
    lastFailedInput = null;

    state.messages.push({ role: 'user', content: text });
    var history = state.messages.slice();
    state.messages.push({ role: 'assistant', content: '' });
    save();
    renderMessages();
    setStreaming(true);

    var reply = '';
    streamChat(history, function (token) {
      reply += token;
      state.messages[state.messages.length - 1].content = reply;
      updateLastBubble(reply);
    }).then(function () {
      finish();
    }).catch(function (err) {
      if (err && err.name === 'AbortError') { finish(); return; }
      state.messages.pop();               // drop the empty assistant bubble
      if (!reply) lastFailedInput = text;
      finish();
      showError(t('error'));
    });

    function finish() {
      controller = null;
      if (state.messages.length && state.messages[state.messages.length - 1].content === '' &&
          state.messages[state.messages.length - 1].role === 'assistant') {
        state.messages.pop();
      }
      save();
      renderMessages();
      setStreaming(false);
      if (open) el.input.focus();
    }
  }

  function stop() {
    if (controller) controller.abort();
  }

  /* ------------------------------------------------------------- actions */

  function setOpen(next) {
    open = next;
    el.panel.classList.toggle('open', open);
    el.launcher.setAttribute('aria-expanded', String(open));
    if (open) {
      renderMessages();
      setTimeout(function () { el.input.focus(); }, 200);
    }
  }

  function reset() {
    stop();
    state.messages = [];
    state.sessionId = newSessionId();
    lastFailedInput = null;
    hideError();
    save();
    renderMessages();
    el.input.focus();
  }

  function toggleLocale() {
    state.locale = state.locale === 'en' ? 'zh' : 'en';
    save();
    applyLabels();
    renderMessages();
  }

  el.launcher.addEventListener('click', function () { setOpen(!open); });
  el.close.addEventListener('click', function () { setOpen(false); });
  el.reset.addEventListener('click', reset);
  el.lang.addEventListener('click', toggleLocale);
  el.retry.addEventListener('click', function () {
    hideError();
    var text = lastFailedInput;
    if (text) {
      // remove the user turn that produced no reply, then resend it
      var last = state.messages[state.messages.length - 1];
      if (last && last.role === 'user' && last.content === text) state.messages.pop();
      send(text);
    }
  });
  el.input.addEventListener('input', updateSendState);
  el.form.addEventListener('submit', function (e) {
    e.preventDefault();
    if (streaming) { stop(); return; }
    var text = el.input.value;
    el.input.value = '';
    updateSendState();
    send(text);
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && open) setOpen(false);
  });

  applyLabels();
  renderMessages();
  updateSendState();

  window.ChatWidget = {
    open: function () { setOpen(true); },
    close: function () { setOpen(false); },
    toggle: function () { setOpen(!open); },
    send: function (text) { setOpen(true); send(text); }
  };
})();
