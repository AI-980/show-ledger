const sleeveEl = document.querySelector("#sleeve");
const hero = document.querySelector("#hero");
const leadersEl = document.querySelector("#leaders");
const weeksEl = document.querySelector("#weeks");
const panel = document.querySelector("#panel");
const syncState = document.querySelector("#sync-state");
const syncBtn = document.querySelector("#sync-btn");

const money = new Intl.NumberFormat("zh-TW", { maximumFractionDigits: 0 });
const price = new Intl.NumberFormat("zh-TW", { maximumFractionDigits: 2 });

const BADGE = {
  win: "獲利",
  loss: "敗",
  push: "平",
  open: "持有",
  pending: "待買",
  unfilled: "買不起",
  no_price: "無報價",
};

let data = null;
let selected = null;
let timer = null;
let readOnly = false;

function pct(value) {
  if (value == null || Number.isNaN(value)) return "—";
  const number = value * 100;
  const sign = number > 0 ? "+" : "";
  return `${sign}${number.toFixed(1)}%`;
}

function signed(value) {
  if (value == null) return "—";
  const rounded = Math.round(value);
  if (rounded > 0) return `+${money.format(rounded)}`;
  return money.format(rounded);
}

function tone(value) {
  if (value > 0) return "up";
  if (value < 0) return "down";
  return "";
}

function rateText(value) {
  if (value == null) return "—";
  return `${Math.round(value * 1000) / 10}%`;
}

function cell(text, className) {
  const td = document.createElement("td");
  td.textContent = text;
  if (className) td.className = className;
  return td;
}

async function load() {
  let response;
  try {
    response = await fetch("/api/dashboard", { cache: "no-store" });
    if (!response.ok) throw new Error(String(response.status));
    readOnly = false;
  } catch {
    response = await fetch("dashboard.json", { cache: "no-store" });
    readOnly = true;
  }
  data = await response.json();
  const sync = data.sync || {};
  const when = data.syncedAt ? ` · ${data.syncedAt.replace("T", " ").replace(/[+-]\d{2}:\d{2}$/, "")}` : "";
  syncBtn.hidden = readOnly;
  syncState.textContent = readOnly
    ? `雲端帳本${when}`
    : `${sync.message || "待命"}${when}`;
  syncBtn.disabled = Boolean(sync.running);
  if (sync.error && !readOnly) syncState.textContent = `更新失敗：${sync.error}`;
  render();
  const waiting = sync.running || (data.summary.episodes === 0 && !sync.error);
  clearTimeout(timer);
  if (waiting) timer = setTimeout(load, 2000);
}

function render() {
  renderSleeve();
  renderHero();
  renderLeaders();
  renderWeeks();
  renderPanel();
}

