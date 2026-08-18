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

  function fmtTps(a) {
    if (a.ttft_ms == null || a.latency_ms == null || !a.output_tokens) return "—";
    const genS = (a.latency_ms - a.ttft_ms) / 1000;
    return genS > 0 ? (a.output_tokens / genS).toFixed(1) + "/s" : "—";
  }

  function fmtCost(usd) {
    if (usd == null) return "—";
    return "$" + (usd < 0.01 ? usd.toFixed(4) : usd.toFixed(3));
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

  /* ---------- 回答卡片(支持双盲:默认匿名,可揭晓) ---------- */

  function answerCardHTML(a) {
    const color = a.blind ? "#6d8cff" : modelColor(a.model);
    const label = a.blind ? "模型 " + (a.blindLabel || "?") : modelName(a.model);
    const initial = a.blind ? (a.blindLabel || "?") : modelInitial(a.model);
    const revealBtn = a.blind
      ? `<button class="reveal-btn" data-blind="1">揭晓模型</button>`
      : "";
    const hasContent = a.content && a.content.trim().length > 0;
    const body = hasContent
      ? `<div class="answer-body markdown">${renderMarkdown(a.content)}</div>`
      : '<div class="answer-empty">（模型未返回内容）</div>';
    const err = a.error
      ? `<div class="answer-error">${escapeHtml(a.error)}</div>`
      : "";
    const judge = !a.error && a.judge_score
      ? `<div class="judge-badge" title="LLM Judge：${escapeHtml(a.judge_reason || "无理由")}">🤖 Judge ${a.judge_score}/5</div>`
      : "";
    return `
      <div class="answer-card" style="--mc:${color}">
        <div class="answer-head">
          <span class="mdot">${initial}</span>
          <span class="mname">${label}</span>
          ${revealBtn}
          <span class="answer-meta">耗时 ${fmtMs(a.latency_ms)} · 首字 ${fmtMs(a.ttft_ms)}<br>${fmtTokens(a.output_tokens)} out · ${fmtTps(a)} · ${fmtCost(a.cost_usd)}</span>
        </div>
        ${judge}
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
    const reveal = el.querySelector(".reveal-btn");
    if (reveal) {
      reveal.addEventListener("click", () => {
        reveal.remove();
        const mdot = el.querySelector(".mdot");
        const mname = el.querySelector(".mname");
        mdot.textContent = modelInitial(a.model);
        mdot.style.background = modelColor(a.model);
        mname.textContent = modelName(a.model);
        el.style.setProperty("--mc", modelColor(a.model));
        toast("已揭晓:" + modelName(a.model), "ok");
      });
    }
    return el;
  }

  function mountDuelControls(container, answers) {
    /* 两两对决按钮:选中两个有效回答后提交 /api/duel */
    let sel = new Set();
    container.innerHTML = '<div class="duel-bar">对决:点击两张回答卡片挑选,再点「判定胜负」</div>';
    const bar = container.querySelector(".duel-bar");
    const btn = document.createElement("button");
    btn.textContent = "判定胜负(选中两个)";
    btn.className = "duel-submit";
    btn.disabled = true;
    bar.appendChild(btn);

    container.querySelectorAll(".answer-card").forEach((card, i) => {
      card.classList.add("duelable");
      card.addEventListener("click", () => {
        if (answers[i].error) return;
        if (sel.has(i)) {
          sel.delete(i);
          card.classList.remove("selected");
        } else if (sel.size < 2) {
          sel.add(i);
          card.classList.add("selected");
        }
        btn.disabled = sel.size !== 2;
      });
    });

    btn.addEventListener("click", async () => {
      const [x, y] = [...sel];
      const winner = x;
      const loser = y;
      try {
        await api("/api/duel", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            winner_answer_id: answers[winner].answer_id,
            loser_answer_id: answers[loser].answer_id,
            outcome: "win",
          }),
        });
        toast("对决已记录,Elo 已更新", "ok");
        container.querySelectorAll(".answer-card").forEach((c) => c.classList.remove("selected", "duelable"));
        btn.disabled = true;
        sel.clear();
      } catch (e) {
        toast("对决失败:" + e.message, "err");
      }
    });
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
    fmtTps,
    fmtCost,
    fmtTime,
    attachStars,
    renderStars,
    answerCardHTML,
    mountAnswerCard,
    mountDuelControls,
    toast,
  };
})();
