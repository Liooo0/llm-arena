/* 最小 Markdown 渲染器（离线可用，无外部依赖）。
 * 支持：标题 / 粗体 / 斜体 / 行内代码 / 代码块 / 无序与有序列表 /
 *       引用 / 链接 / 分隔线 / 表格 / 段落。
 * 输出 HTML 字符串；所有输入先做 HTML 转义，再按块级 → 行内顺序处理。
 */
(function () {
  "use strict";

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // 行内格式：代码 > 粗体 > 斜体 > 链接
  function inline(t) {
    return t
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/__([^_]+)__/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>")
      .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  }

  // 代码块占位符（用户输入经过 HTML 转义后，几乎不可能与本串冲突）
  const CODE_TAG = "ZZCODE";

  function codePlaceholder(i) {
    return CODE_TAG + i + "ZZ";
  }

  function isCodeToken(line) {
    return /^ZZCODE\d+ZZ$/.test(line.trim());
  }

  function isBlockStart(line) {
    return (
      /^#{1,6}\s+/.test(line) ||
      /^\s*([-*_])\s*(\1\s*){2,}$/.test(line) ||
      /^\s*[-*+]\s+/.test(line) ||
      /^\s*\d+\.\s+/.test(line) ||
      /^\s*&gt;\s?/.test(line) ||
      isCodeToken(line) ||
      (line.includes("|") && /^\s*\|?[\s:|-]+\|?\s*$/.test(line))
    );
  }

  function renderTable(lines, i) {
    // 表头行
    const header = lines[i].split("|").map((s) => s.trim());
    if (header[0] === "") header.shift();
    if (header[header.length - 1] === "") header.pop();
    const body = [];
    let j = i + 2; // 跳过分隔行
    while (j < lines.length && lines[j].includes("|")) {
      let cells = lines[j].split("|").map((s) => s.trim());
      if (cells[0] === "") cells.shift();
      if (cells[cells.length - 1] === "") cells.pop();
      body.push(cells);
      j++;
    }
    let html = "<table><thead><tr>";
    for (const h of header) html += `<th>${inline(escapeHtml(h))}</th>`;
    html += "</tr></thead><tbody>";
    for (const row of body) {
      html += "<tr>";
      for (const c of row) html += `<td>${inline(escapeHtml(c))}</td>`;
      html += "</tr>";
    }
    html += "</tbody></table>";
    return { html, next: j };
  }

  function renderMarkdown(src) {
    if (!src) return "";
    const codeBlocks = [];
    let s = escapeHtml(src);

    // 先抽出围栏代码块，用占位符保护
    s = s.replace(
      /```(\w*)\n([\s\S]*?)(?:```|$)/g,
      function (m, lang, code) {
        const idx = codeBlocks.length;
        codeBlocks.push({ lang: lang || "", code: code.replace(/\n$/, "") });
        return codePlaceholder(idx);
      }
    );

    const lines = s.split("\n");
    let html = "";
    let i = 0;

    while (i < lines.length) {
      const line = lines[i];

      // 代码块占位还原
      const mCode = line.trim().match(/^ZZCODE(\d+)ZZ$/);
      if (mCode) {
        const cb = codeBlocks[Number(mCode[1])];
        html += `<pre><code class="lang-${cb.lang}">${cb.code}</code></pre>`;
        i++;
        continue;
      }

      // 标题
      const mH = line.match(/^(#{1,6})\s+(.*)$/);
      if (mH) {
        const lv = mH[1].length;
        html += `<h${lv}>${inline(mH[2])}</h${lv}>`;
        i++;
        continue;
      }

      // 分隔线
      if (/^\s*([-*_])\s*(\1\s*){2,}$/.test(line)) {
        html += "<hr>";
        i++;
        continue;
      }

      // 引用
      if (/^\s*&gt;\s?/.test(line)) {
        const quote = [];
        while (i < lines.length && /^\s*&gt;\s?/.test(lines[i])) {
          quote.push(lines[i].replace(/^\s*&gt;\s?/, ""));
          i++;
        }
        html += `<blockquote>${quote.map(inline).join("<br>")}</blockquote>`;
        continue;
      }

      // 表格
      if (line.includes("|") && i + 1 < lines.length && /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[i + 1])) {
        const t = renderTable(lines, i);
        html += t.html;
        i = t.next;
        continue;
      }

      // 无序列表
      if (/^\s*[-*+]\s+/.test(line)) {
        const items = [];
        while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
          items.push(`<li>${inline(lines[i].replace(/^\s*[-*+]\s+/, ""))}</li>`);
          i++;
        }
        html += `<ul>${items.join("")}</ul>`;
        continue;
      }

      // 有序列表
      if (/^\s*\d+\.\s+/.test(line)) {
        const items = [];
        while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
          items.push(`<li>${inline(lines[i].replace(/^\s*\d+\.\s+/, ""))}</li>`);
          i++;
        }
        html += `<ol>${items.join("")}</ol>`;
        continue;
      }

      // 段落：收集连续文本行
      const para = [];
      while (
        i < lines.length &&
        lines[i].trim() !== "" &&
        !isBlockStart(lines[i])
      ) {
        para.push(lines[i]);
        i++;
      }
      if (para.length) {
        html += `<p>${para.map(inline).join("<br>")}</p>`;
      }
      i++; // 跳过空行
    }

    return html;
  }

  window.renderMarkdown = renderMarkdown;
})();
