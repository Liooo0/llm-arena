/* 共享工具：模型元信息 / API 封装 / 星标组件 / 回答卡片渲染 / toast */
(function () {
  "use strict";

  // 每个模型一个固定强调色（深色背景下对比清晰）
  const MODEL_COLORS = {
    "deepseek-v4-flash": "#3b82f6",
    "deepseek-v4-pro": "#8b5cf6",
    "glm-5.2": "#f59e0b",
    "kimi-k3": "#06b6d4",
    "qwen3.7-max": "#ec4899",
  };
  const MODEL_NAMES = {
    "deepseek-v4-flash": "DeepSeek V4 Flash",
    "deepseek-v4-pro": "DeepSeek V4 Pro",
    "glm-5.2": "GLM 5.2",
    "kimi-k3": "Kimi K3",
    "qwen3.7-max": "Qwen 3.7 Max",
  };

  let cachedModels = null;

  function modelColor(id) {
    return MODEL_COLORS[id] || "#6d8cff";
  }
  function modelName(id) {
    return MODEL_NAMES[id] || id;
  }
  function modelInitial(id) {
    const n = modelName(id);
    return n.split(/\s+/)[0].slice(0, 2).toUpperCase();
  }

  async function fetchModels(force) {
    if (cachedModels && !force) return cachedModels;
    const res = await fetch("/api/models");
    if (!res.ok) throw new Error("加载模型列表失败");
    cachedModels = await res.json();
    return cachedModels;
  }

  async function api(path, opts) {
    const res = await fetch(path, opts);
    if (!res.ok) {
      let detail = "HTTP " + res.status;
      try {
        const j = await res.json();
        if (j.detail) detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
      } catch (_) { /* ignore */ }
      throw new Error(detail);
    }
    return res.json();
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function fmtMs(ms) {
    if (ms == null) return "—";
    if (ms >= 1000) return (ms / 1000).toFixed(1) + "s";
    return ms + "ms";
  }

  function fmtTokens(n) {
    if (n == null) return "—";
    if (n >= 1000) return (n / 1000).toFixed(1) + "k";
    return String(n);
  }

  function fmtTime(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    const p = (x) => String(x).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  /* ---------- 星标组件 ---------- */

  function starsMarkup(score, readonly) {
    let html = '<span class="stars' + (readonly ? " readonly" : "") + '">';
    for (let i = 1; i <= 5; i++) {
      html += `<button class="star${i <= score ? " on" : ""}" data-v="${i}" aria-label="${i} 星">&starf;</button>`;
    }
    return html + "</span>";
  }

  function attachStars(container, answerId, initial, readonly) {
    container.innerHTML = starsMarkup(initial || 0, readonly);
    const stars = container.querySelectorAll(".star");
    if (readonly) return;
    stars.forEach((s) => {
      s.addEventListener("click", async () => {
        const v = Number(s.dataset.v);
        try {
          await api("/api/rate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ answer_id: answerId, score: v }),
          });
          renderStars(container, v);
          toast("已评 " + v + " 星", "ok");
        } catch (e) {
          toast("评分失败：" + e.message, "err");
        }
      });
      s.addEventListener("mouseenter", () => preview(container, Number(s.dataset.v)));
      s.addEventListener("mouseleave", () => renderStars(container, initial));
    });
  }

  function preview(container, v) {
    container.querySelectorAll(".star").forEach((s) => {
      s.classList.toggle("on", Number(s.dataset.v) <= v);
    });
  }

  function renderStars(container, v) {
    container.querySelectorAll(".star").forEach((s) => {
      s.classList.toggle("on", Number(s.dataset.v) <= v);
    });
  }

  /* ---------- 回答卡片 ---------- */

  function answerCardHTML(a) {
    const color = modelColor(a.model);
    const hasContent = a.content && a.content.trim().length > 0;
    const body = hasContent
      ? `<div class="answer-body markdown">${renderMarkdown(a.content)}</div>`
      : '<div class="answer-empty">（模型未返回内容）</div>';
    const err = a.error
      ? `<div class="answer-error">${escapeHtml(a.error)}</div>`
      : "";
    return `
      <div class="answer-card" style="--mc:${color}">
        <div class="answer-head">
          <span class="mdot">${modelInitial(a.model)}</span>
          <span class="mname">${modelName(a.model)}</span>
          <span class="answer-meta">耗时 ${fmtMs(a.latency_ms)}<br>token ${fmtTokens(a.tokens)}</span>
        </div>
        ${body}
        ${err}
        <div class="answer-foot">
          <span class="stars-host"></span>
          <span class="rate-hint">${a.error ? "" : "点击星标打分"}</span>
        </div>
      </div>`;
  }

  function mountAnswerCard(el, a, readonly) {
    el.innerHTML = answerCardHTML(a);
    const host = el.querySelector(".stars-host");
    attachStars(host, a.answer_id, a.rating || 0, readonly || !!a.error);
    return el;
  }

  /* ---------- Toast ---------- */

  function toast(msg, type) {
    let wrap = document.querySelector(".toast-wrap");
    if (!wrap) {
      wrap = document.createElement("div");
      wrap.className = "toast-wrap";
      document.body.appendChild(wrap);
    }
    const t = document.createElement("div");
    t.className = "toast " + (type || "");
    t.textContent = msg;
    wrap.appendChild(t);
    setTimeout(() => {
      t.style.opacity = "0";
      t.style.transition = "opacity .3s";
      setTimeout(() => t.remove(), 350);
    }, 2400);
  }

  window.arena = {
    MODEL_COLORS,
    MODEL_NAMES,
    modelColor,
    modelName,
    fetchModels,
    api,
    escapeHtml,
    fmtMs,
    fmtTokens,
    fmtTime,
    attachStars,
    renderStars,
    answerCardHTML,
    mountAnswerCard,
    toast,
  };
})();