function renderSleeve() {
  sleeveEl.replaceChildren();
  const book = data.portfolio;
  if (!book) return;
  const heading = document.createElement("h2");
  heading.textContent = "5 萬元，最多 10 檔";
  const note = document.createElement("p");
  note.className = "note";
  note.textContent = "只從 10/1 播出之後、節目點到名的股票裡挑。股價要低於 300 元，ETF 不買。播出當天的收盤還要站上 5 日均線，而且近 5 個交易日上漲，才各買 5,000 元。同一天有多檔符合，先買近 5 日漲較多的。持有滿 10 檔或投入滿 5 萬就不再買，等其中一檔漲滿 10% 賣出，空出額度再補。這是用當時已經收盤的價格做篩選，沒有讀財報或新聞，也不是預測。";
  const counts = book.counts || {};
  const summary = document.createElement("p");
  summary.className = "meta";
  summary.textContent = `持有 ${book.openCount} / ${book.slots} 檔 · 投入 ${money.format(Math.round(book.deployed))} / ${money.format(book.capital)} · 已獲利 ${book.wins} 筆 · 已實現 ${signed(book.realized)} · 看過 ${counts.seen} 檔，300 元以上 ${counts.expensive || 0} 檔，ETF ${counts.etf || 0} 檔，線型不符 ${counts.rejected} 檔`;
  sleeveEl.append(heading, note, summary);
  if (book.open.length) {
    sleeveEl.append(sleeveHeading("持有中"), sleeveTable(
      ["進場", "股票", "股數", "進場價", "現價", "損益", "報酬"],
      book.open.map((item) => [
        item.entryLabel,
        `${item.name} ${item.symbol}`,
        String(item.shares),
        price.format(item.entryPrice),
        item.markPrice == null ? "—" : price.format(item.markPrice),
        item.pnl == null ? "—" : `估 ${signed(item.pnl)}`,
        item.pnlPct == null ? "—" : pct(item.pnlPct),
      ]),
      book.open.map((item) => item.pnl)
    ));
  } else {
    const empty = document.createElement("p");
    empty.className = "note";
    empty.textContent = "目前沒有符合條件而買進的股票。";
    sleeveEl.append(empty);
  }
  if (book.closed.length) {
    sleeveEl.append(sleeveHeading("已漲滿 10%"), sleeveTable(
      ["進場", "賣出", "股票", "股數", "損益", "報酬"],
      book.closed.map((item) => [
        item.entryLabel,
        item.exitLabel,
        `${item.name} ${item.symbol}`,
        String(item.shares),
        signed(item.pnl),
        pct(item.pnlPct),
      ]),
      book.closed.map((item) => item.pnl)
    ));
  }
  if (book.blocked.length) {
    sleeveEl.append(sleeveHeading("符合條件但沒買"), sleeveTable(
      ["進場", "股票", "原因", "當時近 5 日"],
      book.blocked.map((item) => [
        item.entryLabel,
        `${item.name} ${item.symbol}`,
        item.reason,
        pct(item.momentum5),
      ]),
      book.blocked.map((item) => item.momentum5)
    ));
  }
}

function sleeveHeading(text) {
  const heading = document.createElement("h3");
  heading.textContent = text;
  return heading;
}

function sleeveTable(headers, rows, tones) {
  const wrap = document.createElement("div");
  wrap.className = "table-wrap";
  const table = document.createElement("table");
  const head = document.createElement("tr");
  headers.forEach((label) => {
    const th = document.createElement("th");
    th.textContent = label;
    if (label !== "進場" && label !== "賣出" && label !== "股票") th.className = "num";
    head.append(th);
  });
  const thead = document.createElement("thead");
  thead.append(head);
  const body = document.createElement("tbody");
  rows.forEach((values, rowIndex) => {
    const row = document.createElement("tr");
    values.forEach((value, index) => {
      const label = headers[index];
      const numeric = label !== "進場" && label !== "賣出" && label !== "股票";
      const className = numeric && index === values.length - 1 ? `num ${tone(tones[rowIndex])}` : (numeric ? "num" : "");
      row.append(cell(value, className));
    });
    body.append(row);
  });
  table.append(thead, body);
  wrap.append(table);
  return wrap;
}

function renderLeaders() {
  leadersEl.replaceChildren();
  const trends = data.trends || [];
  const heading = document.createElement("h2");
  heading.textContent = "走勢最強";
  const note = document.createElement("p");
  note.className = "note";
  note.textContent = "只在節目點過名的股票裡排。10 月 1 日收盤到最近收盤要上漲，最近 5 個交易日也要漲，而且收盤站上 5 日均線。";
  leadersEl.append(heading, note);
  const strong = trends.filter((item) => item.strong);
  if (!strong.length) {
    const empty = document.createElement("p");
    empty.className = "note";
    empty.textContent = "目前沒有同時符合這三個條件的股票。";
    leadersEl.append(empty);
  } else {
    const cards = document.createElement("div");
    cards.className = "leader-cards";
    strong.forEach((item) => cards.append(leaderCard(item)));
    leadersEl.append(cards);
  }
  if (trends.length) leadersEl.append(trendTable(trends));
}

function leaderCard(item) {
  const card = document.createElement("article");
  card.className = "leader-card";
  const name = document.createElement("strong");
  name.textContent = `${item.name} ${item.symbol}`;
  const ret = document.createElement("p");
  ret.className = `ret ${tone(item.returnSinceStart)}`;
  ret.textContent = pct(item.returnSinceStart);
  const detail = document.createElement("p");
  detail.className = "meta";
  detail.textContent = `近 5 日 ${pct(item.momentum5)} · 已獲利 ${item.wins} 筆`;
  card.append(name, ret, sparkline(item.closes), detail);
  return card;
}

