(function () {
  "use strict";

  var cfg = window.ChatbotConfig || {};
  var API = (cfg.apiUrl || "").replace(/\/$/, "");
  var TITLE = cfg.title || "Chat with us";
  var PLACEHOLDER = cfg.placeholder || "Type your message…";
  var COLOR = cfg.primaryColor || "#4f46e5";
  var WELCOME = cfg.welcomeMessage || "Hi there! How can I help you today?";

  if (!API) {
    console.error("[Chatbot] ChatbotConfig.apiUrl is required.");
    return;
  }

  // ── Styles ──────────────────────────────────────────────────────────────────
  var css = `
    #cb-btn {
      position: fixed; bottom: 24px; right: 24px; z-index: 999999;
      width: 56px; height: 56px; border-radius: 50%;
      background: ${COLOR}; border: none; cursor: pointer;
      box-shadow: 0 4px 16px rgba(0,0,0,0.25);
      display: flex; align-items: center; justify-content: center;
      transition: transform .2s;
    }
    #cb-btn:hover { transform: scale(1.08); }
    #cb-btn svg { width: 26px; height: 26px; fill: #fff; }

    #cb-panel {
      position: fixed; bottom: 92px; right: 24px; z-index: 999999;
      width: 340px; max-height: 520px;
      border-radius: 16px; overflow: hidden;
      box-shadow: 0 8px 32px rgba(0,0,0,0.18);
      display: flex; flex-direction: column;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      font-size: 14px; line-height: 1.5;
      background: #fff;
      transform: scale(0); transform-origin: bottom right;
      transition: transform .22s cubic-bezier(.34,1.56,.64,1);
    }
    #cb-panel.cb-open { transform: scale(1); }

    #cb-header {
      background: ${COLOR}; color: #fff;
      padding: 14px 16px; display: flex; align-items: center; gap: 10px;
    }
    #cb-header-title { flex: 1; font-weight: 600; font-size: 15px; }
    #cb-close {
      background: none; border: none; color: #fff; cursor: pointer;
      font-size: 20px; line-height: 1; padding: 0 2px;
    }

    #cb-messages {
      flex: 1; overflow-y: auto; padding: 14px 12px;
      display: flex; flex-direction: column; gap: 10px;
      background: #f9fafb;
    }

    .cb-msg { max-width: 82%; display: flex; flex-direction: column; }
    .cb-msg.cb-user { align-self: flex-end; align-items: flex-end; }
    .cb-msg.cb-bot  { align-self: flex-start; }
    .cb-bubble {
      padding: 9px 13px; border-radius: 14px; word-break: break-word;
    }
    .cb-user .cb-bubble { background: ${COLOR}; color: #fff; border-bottom-right-radius: 4px; white-space: pre-wrap; }
    .cb-bot  .cb-bubble { background: #fff; color: #111; border: 1px solid #e5e7eb; border-bottom-left-radius: 4px; }
    .cb-bot .cb-bubble p { margin: 3px 0; }
    .cb-bot .cb-bubble strong { font-weight: 600; }
    .cb-bot .cb-bubble em { font-style: italic; }
    .cb-bot .cb-bubble code { background: #f3f4f6; border: 1px solid #e5e7eb; border-radius: 3px; padding: 1px 4px; font-family: monospace; font-size: 12px; }
    .cb-bot .cb-bubble pre { background: #1e293b; color: #e2e8f0; border-radius: 6px; padding: 8px 10px; margin: 4px 0; overflow-x: auto; font-size: 12px; }
    .cb-bot .cb-bubble pre code { background: none; border: none; padding: 0; color: inherit; }
    .cb-bot .cb-bubble ul, .cb-bot .cb-bubble ol { padding-left: 16px; margin: 3px 0; }
    .cb-bot .cb-bubble li { margin: 1px 0; }

    .cb-typing { display: flex; gap: 4px; align-items: center; padding: 10px 13px; }
    .cb-dot { width: 7px; height: 7px; border-radius: 50%; background: #9ca3af;
      animation: cb-bounce .9s infinite; }
    .cb-dot:nth-child(2) { animation-delay: .15s; }
    .cb-dot:nth-child(3) { animation-delay: .3s; }
    @keyframes cb-bounce {
      0%,80%,100% { transform: translateY(0); }
      40%          { transform: translateY(-5px); }
    }

    #cb-input-row {
      display: flex; gap: 8px; padding: 10px 12px;
      border-top: 1px solid #e5e7eb; background: #fff;
    }
    #cb-input {
      flex: 1; border: 1px solid #d1d5db; border-radius: 10px;
      padding: 8px 12px; font-size: 14px; outline: none; resize: none;
      font-family: inherit; line-height: 1.4; max-height: 100px; overflow-y: auto;
    }
    #cb-input:focus { border-color: ${COLOR}; }
    #cb-send {
      background: ${COLOR}; border: none; border-radius: 10px;
      color: #fff; padding: 8px 14px; cursor: pointer; font-size: 14px;
      transition: opacity .15s;
    }
    #cb-send:disabled { opacity: .5; cursor: default; }
  `;

  var style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);

  // ── Button ──────────────────────────────────────────────────────────────────
  var btn = document.createElement("button");
  btn.id = "cb-btn";
  btn.setAttribute("aria-label", "Open chat");
  btn.innerHTML = '<svg viewBox="0 0 24 24"><path d="M20 2H4a2 2 0 00-2 2v18l4-4h14a2 2 0 002-2V4a2 2 0 00-2-2z"/></svg>';
  document.body.appendChild(btn);

  // ── Panel ───────────────────────────────────────────────────────────────────
  var panel = document.createElement("div");
  panel.id = "cb-panel";
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-label", TITLE);
  panel.innerHTML = `
    <div id="cb-header">
      <svg style="width:20px;height:20px;fill:#fff;flex-shrink:0" viewBox="0 0 24 24">
        <path d="M20 2H4a2 2 0 00-2 2v18l4-4h14a2 2 0 002-2V4a2 2 0 00-2-2z"/>
      </svg>
      <span id="cb-header-title">${TITLE}</span>
      <button id="cb-close" aria-label="Close chat">&times;</button>
    </div>
    <div id="cb-messages"></div>
    <div id="cb-input-row">
      <textarea id="cb-input" rows="1" placeholder="${PLACEHOLDER}"></textarea>
      <button id="cb-send">Send</button>
    </div>
  `;
  document.body.appendChild(panel);

  var messages = document.getElementById("cb-messages");
  var input = document.getElementById("cb-input");
  var sendBtn = document.getElementById("cb-send");
  var history = [];
  var isOpen = false;

  function toggle() {
    isOpen = !isOpen;
    panel.classList.toggle("cb-open", isOpen);
    if (isOpen && messages.children.length === 0) addBotMsg(WELCOME);
    if (isOpen) setTimeout(function () { input.focus(); }, 220);
  }

  btn.addEventListener("click", toggle);
  document.getElementById("cb-close").addEventListener("click", toggle);

  function addBotMsg(text) {
    var div = document.createElement("div");
    div.className = "cb-msg cb-bot";
    div.innerHTML = '<div class="cb-bubble">' + mdToHtml(text) + "</div>";
    messages.appendChild(div);
    scrollDown();
    return div;
  }

  function addUserMsg(text) {
    var div = document.createElement("div");
    div.className = "cb-msg cb-user";
    div.innerHTML = '<div class="cb-bubble">' + escHtml(text) + "</div>";
    messages.appendChild(div);
    scrollDown();
  }

  function showTyping() {
    var div = document.createElement("div");
    div.className = "cb-msg cb-bot";
    div.innerHTML = '<div class="cb-bubble cb-typing"><span class="cb-dot"></span><span class="cb-dot"></span><span class="cb-dot"></span></div>';
    messages.appendChild(div);
    scrollDown();
    return div;
  }

  function scrollDown() {
    messages.scrollTop = messages.scrollHeight;
  }

  function escHtml(str) {
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function mdToHtml(text) {
    var s = text
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
    s = s.replace(/```(?:\w+)?\n?([\s\S]*?)```/g, function(_, code) {
      return "<pre><code>" + code.trim() + "</code></pre>";
    });
    s = s.replace(/`([^`\n]+)`/g, "<code>$1</code>");
    s = s.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/\*([^*\n]+)\*/g, "<em>$1</em>");
    s = s.replace(/^#{1,3} (.+)$/gm, "<strong>$1</strong>");
    s = s.replace(/^[ \t]*[-*+] (.+)$/gm, "<li>$1</li>");
    s = s.replace(/(<li>.*<\/li>\n?)+/g, "<ul>$&</ul>");
    s = s.replace(/\n{2,}/g, "</p><p>");
    s = s.replace(/\n/g, "<br>");
    return "<p>" + s + "</p>";
  }

  async function send() {
    var text = input.value.trim();
    if (!text || sendBtn.disabled) return;

    input.value = "";
    input.style.height = "auto";
    sendBtn.disabled = true;
    addUserMsg(text);

    var typing = showTyping();

    try {
      var res = await fetch(API + "/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, history: history }),
      });

      if (!res.ok) throw new Error("Server error " + res.status);
      var data = await res.json();
      var reply = data.reply || "Sorry, I couldn't get a response.";

      history.push({ role: "user", content: text });
      history.push({ role: "assistant", content: reply });
      if (history.length > 20) history = history.slice(-20);

      typing.remove();
      addBotMsg(reply);
    } catch (err) {
      typing.remove();
      addBotMsg("Sorry, something went wrong. Please try again.");
      console.error("[Chatbot]", err);
    } finally {
      sendBtn.disabled = false;
      input.focus();
    }
  }

  sendBtn.addEventListener("click", send);
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });
  input.addEventListener("input", function () {
    this.style.height = "auto";
    this.style.height = Math.min(this.scrollHeight, 100) + "px";
  });
})();
