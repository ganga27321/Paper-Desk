/* Paper Desk dashboard: hash-routed tabs, hand-built SVG charts with hover, calm motion. No libraries. */
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const app = $("#app"), tip = $("#tip"), tabs = $("#tabs");
  const COLORS = { day: "--s1", swing: "--s2", long: "--s3", futures: "--s4", options_aggr: "--s5", options_safe: "--s6", crypto: "--s7" };
  const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const col = id => css(COLORS[id] || "--s1");
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const usd = (v, d = 2) => v == null || isNaN(v) ? "-" : (v < 0 ? "-" : "") + "C$" + Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
  const pct = (v, d = 1) => v == null || isNaN(v) ? "-" : (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v * 100).toFixed(d) + "%";
  const signed = v => (v > 0 ? "+" : v < 0 ? "−" : "") + usd(Math.abs(v));
  const cls = v => v > 0 ? "up" : v < 0 ? "down" : "";
  const arrow = v => v > 0 ? "▲" : v < 0 ? "▼" : "•";
  const when = ts => { const d = new Date(ts); return isNaN(d) ? esc(ts) : d.toLocaleString("en-CA", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: "America/Toronto" }); };
  let D = null;

  // ---------- theme
  try { const t = localStorage.getItem("pd-theme"); if (t) document.documentElement.dataset.theme = t; } catch (e) {}
  $("#theme").onclick = () => {
    const cur = document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("pd-theme", next); } catch (e) {}
    route();
  };

  // ---------- motion helpers
  function countUp(root) {
    root.querySelectorAll("[data-count]").forEach(el => {
      const end = +el.dataset.count, fmt = el.dataset.fmt || "usd", t0 = performance.now(), dur = 900;
      if (matchMedia("(prefers-reduced-motion: reduce)").matches) { el.textContent = fmt === "pct" ? pct(end) : usd(end); return; }
      const step = now => {
        const k = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - k, 3), v = end * e;
        el.textContent = fmt === "pct" ? pct(v) : usd(v);
        if (k < 1) requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    });
  }
  function drawLines(root) {
    root.querySelectorAll("path.draw").forEach(p => { const L = p.getTotalLength(); p.style.setProperty("--len", L); });
  }
  const showTip = (html, x, y) => {
    tip.innerHTML = html; tip.classList.add("on");
    const w = tip.offsetWidth, h = tip.offsetHeight;
    tip.style.left = Math.min(innerWidth - w - 10, Math.max(10, x + 14)) + "px";
    tip.style.top = Math.max(10, y - h - 14) + "px";
  };
  const hideTip = () => tip.classList.remove("on");

  // ---------- charts
  function spark(points, color) {
    if (!points || points.length < 2) return `<svg class="spark" viewBox="0 0 100 44" aria-hidden="true"><line x1="0" y1="22" x2="100" y2="22" stroke="${color}" stroke-width="2" stroke-dasharray="3 4" opacity=".5"/></svg>`;
    const v = points.map(p => p[1]), lo = Math.min(...v), hi = Math.max(...v), r = hi - lo || 1;
    const d = v.map((y, i) => `${i ? "L" : "M"}${(i / (v.length - 1) * 100).toFixed(2)},${(40 - (y - lo) / r * 36).toFixed(2)}`).join("");
    return `<svg class="spark" viewBox="0 0 100 44" preserveAspectRatio="none" aria-hidden="true"><path class="draw" d="${d}" fill="none" stroke="${color}" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round"/></svg>`;
  }

  function lineChart(host, series, opts = {}) {
    const W = Math.max(320, host.clientWidth || 800), H = 300, m = { l: 56, r: 16, t: 14, b: 28 };
    const live = series.filter(s => !s.hidden && s.points.length);
    const all = live.flatMap(s => s.points.map(p => [new Date(p[0]).getTime(), p[1]]));
    if (!all.length) { host.innerHTML = `<div class="empty">The equity line starts with the first cycle.</div>`; return; }
    let x0 = Math.min(...all.map(p => p[0])), x1 = Math.max(...all.map(p => p[0]));
    if (x1 === x0) { x0 -= 36e5; x1 += 36e5; }
    let y0 = Math.min(...all.map(p => p[1]), opts.base ?? Infinity), y1 = Math.max(...all.map(p => p[1]), opts.base ?? -Infinity);
    const pad = (y1 - y0) * 0.08 || y1 * 0.02 || 1; y0 -= pad; y1 += pad;
    const X = t => m.l + (t - x0) / (x1 - x0) * (W - m.l - m.r), Y = v => m.t + (1 - (v - y0) / (y1 - y0)) * (H - m.t - m.b);
    const ticks = 4, grid = [];
    for (let i = 0; i <= ticks; i++) { const v = y0 + (y1 - y0) * i / ticks; grid.push(`<line class="grid-l" x1="${m.l}" x2="${W - m.r}" y1="${Y(v)}" y2="${Y(v)}"/><text x="${m.l - 8}" y="${Y(v) + 4}" text-anchor="end">${usd(v, 0)}</text>`); }
    const xt = []; for (let i = 0; i <= 3; i++) { const t = x0 + (x1 - x0) * i / 3; xt.push(`<text x="${X(t)}" y="${H - 8}" text-anchor="${i === 0 ? "start" : i === 3 ? "end" : "middle"}">${new Date(t).toLocaleDateString("en-CA", { month: "short", day: "numeric" })}</text>`); }
    const paths = live.map(s => {
      const d = s.points.map((p, i) => `${i ? "L" : "M"}${X(new Date(p[0]).getTime()).toFixed(1)},${Y(p[1]).toFixed(1)}`).join("");
      const last = s.points[s.points.length - 1];
      return `<path class="ln draw" d="${d}" stroke="${s.color}"/><circle cx="${X(new Date(last[0]).getTime()).toFixed(1)}" cy="${Y(last[1]).toFixed(1)}" r="4" fill="${s.color}" stroke="${css("--surface")}" stroke-width="2"/>`;
    }).join("");
    const base = opts.base != null ? `<line class="base" x1="${m.l}" x2="${W - m.r}" y1="${Y(opts.base)}" y2="${Y(opts.base)}"/>` : "";
    host.innerHTML = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(opts.label || "Equity over time")}">${grid.join("")}${xt.join("")}${base}${paths}<g class="hover" style="display:none"><line class="cross" y1="${m.t}" y2="${H - m.b}"/>${live.map(s => `<circle r="4.5" fill="${s.color}" stroke="${css("--surface")}" stroke-width="2"/>`).join("")}</g><rect x="${m.l}" y="0" width="${W - m.l - m.r}" height="${H}" fill="transparent"/></svg>`;
    drawLines(host);
    const svg = host.querySelector("svg"), g = svg.querySelector(".hover"), cross = g.querySelector("line"), dots = g.querySelectorAll("circle");
    const move = ev => {
      const r = svg.getBoundingClientRect(), px = (ev.clientX - r.left) * W / r.width, t = x0 + (px - m.l) / (W - m.l - m.r) * (x1 - x0);
      g.style.display = ""; cross.setAttribute("x1", px); cross.setAttribute("x2", px);
      let rows = "", stamp = "";
      live.forEach((s, i) => {
        let best = s.points[0], bd = Infinity;
        for (const p of s.points) { const dd = Math.abs(new Date(p[0]).getTime() - t); if (dd < bd) { bd = dd; best = p; } }
        dots[i].setAttribute("cx", X(new Date(best[0]).getTime())); dots[i].setAttribute("cy", Y(best[1]));
        stamp = when(best[0]);
        rows += `<div class="row"><span><span class="sw" style="background:${s.color}"></span> ${esc(s.name)}</span><b class="num">${usd(best[1])}</b></div>`;
      });
      showTip(`<div class="muted" style="margin-bottom:4px">${stamp}</div>${rows}`, ev.clientX, ev.clientY);
    };
    svg.addEventListener("pointermove", move);
    svg.addEventListener("pointerleave", () => { g.style.display = "none"; hideTip(); });
  }

  // ---------- pieces
  const avatar = t => `<div class="avatar" style="background:${col(t.id)}">${esc(t.name[0])}</div>`;
  const badge = t => t.tfsa ? `<span class="chip ok">✓ TFSA rules</span>` : `<span class="chip sand">⚠ Sandbox: not possible in your TFSA</span>`;
  const pnlCell = v => v === "" || v == null ? `<td class="r muted">-</td>` : `<td class="r ${cls(+v)}">${arrow(+v)} ${signed(+v)}</td>`;
  const actPill = a => `<span class="pill ${/BUY|LONG/.test(a) ? "buy" : "sell"}">${esc(a.replace(/_/g, " "))}</span>`;

  function tradeTable(rows, withTrader) {
    if (!rows.length) return `<div class="tw"><div class="empty">No trades yet.</div></div>`;
    return `<div class="tw"><table><thead><tr><th>When (Toronto)</th>${withTrader ? "<th>Trader</th>" : ""}<th>What</th><th>Symbol</th><th class="r">Qty</th><th class="r">Price</th><th class="r">How much</th><th class="r">P/L</th><th>Why</th></tr></thead><tbody>${rows.map(r => {
      const t = D.traders.find(x => x.id === r.trader) || {};
      return `<tr><td class="num">${when(r.ts)}</td>${withTrader ? `<td><span class="sw" style="background:${col(r.trader)}"></span> ${esc(t.name)}</td>` : ""}<td>${actPill(r.action)}</td><td class="sym">${esc(r.symbol)}</td><td class="r">${(+r.qty).toLocaleString("en-US", { maximumFractionDigits: 6 })}</td><td class="r">${usd(+r.price, +r.price < 1 ? 6 : 2)}</td><td class="r">${usd(+r.value)}<div class="muted">${r.pct_of_equity ? r.pct_of_equity + "% of equity" : ""}</div></td>${pnlCell(r.pnl)}<td class="why">${esc(r.why)}${r.source === "meeting" ? ' <span class="pill">meeting</span>' : ""}</td></tr>`;
    }).join("")}</tbody></table></div>`;
  }

  function posTable(t) {
    const ps = t.positions;
    if (!ps.length) return `<div class="tw"><div class="empty">No open positions - all cash.</div></div>`;
    return `<div class="tw"><table><thead><tr><th>Position</th><th>Kind</th><th class="r">Qty</th><th class="r">Entry</th><th class="r">Now</th><th class="r">Open P/L</th><th class="r">Stop / Target</th><th>Why</th></tr></thead><tbody>${ps.map(p => {
      const mult = p.kind === "option" ? 100 : p.kind === "future" ? p.meta.multiplier : 1;
      const now = p.kind === "option" ? (p.mark ?? p.entry) : (p.last ?? p.entry);
      const sign = p.side === "short" ? -1 : 1;
      const open = p.kind === "future" ? (p.unrealized ?? 0) : sign * (now - p.entry) * p.qty * mult;
      return `<tr><td class="sym">${esc(p.symbol)} ${p.side === "short" ? '<span class="pill sell">SHORT</span>' : ""}</td><td>${esc(p.kind)}${p.meta?.bucket ? " · " + esc(p.meta.bucket) : ""}</td><td class="r">${(+p.qty).toLocaleString("en-US", { maximumFractionDigits: 6 })}</td><td class="r">${usd(p.entry, p.entry < 1 ? 6 : 2)}</td><td class="r">${usd(now, now < 1 ? 6 : 2)}</td><td class="r ${cls(open)}">${arrow(open)} ${signed(open)}</td><td class="r">${p.stop != null ? usd(p.stop) : "-"} / ${p.target != null ? usd(p.target) : "-"}</td><td class="why">${esc(p.why)}</td></tr>`;
    }).join("")}</tbody></table></div>`;
  }

  // ---------- style scales
  const SM = () => D.scales_meta;
  function effRules(tid, scales) {
    const T = D.traders.find(t => t.id === tid).scales, m = SM(), out = { ...T.base_rules };
    for (const [knob, keys] of Object.entries(T.map || {})) {
      const f = Math.pow(2, (scales[knob] - T.defaults[knob]) / 2.5);
      for (const k of keys) {
        if (!(k in out)) continue;
        let v = m.inverse.some(([a, b]) => a === tid && b === k) ? out[k] / f : out[k] * f;
        const c = m.caps[k]; if (c) v = Math.min(Math.max(v, c[0]), c[1]);
        out[k] = m.ints.includes(k) ? Math.round(v) : Math.round(v * 1000) / 1000;
      }
    }
    out.deploy_frac = Math.min(1, 0.2 + 0.08 * scales.deployment);
    return out;
  }
  function radar(cur, prev, color, size = 300, labels = true) {
    const K = SM().knobs, n = K.length, c = size / 2, R = size / 2 - (labels ? 58 : 10);
    const pt = (i, v) => { const a = -Math.PI / 2 + i * 2 * Math.PI / n; return [c + Math.cos(a) * R * v / 10, c + Math.sin(a) * R * v / 10]; };
    const poly = vals => K.map((k, i) => pt(i, vals[k.id]).map(x => x.toFixed(1)).join(",")).join(" ");
    const rings = [2, 4, 6, 8, 10].map(r => `<polygon points="${K.map((k, i) => pt(i, r).join(",")).join(" ")}" fill="none" stroke="var(--line)"/>`).join("");
    const spokes = K.map((k, i) => { const [x, y] = pt(i, 10); return `<line x1="${c}" y1="${c}" x2="${x}" y2="${y}" stroke="var(--line)"/>`; }).join("");
    const lab = labels ? K.map((k, i) => { const [x, y] = pt(i, 12.6); return `<text x="${x}" y="${y}" text-anchor="middle" dominant-baseline="middle" style="fill:var(--ink-2);font:600 11px var(--font)">${esc(k.label)}</text><text x="${x}" y="${y + 13}" text-anchor="middle" dominant-baseline="middle" style="fill:var(--ink-3);font:11px var(--font)">${cur[k.id]}${prev && prev[k.id] !== cur[k.id] ? " (was " + prev[k.id] + ")" : ""}</text>`; }).join("") : "";
    const dots = K.map((k, i) => { const [x, y] = pt(i, cur[k.id]); return `<circle class="rdot" cx="${x}" cy="${y}" r="${labels ? 5 : 3}" fill="${color}" stroke="var(--surface)" stroke-width="2" data-tip="${esc(k.label)}: ${cur[k.id]}/10${prev ? " (last week " + prev[k.id] + ")" : ""}"/>`; }).join("");
    return `<svg class="radar" viewBox="0 0 ${size} ${size}" role="img" aria-label="Style scales">${rings}${spokes}
      ${prev ? `<polygon points="${poly(prev)}" fill="none" stroke="var(--ink-3)" stroke-width="1.5" stroke-dasharray="4 4"/>` : ""}
      <g class="grow" style="transform-origin:${c}px ${c}px"><polygon points="${poly(cur)}" fill="${color}" fill-opacity=".18" stroke="${color}" stroke-width="2" stroke-linejoin="round"/>${dots}</g>${lab}</svg>`;
  }
  function bindDots(root) {
    root.querySelectorAll(".rdot").forEach(d => { d.addEventListener("pointerenter", e => showTip(esc(d.dataset.tip), e.clientX, e.clientY)); d.addEventListener("pointerleave", hideTip); });
  }
  const RULE_LABEL = { risk_pct: "Risk per trade %", max_position_pct: "Max position % of equity", max_trades_day: "Trades per day", target_r: "Target (R)", max_positions: "Positions at once", new_per_day: "New trades per day", trades_week_goal: "Trades per week goal", time_stop_days: "Time stop (days)", stop_atr: "Stop (ATR)", target_atr: "Target (ATR)", buy_vs_50d_max: "Buy up to above 50-day", max_contracts: "Max contracts", dte_min: "Days to expiry (min)", dte_max: "Days to expiry (max)", stop_pct: "Option stop %", take_profit_pct: "Take profit %", put_otm_pct: "Put strike below price", meme_stop_pct: "Meme stop %", news_stop_pct: "News stop %", meme_take_pct: "Meme take %", news_take_pct: "News take %", deploy_frac: "Share of account in market" };
  function scalesPanel(t) {
    const S = t.scales, m = SM(), draft = { scales: { ...S.scales }, sectors: { ...S.sectors } };
    const host = $("#scales");
    const render = () => {
      const eff = effRules(t.id, draft.scales), base = S.effective;
      const changed = Object.keys(draft.scales).filter(k => draft.scales[k] !== S.scales[k]).length + Object.keys(draft.sectors).filter(k => draft.sectors[k] !== S.sectors[k]).length;
      host.innerHTML = `<div class="two">
        <section class="card in"><h3>This week vs last</h3><p class="muted" style="margin:2px 0 0">Solid: now${changed ? " (your draft)" : ""}. Dashed: start of this week. Hover a dot for values.</p>
          <div class="radar-wrap">${radar(draft.scales, t.scales_prev.scales, col(t.id))}</div></section>
        <section class="card in"><h3>Tinker</h3><p class="muted" style="margin:2px 0 10px">Move a slider to preview the rules it drives. Nothing changes until you send it to the desk.</p>
          ${m.knobs.map(k => `<label class="sl"><span><b>${esc(k.label)}</b><i>${esc(k.help)}</i></span><input type="range" min="0" max="10" step="0.1" value="${draft.scales[k.id]}" data-k="${k.id}" aria-label="${esc(k.label)}"><output>${draft.scales[k.id]}</output></label>`).join("")}
          ${S.uses_sectors ? `<h3 style="margin-top:16px">Industry focus</h3><p class="muted" style="margin:2px 0 8px">0 avoids an industry, 5 is neutral, 10 strongly prefers it.</p><div class="secs">${m.sector_list.map(sec => `<label class="sl sm"><span><b>${esc(sec)}</b></span><input type="range" min="0" max="10" step="0.1" value="${draft.sectors[sec]}" data-s="${esc(sec)}" aria-label="${esc(sec)}"><output>${draft.sectors[sec]}</output></label>`).join("")}</div>` : ""}
          <h3 style="margin-top:16px">Rules this produces</h3>
          <div class="rules">${Object.keys(eff).filter(k => RULE_LABEL[k]).map(k => { const d = eff[k] !== base[k]; const v = k === "deploy_frac" ? Math.round(eff[k] * 100) + "%" : eff[k]; return `<div class="${d ? "chg" : ""}"><span>${RULE_LABEL[k]}</span><b>${v}${d ? ` <small>(now ${k === "deploy_frac" ? Math.round(base[k] * 100) + "%" : base[k]})</small>` : ""}</b></div>`; }).join("")}</div>
          <div style="display:flex;gap:8px;margin-top:16px;flex-wrap:wrap"><button class="btn" id="send" ${changed ? "" : "disabled"}>Send ${changed || ""} change${changed === 1 ? "" : "s"} to the desk</button><button class="btn ghost" id="reset">Reset</button></div>
          <p class="muted" style="font-size:12px;margin-top:8px">Opens a pre-filled GitHub issue; submit it and the desk applies it within minutes (logged as "by you").</p></section></div>`;
      bindDots(host);
      host.querySelectorAll("input[type=range]").forEach(r => {
        const set = () => { const v = Math.round(+r.value * 10) / 10; if (r.dataset.k) draft.scales[r.dataset.k] = v; else draft.sectors[r.dataset.s] = v; return v; };
        // while dragging: only the number moves (rebuilding the panel would drop the slider out from under the finger)
        r.oninput = () => { const v = set(); const o = r.parentElement.querySelector("output"); if (o) o.textContent = v.toFixed(1).replace(/\.0$/, ""); };
        // on release: redraw the radar, the rule preview and the send button
        r.onchange = () => { set(); const y = scrollY; render(); scrollTo({ top: y });
          const again = host.querySelector(r.dataset.k ? `[data-k="${r.dataset.k}"]` : `[data-s="${r.dataset.s}"]`); if (again) again.focus({ preventScroll: true }); };
      });
      $("#reset").onclick = () => { draft.scales = { ...S.scales }; draft.sectors = { ...S.sectors }; render(); };
      $("#send").onclick = () => {
        const doc = { trader: t.id, scales: {}, sectors: {}, why: "Changed from the dashboard" };
        for (const k in draft.scales) if (draft.scales[k] !== S.scales[k]) doc.scales[k] = draft.scales[k];
        for (const k in draft.sectors) if (draft.sectors[k] !== S.sectors[k]) doc.sectors[k] = draft.sectors[k];
        const body = "```json\n" + JSON.stringify(doc, null, 1) + "\n```\n\nSent from the Paper Desk dashboard. Edit the \"why\" above if you like, then submit.";
        open(`https://github.com/${D.repo}/issues/new?title=${encodeURIComponent("Desk settings: " + t.name)}&body=${encodeURIComponent(body)}`, "_blank", "noopener");
      };
    };
    render();
  }
  function historyList(rows) {
    if (!rows.length) return `<div class="tw"><div class="empty">No scale changes yet. The Thursday meeting reviews every trader's week and adjusts.</div></div>`;
    const lab = Object.fromEntries(SM().knobs.map(k => [k.id, k.label]));
    return `<div class="tw"><table><thead><tr><th>When</th><th>Trader</th><th>Scale</th><th class="r">Change</th><th>By</th><th>Why</th></tr></thead><tbody>${rows.map(h => { const t = D.traders.find(x => x.id === h.trader) || {}; const up = h.new > h.old; return `<tr><td class="num">${when(h.ts)}<div class="muted">${esc(h.week)}</div></td><td><span class="sw" style="background:${col(h.trader)}"></span> ${esc(t.name)}</td><td>${esc(h.knob.startsWith("sector:") ? "Focus: " + h.knob.slice(7) : lab[h.knob] || h.knob)}</td><td class="r"><span class="${up ? "up" : "down"}">${h.old} ${up ? "▲" : "▼"} ${h.new}</span></td><td><span class="pill">${esc(h.by)}</span></td><td class="why">${esc(h.why)}</td></tr>`; }).join("")}</tbody></table></div>`;
  }

  // ---------- views
  function overview() {
    const T = D.traders, total = T.reduce((s, t) => s + t.equity, 0), start = T.reduce((s, t) => s + t.put_in, 0);
    const wk = Object.entries(D.bonuses || {}).sort().pop();
    const recent = D.traders.flatMap(t => t.trades).sort((a, b) => b.ts.localeCompare(a.ts)).slice(0, 15);
    const ranked = [...T].sort((a, b) => b.return - a.return);
    app.innerHTML = `
      <section class="hero in"><p class="eyebrow"><span class="pulse"></span> Seven paper traders · started ${esc(D.start_date)}</p>
        <div class="hero-num"><span class="big" data-count="${total}">${usd(total)}</span>
        <span class="chip ${total >= start ? "ok" : ""}"><span class="${cls(total - start)}">${arrow(total - start)} ${signed(total - start)} (${pct(total / start - 1)})</span></span></div>
        <p class="lede">Combined paper equity on ${usd(start, 0)} put in (C$3,000 each plus weekly bonuses). Updated ${when(D.updated)} Toronto time. The page checks for new data every 3 minutes: the desk updates every 5-15 minutes in market hours, every 2 hours overnight and on weekends (crypto and futures).</p></section>
      ${(D.bonus_hold || []).includes(D.week) ? `<section class="card in winner"><span class="trophy">🏆</span><div><p class="eyebrow">${esc(D.week)}</p><h3>No bonus this week</h3><p class="muted" style="margin:2px 0 0">The owner forfeited this week's C$${D.weekly_bonus}: no payout and nothing carries over. The review and scale changes still happen on Saturday.</p></div></section>` : wk ? `<section class="card in winner"><span class="trophy">🏆</span><div><p class="eyebrow">Winner of ${esc(wk[0])}</p><h3>${esc(wk[1].name)} earned ${signed(wk[1].week_pnl)} and gets C$${wk[1].bonus} added</h3><p class="muted" style="margin:2px 0 0">Every Saturday, in the weekly review, the week's biggest earner gets C$${D.weekly_bonus}.</p></div></section>` : `<section class="card in winner"><span class="trophy">🏆</span><div><h3>C$${D.weekly_bonus} to this week's biggest earner</h3><p class="muted" style="margin:2px 0 0">Paid Saturday in the weekly review. Leader right now: ${esc(ranked[0].name)}.</p></div></section>`}
      <h2>Leaderboard</h2>
      <section class="grid">${ranked.map((t, i) => `
        <article class="card click in" data-go="t/${t.id}" tabindex="0" role="link" aria-label="${esc(t.name)}">
          <div class="top">${avatar(t)}<div><h3>${i + 1}. ${esc(t.name)} ${t.trophies ? `<span title="weekly wins">${"🏆".repeat(Math.min(t.trophies, 5))}</span>` : ""}</h3><div class="sub">${esc(t.title)}</div></div></div>
          <div class="eq" data-count="${t.equity}">${usd(t.equity)}</div>
          <div class="sub"><span class="${cls(t.return)}">${arrow(t.return)} ${pct(t.return)}</span> · ${t.positions.length} open · ${t.stats.trades} trades ${t.tfsa ? "" : "· sandbox"}</div>
          ${spark(t.series, col(t.id))}
        </article>`).join("")}</section>
      <h2>How each one trades</h2><p class="muted" style="margin:-6px 0 14px">Style scales, 0-10. Dashed: start of this week. Tap a trader to tinker.</p>
      <section class="grid styles">${T.map(t => `<article class="card click in" data-go="t/${t.id}" tabindex="0" role="link"><div class="top">${avatar(t)}<div><h3>${esc(t.name)}</h3><div class="sub">${esc(t.title)}</div></div></div>${radar(t.scales.scales, t.scales_prev.scales, col(t.id), 220, false)}<div class="mini-k">${SM().knobs.map(k => `<span>${esc(k.label.split(" ")[0])} <b>${t.scales.scales[k.id]}</b></span>`).join("")}</div></article>`).join("")}</section>
      <h2>Equity, all traders</h2>
      <section class="card chart-card in"><div class="legend" id="lg">${T.map(t => `<button aria-pressed="true" data-id="${t.id}"><span class="sw" style="background:${col(t.id)}"></span>${esc(t.name)}</button>`).join("")}</div><div id="ch"></div></section>
      <h2>Latest trades</h2>${tradeTable(recent, true)}`;
    const series = T.map(t => ({ id: t.id, name: t.name, color: col(t.id), points: t.series, hidden: false }));
    const draw = () => lineChart($("#ch"), series, { base: 3000, label: "Equity of each trader over time" });
    draw();
    $("#lg").onclick = e => { const b = e.target.closest("button"); if (!b) return; const s = series.find(x => x.id === b.dataset.id); s.hidden = !s.hidden; b.setAttribute("aria-pressed", String(!s.hidden)); draw(); };
    countUp(app); drawLines(app); bindDots(app);
  }

  function trader(id) {
    const t = D.traders.find(x => x.id === id); if (!t) return overview();
    const st = t.stats, pv = t.equity - t.cash;
    const parts = [["Cash", Math.max(0, t.cash), css("--ink-3")]].concat(t.positions.map((p, i) => [p.symbol, Math.abs((p.kind === "option" ? (p.mark ?? p.entry) * 100 : p.kind === "future" ? p.margin || 0 : (p.last ?? p.entry)) * p.qty), col(t.id)]));
    const tot = parts.reduce((s, p) => s + p[1], 0) || 1;
    const goal = t.rules.trades_week_goal ? ` of ${t.rules.trades_week_goal} goal` : "";
    app.innerHTML = `
      <section class="hero in"><p class="eyebrow">${esc(t.title)} · ${esc(t.account)}</p>
        <h1 style="display:flex;align-items:center;gap:14px">${avatar(t)}${esc(t.name)}</h1>
        <p class="lede">${esc(t.style)}</p>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:14px">${badge(t)}${t.tfsa_mirror ? `<span class="chip">TFSA stand-in: ${esc(t.tfsa_mirror)}</span>` : ""}</div>
        <div class="hero-num" style="margin-top:22px"><span class="big" data-count="${t.equity}">${usd(t.equity)}</span><span class="chip"><span class="${cls(t.return)}">${arrow(t.return)} ${pct(t.return)} since start</span></span></div></section>
      <section class="stats in" style="margin-top:22px">
        <div class="stat"><span>Put in</span><b>${usd(t.put_in, 0)}</b></div>
        <div class="stat"><span>Cash</span><b>${usd(t.cash)}</b></div>
        <div class="stat"><span>In positions</span><b>${usd(pv)}</b></div>
        <div class="stat"><span>Realized P/L</span><b class="${cls(st.realized)}">${signed(st.realized)}</b></div>
        <div class="stat"><span>Win rate</span><b>${st.win_rate == null ? "-" : Math.round(st.win_rate * 100) + "%"}</b></div>
        <div class="stat"><span>Trades this week</span><b>${t.week_trades}${goal}</b></div>
        <div class="stat"><span>Max drawdown</span><b>${pct(st.max_drawdown)}</b></div>
        <div class="stat"><span>Fees paid</span><b>${usd(st.fees)}</b></div></section>
      <div class="two" style="margin-top:16px">
        <section class="card chart-card in"><h3>Equity</h3><p class="muted" style="margin:2px 0 6px">Dashed line: the C$3,000 start.</p><div id="ch"></div></section>
        <section class="card in"><h3>Where the money is</h3><div class="alloc" role="img" aria-label="Allocation">${parts.map(p => `<i style="flex-grow:${p[1] / tot};background:${p[2]}" title="${esc(p[0])}"></i>`).join("")}</div>
          <div style="margin-top:12px">${parts.map(p => `<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--line)"><span><span class="sw" style="background:${p[2]}"></span> ${esc(p[0])}</span><span class="num">${usd(p[1])} · ${Math.round(p[1] / tot * 100)}%</span></div>`).join("")}</div>
</section></div>
      <h2>Style scales</h2><div id="scales"></div>
      <h2>Open positions</h2>${posTable(t)}
      ${t.pending.length ? `<h2>Working orders</h2><div class="tw"><table><thead><tr><th>Symbol</th><th>Type</th><th class="r">Qty</th><th class="r">Trigger</th><th class="r">Stop / Target</th><th>Why</th></tr></thead><tbody>${t.pending.map(o => `<tr><td class="sym">${esc(o.symbol)}</td><td>${esc(o.entry_type)} ${esc(o.side)}</td><td class="r">${(+o.qty).toLocaleString("en-US", { maximumFractionDigits: 6 })}</td><td class="r">${o.price ? usd(o.price) : "next bar"}</td><td class="r">${o.stop ? usd(o.stop) : "-"} / ${o.target ? usd(o.target) : "-"}</td><td class="why">${esc(o.why)}</td></tr>`).join("")}</tbody></table></div>` : ""}
      <h2>Every trade: what, why, how much</h2>
      <div class="filters" id="flt"><button aria-pressed="true" data-f="all">All</button><button aria-pressed="false" data-f="open">Opens</button><button aria-pressed="false" data-f="close">Closes</button><button aria-pressed="false" data-f="win">Winners</button><button aria-pressed="false" data-f="loss">Losers</button></div>
      <div id="tt">${tradeTable(t.trades, false)}</div>
      <h2>Scale changes</h2>${historyList(D.scale_history.filter(h => h.trader === t.id))}`;
    scalesPanel(t);
    lineChart($("#ch"), [{ id: t.id, name: t.name, color: col(t.id), points: t.series }], { base: t.capital, label: t.name + " equity" });
    $("#flt").onclick = e => {
      const b = e.target.closest("button"); if (!b) return;
      $("#flt").querySelectorAll("button").forEach(x => x.setAttribute("aria-pressed", String(x === b)));
      const f = b.dataset.f, rows = t.trades.filter(r => f === "all" || (f === "open" && r.pnl === "") || (f === "close" && r.pnl !== "") || (f === "win" && +r.pnl > 0) || (f === "loss" && r.pnl !== "" && +r.pnl <= 0));
      $("#tt").innerHTML = tradeTable(rows, false);
    };
    countUp(app); drawLines(app);
  }

  function reviewsHtml() {
    const r = (D.reviews || [])[0];
    if (!r) return `<h2>Saturday weekly review</h2><div class="tw"><div class="empty">The first review comes on Saturday: insights for all 7, scale changes and the C$${D.weekly_bonus} bonus.</div></div>`;
    const b = (D.bonuses || {})[r.week];
    return `<h2>Saturday weekly review · ${esc(r.week)}</h2><section class="card in">${r.summary ? `<p class="lede" style="font-size:17px">${esc(r.summary)}</p>` : ""}${b ? `<p style="margin:10px 0 0"><span class="chip ok">🏆 ${esc(b.name)} won C$${b.bonus} (${signed(b.week_pnl)} this week)</span></p>` : ""}
      ${Object.entries(r.weekly_review || {}).map(([id, notes]) => { const t = D.traders.find(x => x.id === id) || { id, name: id }; const adj = (r.adjustments || {})[id]; return `<div class="plan-tr">${avatar(t)}<div><h3>${esc(t.name)}</h3><ul style="margin:6px 0 0 18px;padding:0;color:var(--ink-2)">${(notes || []).map(n => `<li>${esc(n)}</li>`).join("")}</ul>${adj ? `<div class="order">Scales: ${Object.entries(adj.scales || {}).map(([k, v]) => `<span class="tag">${esc(k)} → ${v}</span>`).join("")}${Object.entries(adj.sectors || {}).map(([k, v]) => `<span class="tag">${esc(k)} → ${v}</span>`).join("")}<div style="color:var(--ink-2)">${esc(adj.why || "")}</div></div>` : ""}</div></div>`; }).join("")}</section>`;
  }

  function meeting() {
    const plans = D.plans;
    if (!plans.length) { app.innerHTML = `<section class="hero in"><p class="eyebrow">7 AM meeting</p><h1>No meeting yet</h1><p class="lede">The first meeting runs at 7 AM Toronto time on the next trading day. Until then each trader runs on its own rules.</p></section>` + reviewsHtml() + `<h2>Every scale change</h2>${historyList(D.scale_history)}`; return; }
    const whenText = w => { w = w || {}; const T = { time_after: () => `after ${w.time_et} ET`, price_below: () => `${w.symbol} trades below ${w.level}`, price_above: () => `${w.symbol} trades above ${w.level}`, vix_above: () => `VIX above ${w.level}`, vix_below: () => `VIX below ${w.level}`, unfilled_by: () => `${w.symbol} still not bought by ${w.time_et} ET`, stopped_out: () => `stopped out of ${w.symbol}`, day_loss_over: () => `down more than ${w.pct}% today` }; return (T[w.type] || (() => "condition not recognised"))(); };
    const orderHtml = o => `<div class="order"><span class="sym">${esc(o.symbol)}</span> <span class="pill">${esc(o.kind || "stock")}${o.right ? " " + esc(o.right) : ""}</span> ${o.price ? "at " + usd(o.price) : ""} ${o.stop ? "· stop " + usd(o.stop) : ""} ${o.target ? "· target " + usd(o.target) : ""}<div style="color:var(--ink-2);margin-top:4px">${esc(o.why)}</div></div>`;
    const planBlocks = (label, blocks) => (blocks || []).map(b => `<div class="order planb"><span class="pill">${label}</span> <b>If ${esc(whenText(b.when))}</b><div style="color:var(--ink-2);margin-top:4px">${esc(b.note || "")}</div>${(b.orders || []).map(orderHtml).join("") || '<div class="muted" style="margin-top:6px">Stand aside: no orders.</div>'}</div>`).join("");
    const ifThen = list => (list || []).length ? `<ul class="ifthen">${list.map(x => `<li><b>If</b> ${esc(x.if)} <b>→</b> ${esc(x.then)}</li>`).join("")}</ul>` : "";
    const show = i => {
      const p = plans[i];
      $("#pl").innerHTML = `<section class="card in"><p class="lede" style="font-size:17px">${esc(p.market_view)}</p>
        <div style="margin-top:12px">${(p.tabled || []).map(s => `<span class="tag">${esc(s)}</span>`).join("")}</div>
        ${(p.shared_notes || []).length ? `<ul style="color:var(--ink-2);margin:10px 0 0 18px;padding:0">${p.shared_notes.map(n => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}</section>
        ${(p.events || []).length ? `<h2>Today's calendar</h2><section class="card in">${p.events.map(e => `<div class="order"><b>${esc(e.time_et || "")}</b> ${esc(e.name || "")} <span class="pill">${esc(e.status || "")}</span>${e.actual ? `<div style="color:var(--ink-2);margin-top:4px">Result: ${esc(e.actual)}${e.reaction ? " · " + esc(e.reaction) : ""}</div>` : ""}</div>`).join("")}</section>` : ""}
        ${(p.scenarios || []).length ? `<h2>If this happens…</h2><section class="card in">${ifThen(p.scenarios)}</section>` : ""}
        ${p.weekly_review ? `<h2>Weekly review</h2><section class="card in">${Object.entries(p.weekly_review).map(([id, notes]) => { const t = D.traders.find(x => x.id === id) || { id, name: id }; return `<div class="plan-tr">${avatar(t)}<div><h3>${esc(t.name)}</h3><ul style="margin:6px 0 0 18px;padding:0;color:var(--ink-2)">${(notes || []).map(n => `<li>${esc(n)}</li>`).join("")}</ul>${p.adjustments && p.adjustments[id] ? `<div class="order">Scales: ${Object.entries(p.adjustments[id].scales || {}).map(([k, v]) => `<span class="tag">${esc(k)} → ${v}</span>`).join("")}${Object.entries(p.adjustments[id].sectors || {}).map(([k, v]) => `<span class="tag">${esc(k)} → ${v}</span>`).join("")}<div style="color:var(--ink-2)">${esc(p.adjustments[id].why || "")}</div></div>` : ""}</div></div>`; }).join("")}</section>` : ""}
        <h2>Plan by trader</h2><section class="card in" style="margin-top:16px">${Object.entries(p.traders || {}).map(([id, tp]) => { const t = D.traders.find(x => x.id === id) || { id, name: id, title: "" }; return `
          <div class="plan-tr">${avatar(t)}<div><h3>${esc(t.name)} <span class="muted" style="font-weight:500">· ${esc(t.title)}</span></h3><p style="margin:4px 0 0;color:var(--ink-2)">${esc(tp.view)}</p>
          ${(tp.watch || []).map(s => `<span class="tag" style="margin-top:8px">${esc(s)}</span>`).join("")}
          ${ifThen(tp.playbook)}
          ${(tp.orders || []).length ? `<div class="muted" style="margin-top:8px"><b>Plan A</b></div>` : ""}${(tp.orders || []).map(orderHtml).join("") || '<div class="muted" style="margin-top:6px">No orders: autopilot rules.</div>'}
          ${planBlocks("Plan B", tp.plan_b)}${planBlocks("Plan C", tp.plan_c)}
          ${(tp.lessons_applied || []).length ? `<div class="order"><span class="pill">Lessons applied</span><ul class="ifthen">${tp.lessons_applied.map(x => `<li>${esc(x)}</li>`).join("")}</ul></div>` : ""}
          ${tp.failsafe ? `<div class="order failsafe"><span class="pill">Failsafe</span> ${esc(tp.failsafe)}</div>` : ""}</div></div>`; }).join("")}</section>`;
    };
    app.innerHTML = `<section class="hero in"><p class="eyebrow">7 AM meeting · shared plan</p><h1>The morning plan</h1>
      <p class="lede">All seven traders read the morning meeting's plan, its levels and each other's books before the scanner runs. Weekly insights, scale changes and the C$${D.weekly_bonus} reward happen in the Saturday review.</p>
      <div style="margin-top:14px"><select id="ps" aria-label="Choose a meeting">${plans.map((p, i) => `<option value="${i}">${esc(p.date)}</option>`).join("")}</select></div></section><div id="pl"></div>`;
    $("#ps").onchange = e => show(+e.target.value); show(0);
    app.insertAdjacentHTML("beforeend", reviewsHtml() + `<h2>Every scale change</h2>${historyList(D.scale_history)}`);
  }

  function scanner() {
    const pm = D.scans.premarket || {}, id = D.scans.intraday || {};
    const lists = Object.entries(pm.lists || {});
    const names = { small_cap_movers: "Small-cap movers", breakout_20d: "20-day breakouts", pullback_uptrend: "Pullbacks in uptrends", momentum_options: "Momentum (options)", oversold: "Oversold" };
    const tbl = rows => rows.length ? `<div class="tw"><table><thead><tr><th>Symbol</th><th class="r">Price</th><th class="r">1 day</th><th class="r">5 days</th><th class="r">RSI</th><th class="r">Volume vs avg</th><th class="r">Daily range</th></tr></thead><tbody>${rows.map(r => `<tr><td class="sym">${esc(r.symbol)}</td><td class="r">${usd(r.price)}</td><td class="r ${cls(r.d1)}">${pct(r.d1)}</td><td class="r ${cls(r.d5)}">${pct(r.d5)}</td><td class="r">${r.rsi == null ? "-" : Math.round(r.rsi)}</td><td class="r">${r.rel_vol == null ? "-" : r.rel_vol.toFixed(1) + "x"}</td><td class="r">${pct(r.atr_pct)}</td></tr>`).join("")}</tbody></table></div>` : `<div class="tw"><div class="empty">Nothing matched.</div></div>`;
    app.innerHTML = `<section class="hero in"><p class="eyebrow">Hybrid scanner · US + Canada</p><h1>What the scanner sees</h1>
      <p class="lede">Pre-market: ${pm.scanned ? pm.scanned.toLocaleString() : "-"} symbols scanned (${pm.with_data ? pm.with_data.toLocaleString() : "-"} with data), tabled tickers first. Intraday: ${id.candidates || "-"} live candidates. ${pm.asof ? "Last run " + when(pm.asof) + " Toronto time." : ""}</p></section>
      ${Object.keys(pm.tabled || {}).length ? `<h2>Tabled at the meeting</h2>${tbl(Object.entries(pm.tabled).map(([s, m]) => ({ symbol: s, ...m })))}` : ""}
      ${(id.rows || []).length ? `<h2>Intraday gappers</h2><div class="tw"><table><thead><tr><th>Symbol</th><th class="r">Price</th><th class="r">Gap</th><th class="r">Change</th><th class="r">Volume pace</th><th class="r">15-min range</th></tr></thead><tbody>${id.rows.slice(0, 30).map(r => `<tr><td class="sym">${esc(r.symbol)}${r.tabled ? ' <span class="pill">tabled</span>' : ""}</td><td class="r">${usd(r.price)}</td><td class="r ${cls(r.gap_pct)}">${pct(r.gap_pct / 100)}</td><td class="r ${cls(r.chg_pct)}">${pct(r.chg_pct / 100)}</td><td class="r">${r.rel_vol.toFixed(1)}x</td><td class="r">${usd(r.or_low)} - ${usd(r.or_high)}</td></tr>`).join("")}</tbody></table></div>` : ""}
      <div class="filters" id="sf" style="margin-top:30px">${lists.map(([k], i) => `<button aria-pressed="${i === 0}" data-k="${k}">${esc(names[k] || k)}</button>`).join("")}</div><div id="sl">${lists.length ? tbl(lists[0][1]) : ""}</div>`;
    const sf = $("#sf"); if (sf) sf.onclick = e => { const b = e.target.closest("button"); if (!b) return; sf.querySelectorAll("button").forEach(x => x.setAttribute("aria-pressed", String(x === b))); $("#sl").innerHTML = tbl(pm.lists[b.dataset.k]); };
  }

  function reports() {
    app.innerHTML = `<section class="hero in"><p class="eyebrow">Weekly summary</p><h1>Reports</h1><p class="lede">A PDF of every account is made each Saturday, with the weekly review.</p></section>
      <section class="grid">${D.weekly.length ? D.weekly.map(f => `<a class="card click in" href="weekly/${encodeURIComponent(f)}" target="_blank" rel="noopener"><h3>📄 ${esc(f.replace(".pdf", ""))}</h3><div class="sub">Weekly summary PDF</div></a>`).join("") : `<div class="card"><div class="empty">The first weekly PDF arrives on Saturday.</div></div>`}</section>
      <h2>Desk activity</h2><div class="tw"><table><thead><tr><th>When</th><th>Trader</th><th>Event</th><th>Detail</th></tr></thead><tbody>${D.log.slice(0, 80).map(l => `<tr><td class="num">${when(l.ts)}</td><td>${esc((D.traders.find(t => t.id === l.trader) || {}).name || "")}</td><td><span class="pill">${esc(l.event)}</span></td><td class="why">${esc(l.symbol || "")} ${esc(l.reason || l.why || l.error || "")}</td></tr>`).join("")}</tbody></table></div>`;
  }

  // ---------- routing
  const TABS = () => [["overview", "Overview"]].concat(D.traders.map(t => ["t/" + t.id, t.name, t.id])).concat([["meeting", "Meeting"], ["scanner", "Scanner"], ["reports", "Reports"]]);
  function route() {
    if (!D) return;
    const h = (location.hash || "#overview").slice(1);
    tabs.innerHTML = TABS().map(([k, label, id]) => `<button role="tab" aria-selected="${h === k}" data-go="${k}">${id ? `<span class="dot" style="background:${col(id)}"></span>` : ""}${esc(label)}</button>`).join("");
    const sel = tabs.querySelector('[aria-selected="true"]'); if (sel) sel.scrollIntoView({ block: "nearest", inline: "center" });
    const render = () => { hideTip(); if (h.startsWith("t/")) trader(h.slice(2)); else ({ meeting, scanner, reports }[h] || overview)(); if (!quiet) scrollTo({ top: 0 }); };
    if (quiet) { const y = scrollY; render(); scrollTo({ top: y }); return; }   // data refresh: no animations, keep the scroll position
    document.startViewTransition ? document.startViewTransition(render) : render();
  }
  let quiet = false;
  function load(silent) {
    return fetch("data.json?" + Date.now(), { cache: "no-store" }).then(r => r.json()).then(d => {
      if (silent && D && d.updated === D.updated) return;
      D = d; quiet = !!silent; document.body.classList.toggle("quiet", quiet); route(); quiet = false;
    });
  }
  document.addEventListener("click", e => { const g = e.target.closest("[data-go]"); if (g) location.hash = g.dataset.go; });
  document.addEventListener("keydown", e => { if (e.key === "Enter" && e.target.dataset?.go) location.hash = e.target.dataset.go; });
  addEventListener("hashchange", () => { document.body.classList.remove("quiet"); route(); });
  // phones fire "resize" when the address bar hides on the first scroll: only a real width change re-lays the charts
  let rz, lastW = document.documentElement.clientWidth;
  addEventListener("resize", () => { const w = document.documentElement.clientWidth; if (Math.abs(w - lastW) < 40) return; lastW = w; clearTimeout(rz); rz = setTimeout(() => { quiet = true; document.body.classList.add("quiet"); route(); quiet = false; }, 250); });
  setInterval(() => { if (!document.hidden) load(true).catch(() => {}); }, 3 * 60 * 1000);   // new data every few minutes
  document.addEventListener("visibilitychange", () => { if (!document.hidden) load(true).catch(() => {}); });
  load(false).catch(() => { app.innerHTML = `<div class="empty">Could not load data.json.</div>`; });
})();