function trendTable(trends) {
  const wrap = document.createElement("div");
  wrap.className = "table-wrap";
  const table = document.createElement("table");
  const head = document.createElement("tr");
  ["股票", "走勢", "10/1 以來", "近 5 日", "5 日均線", "已獲利"].forEach((label, index) => {
    const th = document.createElement("th");
    th.textContent = label;
    if (index >= 2) th.className = "num";
    head.append(th);
  });
  const thead = document.createElement("thead");
  thead.append(head);
  const body = document.createElement("tbody");
  trends.forEach((item) => {
    const row = document.createElement("tr");
    const name = document.createElement("td");
    const title = document.createElement("span");
    title.className = "name";
    title.textContent = `${item.name} ${item.symbol}`;
    name.append(title);
    if (item.strong) {
      const badge = document.createElement("span");
      badge.className = "badge win";
      badge.textContent = "強勢";
      badge.style.marginLeft = "8px";
      name.append(badge);
    }
    const chart = document.createElement("td");
    chart.append(sparkline(item.closes));
    row.append(
      name,
      chart,
      cell(pct(item.returnSinceStart), `num ${tone(item.returnSinceStart)}`),
      cell(pct(item.momentum5), `num ${tone(item.momentum5)}`),
      cell(item.aboveAverage ? "站上" : "跌破", "num"),
      cell(`${item.wins} 筆`, "num")
    );
    body.append(row);
  });
  table.append(thead, body);
  wrap.append(table);
  return wrap;
}

function sparkline(closes) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 96 28");
  svg.setAttribute("class", "spark");
  if (!closes || closes.length < 2) return svg;
  const low = Math.min(...closes);
  const high = Math.max(...closes);
  const span = high - low || 1;
  const points = closes.map((value, index) => {
    const x = (index / (closes.length - 1)) * 96;
    const y = 26 - ((value - low) / span) * 24;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const line = document.createElementNS(svg.namespaceURI, "polyline");
  line.setAttribute("points", points);
  line.setAttribute("fill", "none");
  line.setAttribute("stroke", closes.at(-1) >= closes[0] ? "#c44536" : "#1c7a45");
  line.setAttribute("stroke-width", "2");
  line.setAttribute("stroke-linejoin", "round");
  line.setAttribute("stroke-linecap", "round");
  svg.append(line);
  return svg;
}

function renderHero() {
  hero.replaceChildren();
  const summary = data.summary;
  const latest = data.latestSettled;
  const score = document.createElement("article");
  score.className = "score";
  const kicker = document.createElement("p");
  kicker.className = "kicker";
  kicker.textContent = "10/1 播出起算 · 漲 10% 才算";
  const rate = document.createElement("p");
  rate.className = "rate";
  rate.textContent = rateText(summary.winRate);
  const record = document.createElement("p");
  record.className = "record";
  record.textContent = summary.wins + summary.open
    ? `${summary.wins} 筆已賺到 10% · ${summary.open} 筆還沒到 · 已實現 ${signed(summary.pnl)}`
    : "這個區間還沒有買到的股票。";
  const overall = document.createElement("p");
  overall.className = "meta";
  overall.textContent = latest
    ? `最近一次獲利 ${latest.label}，${latest.wins} 筆，${signed(latest.pnl)}`
    : "還沒有股票漲滿 10%。";
  score.append(kicker, rate, record, overall);

  const side = document.createElement("aside");
  side.className = "side";
  side.append(
    stat("已實現損益", signed(summary.pnl), tone(summary.pnl)),
    stat("獲利投入", money.format(Math.round(summary.cost || 0))),
    stat("報酬率", pct(summary.returnPct), tone(summary.returnPct)),
    stat("持有中", signed(summary.unrealized), tone(summary.unrealized)),
    stat("待買 / 買不起", `${summary.pending} / ${summary.unfilled}`)
  );
  hero.append(score, side);
}

function stat(label, value, className) {
  const row = document.createElement("div");
  row.className = "stat";
  const span = document.createElement("span");
  span.textContent = label;
  const strong = document.createElement("b");
  strong.textContent = value;
  if (className) strong.className = className;
  row.append(span, strong);
  return row;
}

function renderWeeks() {
  weeksEl.replaceChildren();
  if (!data.weeks.length) {
    return;
  }
  if (!data.weeks.some((week) => week.id === selected)) {
    const holding = data.weeks.find((week) => week.holding);
    selected = (holding || data.latestSettled || data.weeks[0]).id;
  }
  data.weeks.forEach((week) => {
    const button = document.createElement("button");
    button.type = "button";
    if (week.id === selected) button.className = "selected";
    const title = document.createElement("strong");
    title.textContent = week.label;
    const detail = document.createElement("em");
    detail.textContent = week.holding
      ? `${week.open} 持有 · ${week.pending} 待買`
      : `${week.wins} 筆獲利 · ${signed(week.pnl)}`;
    button.append(title, detail);
    button.addEventListener("click", () => {
      selected = week.id;
      renderWeeks();
      renderPanel();
    });
    weeksEl.append(button);
  });
}

function renderPanel() {
  panel.replaceChildren();
  const week = data.weeks.find((item) => item.id === selected);
  if (!week) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = data.sync?.running
      ? "正在讀取最近約兩個月的節目和收盤價，第一筆結算單稍後出現。"
      : "還沒有節目資料。按右上角的更新節目再試一次。";
    panel.append(empty);
    return;
  }
  const head = document.createElement("div");
  head.className = "panel-head";
  const titleWrap = document.createElement("div");
  const heading = document.createElement("h2");
  heading.textContent = week.holding ? "還沒到 10%" : `${week.label} 獲利`;
  const sub = document.createElement("p");
  sub.textContent = week.holding
    ? "收盤還沒漲滿進場價的 10%，繼續持有。下面的損益用最近收盤估計。"
    : `${week.wins} 筆在這天漲滿 10% 賣出，已實現 ${signed(week.pnl)}`;
  titleWrap.append(heading, sub);
  head.append(titleWrap);
  panel.append(head);

  const trades = week.episodes.flatMap((episode) =>
    episode.trades.map((trade) => ({ ...trade, episode }))
  );
  if (!trades.length) {
    const note = document.createElement("p");
    note.className = "note";
    note.textContent = "這週的節目還沒辨認出股票。可以在下面手動補上。";
    panel.append(note);
  } else {
    panel.append(renderTable(trades));
  }
  const episodes = document.createElement("div");
  episodes.className = "episodes";
  week.episodes.forEach((episode) => episodes.append(renderEpisode(episode)));
  panel.append(episodes);
}

function renderTable(trades) {
  const wrap = document.createElement("div");
  wrap.className = "table-wrap";
  const table = document.createElement("table");
  const head = document.createElement("tr");
  ["播出", "進場", "股票", "股數", "進場價", "賣出價", "損益", "報酬", "結果"].forEach((label, index) => {
    const th = document.createElement("th");
    th.textContent = label;
    if (index >= 3 && index <= 7) th.className = "num";
    head.append(th);
  });
  const thead = document.createElement("thead");
  thead.append(head);
  const tbody = document.createElement("tbody");
  trades.forEach((trade) => {
    const row = document.createElement("tr");
    row.append(
      cell(trade.broadcastLabel),
      cell(trade.entryLabel),
      nameCell(trade),
      cell(trade.shares == null ? "—" : String(trade.shares), "num"),
      cell(trade.entryPrice == null ? "—" : price.format(trade.entryPrice), "num"),
      cell(exitText(trade), "num"),
      moneyCell(trade),
      cell(trade.pnlPct == null ? "—" : pct(trade.pnlPct), `num ${tone(trade.pnl)}`),
      badgeCell(trade)
    );
    tbody.append(row);
  });
  table.append(thead, tbody);
  wrap.append(table);
  return wrap;
}

function nameCell(trade) {
  const td = document.createElement("td");
  const strong = document.createElement("span");
  strong.className = "name";
  strong.textContent = `${trade.name} ${trade.symbol}`;
  td.append(strong);
  if (trade.snippet) {
    const snippet = document.createElement("span");
    snippet.className = "snippet";
    snippet.textContent = trade.snippet;
    td.append(snippet);
  }
  return td;
}

function moneyCell(trade) {
  const td = document.createElement("td");
  const blank = trade.state === "pending" || trade.state === "unfilled" || trade.state === "no_price";
  td.className = `num ${tone(trade.pnl)}`;
  if (blank || trade.pnl == null) td.textContent = "—";
  else if (trade.state === "open") td.textContent = `估 ${signed(trade.pnl)}`;
  else td.textContent = signed(trade.pnl);
  return td;
}

function badgeCell(trade) {
  const td = document.createElement("td");
  const badge = document.createElement("span");
  badge.className = `badge ${trade.status}`;
  badge.textContent = BADGE[trade.status] || trade.status;
  td.append(badge);
  return td;
}

function exitText(trade) {
  if (trade.state === "settled" && trade.exitPrice != null) return price.format(trade.exitPrice);
  if (trade.state === "open" && trade.markPrice != null) return `${price.format(trade.markPrice)} 估`;
  return "—";
}

function renderEpisode(episode) {
  const card = document.createElement("article");
  card.className = "episode";
  const heading = document.createElement("h3");
  const link = document.createElement("a");
  link.href = episode.url;
  link.target = "_blank";
  link.rel = "noopener";
  link.textContent = episode.title;
  heading.append(link);
  const meta = document.createElement("p");
  meta.className = "meta";
  meta.textContent = `${episode.broadcastLabel}播出 · ${episode.entryLabel}進場`;
  const chips = document.createElement("div");
  chips.className = "chips";
  if (!episode.trades.length) {
    const empty = document.createElement("span");
    empty.className = "snippet";
    empty.textContent = "這一集還沒有辨認出股票。";
    chips.append(empty);
  }
  episode.trades.forEach((trade) => chips.append(chip(episode.videoId, trade)));
  if (readOnly) {
    card.append(heading, meta, chips);
    return card;
  }
  const form = document.createElement("form");
  form.className = "add";
  const input = document.createElement("input");
  input.placeholder = "補上這一集提到的股票，例如富喬或 1815";
  input.setAttribute("aria-label", "補上股票");
  const button = document.createElement("button");
  button.type = "submit";
  button.textContent = "加入";
  const suggest = document.createElement("div");
  suggest.className = "suggest";
  suggest.hidden = true;
  form.append(input, button, suggest);
  const error = document.createElement("p");
  error.className = "form-error";
  card.append(heading, meta, chips, form, error);

  let picks = [];
  input.addEventListener("input", async () => {
    const query = input.value.trim();
    if (query.length < 1) {
      suggest.hidden = true;
      return;
    }
    const response = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
    picks = await response.json();
    suggest.replaceChildren();
    picks.forEach((item) => {
      const choice = document.createElement("button");
      choice.type = "button";
      choice.textContent = `${item.name} ${item.symbol}`;
      choice.addEventListener("click", () => {
        input.value = item.symbol;
        suggest.hidden = true;
      });
      suggest.append(choice);
    });
    suggest.hidden = picks.length === 0;
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.textContent = "";
    const response = await fetch("/api/mentions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ videoId: episode.videoId, query: input.value.trim() }),
    });
    const result = await response.json();
    if (!result.ok) {
      error.textContent = result.message || "沒加上";
      return;
    }
    input.value = "";
    suggest.hidden = true;
    load();
  });
  return card;
}

function chip(videoId, trade) {
  const item = document.createElement("span");
  item.className = "chip";
  const label = document.createElement("span");
  label.textContent = `${trade.name} ${signed(trade.pnl)}`;
  if (trade.state === "settled" || trade.state === "open") label.className = tone(trade.pnl);
  if (readOnly) {
    item.append(label);
    return item;
  }
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "ghost";
  remove.textContent = "刪除";
  remove.addEventListener("click", async () => {
    await fetch("/api/mentions/remove", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ videoId, symbol: trade.symbol }),
    });
    load();
  });
  item.append(label, remove);
  return item;
}

syncBtn.addEventListener("click", async () => {
  syncBtn.disabled = true;
  syncState.textContent = "開始更新";
  await fetch("/api/sync", { method: "POST" });
  load();
});

load();
