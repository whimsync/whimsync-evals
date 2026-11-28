"use strict";

/* extractor-eval results UI. Plain DOM, no build step, no external libraries.
 * Every piece of run data is inserted as text (never innerHTML) except Markdown
 * rendered by the server with raw HTML disabled. */

const $app = document.getElementById("app");
let CATALOG = {};
let CATALOG_LIST = [];

// ---------- utilities ----------

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style") el.style.cssText = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

const local = {
  get(key, fallback) {
    try {
      const v = localStorage.getItem("xe:" + key);
      return v == null ? fallback : JSON.parse(v);
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    try {
      localStorage.setItem("xe:" + key, JSON.stringify(value));
    } catch {
      /* storage unavailable: per-viewer convenience only */
    }
  },
};

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      msg = (await res.json()).detail || msg;
    } catch {
      /* not JSON */
    }
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return res.json();
}
const get = (p) => api("GET", p);

function toast(message, isError) {
  const el = h("div", { class: "toast" + (isError ? " error" : ""), role: "status" }, message);
  document.body.append(el);
  setTimeout(() => el.remove(), isError ? 5000 : 2200);
}

async function attempt(fn, okMessage) {
  try {
    const out = await fn();
    if (okMessage) toast(okMessage);
    return out;
  } catch (e) {
    toast(e.message, true);
    throw e;
  }
}

const enc = encodeURIComponent;
const runLink = (id, rest = "") => `#/run/${enc(id)}${rest}`;

function fmtDate(iso) {
  if (!iso) return "–";
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function spec(key) {
  return CATALOG[key] || { key, label: key, better: "none", unit: "ratio", group: "other" };
}

function fmt(key, v, unitOverride) {
  if (v == null || Number.isNaN(v)) return "–";
  if (typeof v === "boolean") return v ? "yes" : "no";
  const unit = unitOverride || spec(key).unit;
  if (unit === "seconds") return v.toFixed(2) + "s";
  if (unit === "usd") return "$" + v.toFixed(4);
  if (unit === "count") return Number.isInteger(v) ? v.toLocaleString() : v.toFixed(1);
  if (unit === "score5") return v.toFixed(2);
  if (unit === "ms") return Math.round(v) + "ms";
  return v.toFixed(3);
}

/** Client-side verdict for unit-level cells (summary rows use the server's). */
function verdict(key, base, other, specOverride) {
  if (base == null || other == null) return "n/a";
  const s = specOverride || spec(key);
  if (s.better === "none") return "same";
  const d = other - base;
  const tol = { ratio: 0.02, score5: 0.1, flag: 0.02 }[s.unit];
  const significant =
    tol != null ? Math.abs(d) > tol : Math.abs(d) > 0.25 * Math.max(Math.abs(base), 1e-9);
  if (!significant) return "same";
  return d > 0 === (s.better === "higher") ? "better" : "worse";
}

function metricCell(key, value, base, isBase, specOverride, unitOverride) {
  const v = isBase ? "same" : verdict(key, base, value, specOverride);
  const cls = "num" + (v === "better" ? " better" : v === "worse" ? " worse" : "");
  const delta =
    !isBase && value != null && base != null && v !== "same"
      ? h(
          "span",
          { class: "delta" },
          (value - base >= 0 ? "+" : "") + fmt(key, value - base, unitOverride),
        )
      : null;
  return h("td", { class: cls }, fmt(key, value, unitOverride), delta);
}

function badge(text, kind) {
  return h("span", { class: "badge" + (kind ? " " + kind : "") }, text);
}

function statusBadge(status) {
  const kind = {
    complete: "good",
    running: "info",
    interrupted: "warn",
    complete_with_errors: "warn",
    failed: "bad",
    success: "good",
    timeout: "bad",
  }[status];
  return badge((status || "unknown").replaceAll("_", " "), kind);
}

function seg(options, current, onPick) {
  return h(
    "div",
    { class: "seg", role: "group" },
    options.map(([value, label]) =>
      h(
        "button",
        {
          class: value === current ? "on" : "",
          "aria-pressed": String(value === current),
          onclick: () => onPick(value),
        },
        label,
      ),
    ),
  );
}

function setHashParams(path, params) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v != null && v !== "") q.set(k, v);
  const s = q.toString();
  location.hash = path + (s ? "?" + s : "");
}

function kv(pairs) {
  return h(
    "dl",
    { class: "kv" },
    pairs.filter(Boolean).map(([k, v]) => [h("dt", {}, k), h("dd", {}, v ?? "–")]),
  );
}

function jsonBlock(obj) {
  return h("pre", { class: "md-raw" }, JSON.stringify(obj, null, 2));
}

// ---------- selection (compare basket) ----------

const selection = {
  ids() {
    return local.get("selected", []);
  },
  has(id) {
    return this.ids().includes(id);
  },
  toggle(id, on) {
    const set = new Set(this.ids());
    const add = on ?? !set.has(id);
    if (add) set.add(id);
    else set.delete(id);
    local.set("selected", [...set]);
    updateNav();
  },
  clear() {
    local.set("selected", []);
    updateNav();
  },
};

function updateNav() {
  const n = selection.ids().length;
  document.getElementById("nav-compare").textContent = n ? `Compare (${n})` : "Compare";
  const route = location.hash.split("?")[0];
  for (const a of document.querySelectorAll("[data-nav]")) {
    a.classList.toggle(
      "active",
      route.startsWith("#/" + a.dataset.nav) ||
        (a.dataset.nav === "runs" && (route === "" || route === "#/" || route.startsWith("#/run"))),
    );
  }
}

// ---------- shared editors ----------

function groupEditor(record, onChange, allGroups) {
  const wrap = h("span", { class: "chips" });
  const render = (groups) => {
    wrap.replaceChildren(
      ...groups.map((g) =>
        h(
          "span",
          { class: "chip" },
          g,
          h(
            "button",
            {
              title: `Remove from ${g}`,
              "aria-label": `Remove group ${g}`,
              onclick: async (e) => {
                e.stopPropagation();
                const rec = await attempt(() =>
                  api("PATCH", `/api/runs/${enc(record.id)}`, { remove_groups: [g] }),
                );
                render(rec.groups);
                onChange && onChange(rec);
              },
            },
            "×",
          ),
        ),
      ),
      h(
        "button",
        {
          class: "chip-add",
          title: "Add to a group",
          onclick: (e) => {
            e.stopPropagation();
            const input = h("input", {
              type: "text",
              list: "group-list",
              placeholder: "group",
              style: "width:110px;padding:1px 6px",
              "aria-label": "Group name",
            });
            const list = h(
              "datalist",
              { id: "group-list" },
              (allGroups || []).map((g) => h("option", { value: g })),
            );
            const save = async () => {
              const name = input.value.trim();
              if (!name) return render(groups);
              const rec = await attempt(() =>
                api("PATCH", `/api/runs/${enc(record.id)}`, { add_groups: [name] }),
              );
              render(rec.groups);
              onChange && onChange(rec);
            };
            input.addEventListener("keydown", (ev) => {
              if (ev.key === "Enter") save();
              if (ev.key === "Escape") render(groups);
            });
            input.addEventListener("blur", save);
            e.target.replaceWith(input, list);
            input.focus();
          },
        },
        "+ group",
      ),
    );
  };
  render(record.groups || []);
  return wrap;
}

function editableName(record, onSaved, tag = "span") {
  const el = h(tag, { class: "editable", title: "Click to rename", tabindex: "0" }, record.name);
  const start = () => {
    const input = h("input", {
      type: "text",
      value: record.name,
      style: "min-width:260px;font-size:inherit;font-weight:inherit",
      "aria-label": "Run name",
    });
    const finish = async (save) => {
      if (save && input.value.trim() && input.value.trim() !== record.name) {
        const rec = await attempt(
          () => api("PATCH", `/api/runs/${enc(record.id)}`, { name: input.value.trim() }),
          "Renamed",
        );
        record.name = rec.name;
        onSaved && onSaved(rec);
      }
      el.textContent = record.name;
      input.replaceWith(el);
    };
    input.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") finish(true);
      if (ev.key === "Escape") finish(false);
    });
    input.addEventListener("blur", () => finish(true));
    el.replaceWith(input);
    input.focus();
    input.select();
  };
  el.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    start();
  });
  el.addEventListener("keydown", (e) => e.key === "Enter" && start());
  return el;
}

function targetLabel(t) {
  if (!t) return "whole run";
  return [t.case, t.unit, t.tier].filter(Boolean).join(" / ");
}

/** Comment list + form. `filter(c)` picks which comments show; `target` is attached to new ones. */
function commentsPanel({ runId, comments, target, filter, tiers, linkFor, onChange }) {
  const wrap = h("div", { class: "comments" });
  let items = comments.slice();
  const draw = () => {
    const shown = items.filter(filter || (() => true));
    const list = shown.length
      ? shown.map((c) => commentView(c))
      : [h("div", { class: "muted small" }, "No comments yet.")];
    wrap.replaceChildren(...list, form());
  };
  const commentView = (c) => {
    const body = h("div", { class: "body" }, c.text);
    const link =
      linkFor && c.target
        ? h("a", { href: linkFor(c.target) }, targetLabel(c.target))
        : h("span", {}, targetLabel(c.target));
    return h(
      "div",
      { class: "comment" },
      h(
        "div",
        { class: "who" },
        h("b", {}, c.author),
        h("span", {}, fmtDate(c.created_at) + (c.updated_at ? " (edited)" : "")),
        h("span", {}, "on ", link),
        h("span", { style: "flex:1" }),
        h(
          "button",
          {
            class: "ghost small",
            onclick: () => {
              const ta = h("textarea", { "aria-label": "Edit comment" }, c.text);
              const save = h(
                "button",
                {
                  class: "primary",
                  onclick: async () => {
                    const upd = await attempt(
                      () =>
                        api("PATCH", `/api/runs/${enc(runId)}/comments/${c.id}`, {
                          text: ta.value,
                        }),
                      "Saved",
                    );
                    items = items.map((x) => (x.id === c.id ? upd : x));
                    onChange && onChange(items);
                    draw();
                  },
                },
                "Save",
              );
              body.replaceWith(
                h(
                  "div",
                  { class: "comment-form" },
                  ta,
                  h("div", { class: "row" }, save, h("button", { onclick: draw }, "Cancel")),
                ),
              );
            },
          },
          "Edit",
        ),
        h(
          "button",
          {
            class: "ghost small danger",
            onclick: async () => {
              if (!confirm("Delete this comment?")) return;
              await attempt(
                () => api("DELETE", `/api/runs/${enc(runId)}/comments/${c.id}`),
                "Deleted",
              );
              items = items.filter((x) => x.id !== c.id);
              onChange && onChange(items);
              draw();
            },
          },
          "Delete",
        ),
      ),
      body,
    );
  };
  const form = () => {
    const ta = h("textarea", {
      placeholder: "Add a comment… (Ctrl+Enter to post)",
      "aria-label": "New comment",
    });
    const author = h("input", {
      type: "text",
      value: local.get("author", ""),
      placeholder: "your name",
      style: "width:140px",
      "aria-label": "Your name",
    });
    const tierSel =
      tiers && tiers.length
        ? h(
            "select",
            { "aria-label": "Tier" },
            h("option", { value: "" }, "all tiers"),
            tiers.map((t) => h("option", { value: t }, t)),
          )
        : null;
    const post = async () => {
      if (!ta.value.trim()) return;
      local.set("author", author.value.trim());
      const tgt = { ...(target || {}) };
      if (tierSel && tierSel.value) tgt.tier = tierSel.value;
      const c = await attempt(
        () =>
          api("POST", `/api/runs/${enc(runId)}/comments`, {
            text: ta.value,
            author: author.value,
            target: Object.keys(tgt).length ? tgt : null,
          }),
        "Comment added",
      );
      items.push(c);
      onChange && onChange(items);
      draw();
    };
    ta.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) post();
    });
    return h(
      "div",
      { class: "comment-form" },
      ta,
      h(
        "div",
        { class: "row" },
        author,
        tierSel,
        h("span", { class: "muted small" }, "on " + targetLabel(target)),
        h("span", { style: "flex:1" }),
        h("button", { class: "primary", onclick: post }, "Comment"),
      ),
    );
  };
  draw();
  return wrap;
}

// ---------- Markdown panes ----------

const renderCache = new Map();
async function renderMarkdown(md) {
  if (renderCache.has(md)) return renderCache.get(md);
  const out = (await api("POST", "/api/render", { markdown: md })).html;
  renderCache.set(md, out);
  return out;
}

async function diffNodes(a, b) {
  const ops = await api("POST", "/api/diff", { a, b });
  return ops.map(([op, text]) =>
    op === "=" ? document.createTextNode(text) : h(op === "+" ? "ins" : "del", {}, text),
  );
}

/** Fills `body` with text in the given mode: raw | rendered | diff (against `base`). */
async function fillPane(body, text, mode, base) {
  body.replaceChildren(h("div", { class: "muted small" }, "…"));
  if (!text) {
    body.replaceChildren(h("div", { class: "empty" }, "(empty)"));
    return;
  }
  if (mode === "rendered") {
    const div = h("div", { class: "md-render" });
    div.innerHTML = await renderMarkdown(text); // server renders with raw HTML disabled
    body.replaceChildren(div);
  } else if (mode === "diff" && base != null) {
    body.replaceChildren(h("pre", { class: "md-raw" }, await diffNodes(base, text)));
  } else {
    body.replaceChildren(h("pre", { class: "md-raw" }, text));
  }
}

function pagesStrip(caseId, pages) {
  return h(
    "div",
    { class: "pages-strip" },
    pages.slice(0, 12).map((p) =>
      h(
        "figure",
        {},
        h("img", {
          src: `/api/cases/${enc(caseId)}/pages/${p}.png`,
          alt: `Source page ${p}`,
          loading: "lazy",
          onclick: (e) => e.target.classList.toggle("zoom"),
        }),
        h("figcaption", {}, `page ${p}`),
      ),
    ),
    pages.length > 12
      ? h("div", { class: "muted small" }, `+${pages.length - 12} more pages`)
      : null,
  );
}

function judgeView(record) {
  if (!record) return h("div", { class: "muted small" }, "Not judged.");
  if (record.error && !record.verdict)
    return h("div", { class: "notice" }, "Judge error: " + record.error);
  const v = record.verdict;
  return h(
    "div",
    {},
    kv([
      ["overall", fmt("judge_overall", v.overall_score)],
      ["text", fmt("judge_text", v.text_fidelity)],
      ["reading order", fmt("judge_reading_order", v.reading_order)],
      ["tables", fmt("judge_table", v.table_fidelity)],
      ["visuals", fmt("judge_visual", v.visual_fidelity)],
      ["model", record.model + (record.synthetic ? " (not called: empty candidate)" : "")],
    ]),
    v.critical_errors && v.critical_errors.length
      ? h(
          "ul",
          { class: "errors small" },
          v.critical_errors.map((e) =>
            h("li", {}, badge(e.type.replaceAll("_", " "), "bad"), " ", e.evidence),
          ),
        )
      : h("div", { class: "small muted", style: "margin-top:6px" }, "No critical errors."),
    h("p", { class: "small" }, v.summary),
  );
}

function traceView(t) {
  if (!t) return null;
  return h(
    "div",
    { class: "small" },
    t.trace_pages.map((p) =>
      h(
        "div",
        { style: "margin-bottom:6px" },
        h("b", {}, `p${p.page_number} `),
        badge(p.route, p.route === "vlm" ? "info" : ""),
        p.vlm_failed ? [" ", badge("VLM failed → native", "bad")] : null,
        " ",
        h(
          "span",
          { class: "muted" },
          (p.complexity?.reasons || []).join(", ") || "no classifier reasons",
        ),
        p.duration_ms != null
          ? h("span", { class: "muted" }, ` · ${Math.round(p.duration_ms)} ms VLM`)
          : null,
      ),
    ),
    t.vlm_calls.length
      ? h(
          "table",
          {},
          h(
            "thead",
            {},
            h(
              "tr",
              {},
              ["page", "kind", "ok", "attempts", "tokens in/out", "finish", "ms"].map((x) =>
                h("th", {}, x),
              ),
            ),
          ),
          h(
            "tbody",
            {},
            t.vlm_calls.map((c) =>
              h(
                "tr",
                {},
                h("td", {}, c.page_number ?? "?"),
                h("td", {}, c.kind),
                h("td", {}, c.ok ? badge("ok", "good") : badge("empty", "bad")),
                h("td", { class: "num" }, c.attempts),
                h("td", { class: "num" }, `${c.input_tokens ?? "–"} / ${c.output_tokens ?? "–"}`),
                h("td", {}, c.finish_reason || (c.errors?.length ? "error" : "–")),
                h("td", { class: "num" }, Math.round(c.api_ms)),
              ),
            ),
          ),
        )
      : null,
  );
}

// ---------- views: runs list ----------

async function viewRuns(params) {
  const [runs, groups] = await Promise.all([get("/api/runs"), get("/api/groups")]);
  const allGroups = Object.keys(groups);
  const group = params.get("group") || "";
  const kind = params.get("kind") || "";
  let q = params.get("q") || "";

  const sidebar = h(
    "aside",
    { class: "card sidebar" },
    h("h3", {}, "Groups"),
    sideItem("All runs", runs.length, !group, () => setHashParams("#/runs", { kind, q })),
    sideItem("Ungrouped", runs.filter((r) => !r.groups.length).length, group === "__none", () =>
      setHashParams("#/runs", { group: "__none", kind, q }),
    ),
    allGroups.map((g) =>
      sideItem(
        g,
        groups[g],
        group === g,
        () => setHashParams("#/runs", { group: g, kind, q }),
        async () => {
          const name = prompt(`Rename group "${g}" to:`, g);
          if (!name || name === g) return;
          await attempt(
            () => api("POST", "/api/groups/rename", { old: g, new: name }),
            "Group renamed",
          );
          setHashParams("#/runs", { group: name, kind, q });
        },
      ),
    ),
  );

  const tableHost = h("div", { class: "card" });
  const bar = h("div");
  const search = h("input", {
    type: "search",
    class: "grow",
    placeholder: "Filter by name, id, model, commit…",
    value: q,
    "aria-label": "Filter runs",
  });
  search.addEventListener("input", () => {
    q = search.value;
    draw();
  });

  const visible = () =>
    runs.filter(
      (r) =>
        (!kind || r.kind === kind) &&
        (!group || (group === "__none" ? !r.groups.length : r.groups.includes(group))) &&
        (!q ||
          [r.name, r.id, r.model, r.commit, r.label, ...r.groups]
            .join(" ")
            .toLowerCase()
            .includes(q.toLowerCase())),
    );

  function headlineCell(r) {
    if (r.kind === "claims") {
      const m = r.headline.all || {};
      return h(
        "td",
        { class: "small" },
        `claim F1 ${fmt("x", m.claim_f1)} · entity F1 ${fmt("x", m.entity_f1)} · mutations ${fmt("x", m.mutation_accuracy)}`,
      );
    }
    const tiers = Object.entries(r.headline);
    if (!tiers.length) return h("td", { class: "muted small" }, "no summary yet");
    return h(
      "td",
      { class: "small" },
      tiers.map(([t, m]) =>
        h(
          "div",
          { class: "nowrap" },
          h("b", {}, t),
          ` text ${fmt("text_similarity", m.text_similarity)} · judge ${fmt("judge_overall", m.judge_overall)} · cells ${fmt("table_cell_accuracy", m.table_cell_accuracy)} · false-native ${fmt("false_native_rate", m.false_native_rate)}`,
        ),
      ),
    );
  }

  function draw() {
    const rows = visible();
    const sel = new Set(selection.ids());
    const allBox = h("input", {
      type: "checkbox",
      "aria-label": "Select all visible",
      checked: rows.length > 0 && rows.every((r) => sel.has(r.id)),
      onchange: (e) => {
        rows.forEach((r) => selection.toggle(r.id, e.target.checked));
        draw();
      },
    });
    tableHost.replaceChildren(
      rows.length
        ? h(
            "div",
            { class: "table-wrap" },
            h(
              "table",
              {},
              h(
                "thead",
                {},
                h(
                  "tr",
                  {},
                  h("th", {}, allBox),
                  ["Name", "Kind", "Created", "Tiers / cases", "Headline", "Groups", ""].map((x) =>
                    h("th", {}, x),
                  ),
                ),
              ),
              h(
                "tbody",
                {},
                rows.map((r) =>
                  h(
                    "tr",
                    {},
                    h(
                      "td",
                      {},
                      h("input", {
                        type: "checkbox",
                        "aria-label": `Select ${r.name}`,
                        checked: sel.has(r.id),
                        onchange: (e) => {
                          selection.toggle(r.id, e.target.checked);
                          draw();
                        },
                      }),
                    ),
                    h(
                      "td",
                      {},
                      h(
                        "div",
                        {},
                        h(
                          "a",
                          { href: r.kind === "claims" ? runLink(r.id) : runLink(r.id) },
                          r.name,
                        ),
                        " ",
                        h(
                          "button",
                          {
                            class: "ghost small",
                            title: "Rename",
                            "aria-label": `Rename ${r.name}`,
                            onclick: (e) => {
                              const cell = e.target.closest("td");
                              const el = editableName(r, () => draw());
                              cell.firstChild.replaceWith(h("div", {}, el));
                              el.click();
                            },
                          },
                          "✎",
                        ),
                      ),
                      h("div", { class: "muted small mono" }, r.id),
                    ),
                    h("td", {}, badge(r.kind, r.kind === "claims" ? "info" : "")),
                    h("td", { class: "nowrap small" }, fmtDate(r.created_at)),
                    h(
                      "td",
                      { class: "small" },
                      r.kind === "claims"
                        ? `${r.cases} episodes × ${r.runs ?? 1}`
                        : `${r.tiers.join(", ")} · ${r.cases} cases`,
                    ),
                    headlineCell(r),
                    h(
                      "td",
                      {},
                      groupEditor(
                        r,
                        (rec) => {
                          r.groups = rec.groups;
                        },
                        allGroups,
                      ),
                    ),
                    h(
                      "td",
                      { class: "badges" },
                      r.kind === "extractor" ? statusBadge(r.status) : null,
                      r.draft_gold ? [" ", badge("draft gold", "warn")] : null,
                      r.errors ? [" ", badge(`${r.errors} errors`, "bad")] : null,
                      r.comments ? [" ", badge(`💬 ${r.comments}`, "info")] : null,
                    ),
                  ),
                ),
              ),
            ),
          )
        : h(
            "div",
            { class: "empty" },
            runs.length
              ? "No runs match the filters."
              : [
                  "No runs yet. ",
                  h("a", { href: "#/new-run" }, "Start your first run"),
                  " to see scores here.",
                ],
          ),
    );
    drawBar();
  }

  function drawBar() {
    const ids = selection.ids();
    if (!ids.length) return bar.replaceChildren();
    const kinds = new Set(ids.map((id) => (id.startsWith("claims:") ? "claims" : "extractor")));
    bar.replaceChildren(
      h(
        "div",
        { class: "selection-bar" },
        h("b", {}, `${ids.length} selected`),
        h(
          "button",
          {
            class: "primary",
            disabled: kinds.size > 1,
            title: kinds.size > 1 ? "Select runs of one kind" : "",
            onclick: () => setHashParams("#/compare", { ids: ids.join(",") }),
          },
          "Compare side by side",
        ),
        h(
          "button",
          {
            onclick: async () => {
              const g = prompt("Add the selected runs to group:");
              if (!g) return;
              await attempt(
                () =>
                  Promise.all(
                    ids.map((id) => api("PATCH", `/api/runs/${enc(id)}`, { add_groups: [g] })),
                  ),
                "Grouped",
              );
              route();
            },
          },
          "Add to group…",
        ),
        h(
          "button",
          {
            onclick: async () => {
              const g = prompt(
                "Remove the selected runs from group:",
                group && group !== "__none" ? group : "",
              );
              if (!g) return;
              await attempt(
                () =>
                  Promise.all(
                    ids.map((id) => api("PATCH", `/api/runs/${enc(id)}`, { remove_groups: [g] })),
                  ),
                "Removed",
              );
              route();
            },
          },
          "Remove from group…",
        ),
        h("span", { style: "flex:1" }),
        h(
          "button",
          {
            class: "danger",
            onclick: async () => {
              if (
                !confirm(
                  `Permanently delete ${ids.length} run(s)? This removes their result files.`,
                )
              )
                return;
              await attempt(
                () => Promise.all(ids.map((id) => api("DELETE", `/api/runs/${enc(id)}`))),
                "Deleted",
              );
              selection.clear();
              route();
            },
          },
          "Delete…",
        ),
        h(
          "button",
          {
            class: "ghost",
            onclick: () => {
              selection.clear();
              draw();
            },
          },
          "Clear",
        ),
      ),
    );
  }

  draw();
  $app.replaceChildren(
    h(
      "div",
      { class: "layout" },
      sidebar,
      h(
        "section",
        {},
        runsHeader(runs),
        h(
          "div",
          { class: "toolbar" },
          search,
          seg(
            [
              ["", "All"],
              ["extractor", "Extractor"],
              ["claims", "Claims"],
            ],
            kind,
            (k) => setHashParams("#/runs", { group, kind: k, q }),
          ),
        ),
        tableHost,
        bar,
      ),
    ),
  );
}

function sideItem(label, count, active, onclick, onEdit) {
  return h(
    "div",
    {
      class: "side-item" + (active ? " active" : ""),
      role: "button",
      tabindex: "0",
      onclick,
      onkeydown: (e) => e.key === "Enter" && onclick(),
    },
    h("span", {}, label),
    h(
      "span",
      {},
      onEdit
        ? h(
            "span",
            {
              class: "edit",
              title: "Rename group",
              onclick: (e) => {
                e.stopPropagation();
                onEdit();
              },
            },
            "✎ ",
          )
        : null,
      h("span", { class: "count" }, count),
    ),
  );
}

// ---------- views: extractor run ----------

const UNIT_COLS = [
  "text_similarity",
  "numeric_recall",
  "table_cell_accuracy",
  "heading_f1",
  "link_recall",
  "judge_overall",
];

function unitIdForPage(scores, caseId, page) {
  const s = scores.find((x) => x.case === caseId);
  const u = s && s.units.find((u) => u.pages.includes(page));
  return u ? u.id : null;
}

async function viewRun(runId, params) {
  const data = await get(`/api/runs/${enc(runId)}`);
  if (data.record.kind === "claims") return viewClaimsRun(runId, data, params);
  const { record, run, summary, scores } = data;
  let comments = data.comments;
  const tiers = Object.keys(summary || {});
  const tab = params.get("tab") || "overview";
  const allGroups = Object.keys(await get("/api/groups"));

  const header = h(
    "div",
    {},
    h("div", { class: "crumbs" }, h("a", { href: "#/runs" }, "Runs"), " / ", record.id),
    h(
      "div",
      { class: "page-head" },
      h(
        "h1",
        {},
        editableName(record, () => {}),
      ),
      h(
        "div",
        { class: "toolbar", style: "margin:0" },
        h(
          "button",
          {
            onclick: () => {
              selection.toggle(runId, !selection.has(runId));
              route();
            },
          },
          selection.has(runId) ? "✓ In compare" : "Add to compare",
        ),
        runActions(runId, record.status, run, record.name),
        tiers.length > 1
          ? h(
              "button",
              { class: "primary", onclick: () => setHashParams("#/compare", { ids: runId }) },
              "Compare tiers",
            )
          : null,
      ),
    ),
    h(
      "div",
      { class: "meta-line" },
      statusBadge(record.status),
      record.draft_gold ? badge("includes draft gold", "warn") : null,
      run.errors.length ? badge(`${run.errors.length} errors`, "bad") : null,
      h("span", {}, "created ", h("b", {}, fmtDate(record.created_at))),
      h("span", {}, "image ", h("b", { class: "mono" }, run.image.tag)),
      h("span", {}, "extraction ", h("b", {}, record.model || "–")),
      h("span", {}, "judge ", h("b", {}, run.config.judge_model || "off")),
      h("span", {}, "tiers ", h("b", {}, run.config.tiers.join(", "))),
      h("span", {}, "cases ", h("b", {}, run.config.cases.length)),
      run.config.perf_repeats
        ? h("span", {}, "perf repeats ", h("b", {}, run.config.perf_repeats))
        : null,
    ),
    h("div", {}, groupEditor(record, null, allGroups)),
  );

  const tabs = [
    ["overview", "Overview"],
    ["cases", "Cases"],
    ["routing", "Routing"],
    ["operational", "Operational"],
    ["settings", "Settings"],
    ["comments", `Comments (${comments.length})`],
  ];
  const tabBar = h(
    "div",
    { class: "tabs", role: "tablist" },
    tabs.map(([k, label]) =>
      h(
        "button",
        {
          role: "tab",
          class: k === tab ? "on" : "",
          "aria-selected": String(k === tab),
          onclick: () => setHashParams(runLink(runId), { tab: k }),
        },
        label,
      ),
    ),
  );

  let body;
  if (!tiers.length)
    body = h(
      "div",
      { class: "empty card" },
      "This run has no summary yet (still running or interrupted). Resume with `extractor-eval run --resume " +
        runId +
        "`.",
    );
  else if (tab === "overview") body = runOverview(summary, tiers);
  else if (tab === "cases") body = runCases(runId, scores, tiers, comments);
  else if (tab === "routing") body = runRouting(runId, summary, scores, tiers);
  else if (tab === "operational") body = runOperational(summary, tiers);
  else if (tab === "settings")
    body = h(
      "div",
      { class: "two-col" },
      h(
        "div",
        { class: "card pad" },
        h("h3", {}, "Run configuration"),
        jsonBlock({
          image: run.image,
          config: run.config,
          evals_commit: run.evals_commit,
          errors: run.errors,
        }),
      ),
      h(
        "div",
        { class: "card pad" },
        h("h3", {}, "Parser fingerprint per tier"),
        jsonBlock(Object.fromEntries(tiers.map((t) => [t, summary[t].fingerprint]))),
        h("h3", {}, "Dataset"),
        jsonBlock(run.dataset),
      ),
    );
  else
    body = h(
      "div",
      { class: "card pad" },
      commentsPanel({
        runId,
        comments,
        target: null,
        tiers,
        linkFor: (t) =>
          t.case && t.unit ? runLink(runId, `/unit/${enc(t.case)}/${enc(t.unit)}`) : runLink(runId),
        onChange: (c) => (comments = c),
      }),
    );

  $app.replaceChildren(header, runBanner(runId, record.status), tabBar, body);
}

function metricRowsTable(columns, getValue, keys, opts = {}) {
  // columns: [{label}], getValue(colIndex, key)
  let lastGroup = null;
  const rows = [];
  for (const key of keys) {
    const s = spec(key);
    if (opts.grouped && s.group !== lastGroup) {
      lastGroup = s.group;
      rows.push(h("tr", { class: "group-row" }, h("td", { colspan: columns.length + 1 }, s.group)));
    }
    const base = getValue(0, key);
    rows.push(
      h(
        "tr",
        {},
        h(
          "td",
          { title: s.description || "" },
          s.label,
          s.better === "lower" ? h("span", { class: "muted small" }, " ↓") : null,
        ),
        columns.map((_, i) => metricCell(key, getValue(i, key), base, i === 0)),
      ),
    );
  }
  return h(
    "div",
    { class: "table-wrap" },
    h(
      "table",
      {},
      h(
        "thead",
        {},
        h(
          "tr",
          {},
          h("th", {}, "Metric"),
          columns.map((c) => h("th", { class: "num" }, c.label)),
        ),
      ),
      h("tbody", {}, rows),
    ),
  );
}

function runOverview(summary, tiers) {
  const headline = CATALOG_LIST.filter((m) => m.headline);
  const cards = h(
    "div",
    { class: "stat-grid" },
    headline.map((m) =>
      h(
        "div",
        { class: "card stat", title: m.description || "" },
        h("div", { class: "label" }, m.label),
        tiers.map((t) =>
          h(
            "div",
            { class: "sub" },
            h(
              "b",
              { style: "color:var(--text);font-size:16px" },
              fmt(m.key, summary[t].metrics[m.key]),
            ),
            " ",
            t,
          ),
        ),
      ),
    ),
  );
  const keys = CATALOG_LIST.map((m) => m.key).filter((k) =>
    tiers.some((t) => summary[t].metrics[k] != null),
  );
  const cols = [];
  for (const t of tiers) cols.push({ label: t, tier: t, scope: "all" });
  const hasVerified = tiers.some((t) => Object.keys(summary[t].metrics_verified || {}).length);
  if (hasVerified)
    for (const t of tiers) cols.push({ label: `${t} (verified gold)`, tier: t, scope: "verified" });
  const value = (i, key) => {
    const c = cols[i];
    return c.scope === "verified"
      ? (summary[c.tier].metrics_verified || {})[key]
      : summary[c.tier].metrics[key];
  };
  const units = tiers.map(
    (t) =>
      `${t}: ${summary[t].units.all} units (${summary[t].units.verified} verified gold), judged ${summary[t].judge.judged}${summary[t].judge.errors ? `, ${summary[t].judge.errors} judge errors` : ""}`,
  );
  const tagKeys = ["text_similarity", "numeric_recall", "table_cell_accuracy", "judge_overall"];
  const breakdown = (title, obj) =>
    h(
      "div",
      { class: "card pad" },
      h("h3", {}, title),
      h(
        "div",
        { class: "table-wrap" },
        h(
          "table",
          {},
          h(
            "thead",
            {},
            h(
              "tr",
              {},
              h("th", {}, "name"),
              h("th", { class: "num" }, "units"),
              tiers.flatMap((t) =>
                tagKeys.map((k) => h("th", { class: "num" }, `${t} ${spec(k).label}`)),
              ),
            ),
          ),
          h(
            "tbody",
            {},
            Object.keys(obj(tiers[0])).map((name) =>
              h(
                "tr",
                {},
                h("td", {}, name),
                h("td", { class: "num" }, obj(tiers[0])[name].units),
                tiers.flatMap((t) =>
                  tagKeys.map((k) => h("td", { class: "num" }, fmt(k, (obj(t)[name] || {})[k]))),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  return h(
    "div",
    {},
    cards,
    h(
      "p",
      { class: "muted small" },
      units.join(" · "),
      hasVerified ? "" : " · No verified gold yet: quality numbers include draft gold.",
    ),
    h(
      "div",
      { class: "card pad" },
      h("h3", {}, "All metrics (colored vs the first column)"),
      metricRowsTable(cols, value, keys, { grouped: true }),
    ),
    h(
      "div",
      { class: "two-col", style: "margin-top:14px" },
      breakdown("By suite", (t) => summary[t].by_suite),
      breakdown("By tag", (t) => summary[t].by_tag),
    ),
  );
}

function runCases(runId, scores, tiers, comments) {
  const filter = h("input", {
    type: "search",
    class: "grow",
    placeholder: "Filter cases, units, tags…",
    "aria-label": "Filter cases",
  });
  const host = h("div", { class: "card" });
  const byCase = {};
  for (const t of tiers)
    for (const s of scores[t]) (byCase[s.case] ||= { s, tiers: {} }).tiers[t] = s;
  const draw = () => {
    const q = filter.value.toLowerCase();
    const rows = [];
    for (const [caseId, entry] of Object.entries(byCase)) {
      for (const unit of entry.s.units) {
        const text = `${caseId} ${unit.id} ${unit.tags.join(" ")} ${entry.s.suite}`.toLowerCase();
        if (q && !text.includes(q)) continue;
        const n = comments.filter(
          (c) => c.target && c.target.case === caseId && c.target.unit === unit.id,
        ).length;
        rows.push(
          h(
            "tr",
            {
              class: "clickable",
              onclick: () =>
                (location.hash = runLink(runId, `/unit/${enc(caseId)}/${enc(unit.id)}`)),
            },
            h(
              "td",
              {},
              h("a", { href: runLink(runId, `/unit/${enc(caseId)}/${enc(unit.id)}`) }, caseId),
              " ",
              entry.s.gold_status !== "verified" ? badge("draft", "warn") : null,
              h("div", { class: "muted small" }, entry.s.suite),
            ),
            h(
              "td",
              { class: "mono" },
              unit.id,
              h(
                "div",
                { class: "muted small" },
                `p${unit.pages[0]}${unit.pages.length > 1 ? "–" + unit.pages[unit.pages.length - 1] : ""}`,
              ),
            ),
            h("td", { class: "small" }, unit.tags.join(", ")),
            tiers.flatMap((t, ti) => {
              const u = (entry.tiers[t]?.units || []).find((x) => x.id === unit.id);
              const base = (entry.tiers[tiers[0]]?.units || []).find((x) => x.id === unit.id);
              return UNIT_COLS.map((k) => metricCell(k, u?.metrics[k], base?.metrics[k], ti === 0));
            }),
            h(
              "td",
              {},
              tiers.map((t) => {
                const u = (entry.tiers[t]?.units || []).find((x) => x.id === unit.id);
                return u && u.issues.length
                  ? badge(`${t}: ${u.issues.length} md issue(s)`, "warn")
                  : null;
              }),
              n ? badge(`💬 ${n}`, "info") : null,
            ),
          ),
        );
      }
    }
    host.replaceChildren(
      h(
        "div",
        { class: "table-wrap" },
        h(
          "table",
          {},
          h(
            "thead",
            {},
            h(
              "tr",
              {},
              h("th", {}, "Case"),
              h("th", {}, "Unit"),
              h("th", {}, "Tags"),
              tiers.flatMap((t) =>
                UNIT_COLS.map((k) =>
                  h("th", { class: "num", title: spec(k).description }, `${t} · ${spec(k).label}`),
                ),
              ),
              h("th", {}, ""),
            ),
          ),
          h(
            "tbody",
            {},
            rows.length
              ? rows
              : h("tr", {}, h("td", { colspan: 99, class: "empty" }, "No units match.")),
          ),
        ),
      ),
    );
  };
  filter.addEventListener("input", draw);
  draw();
  return h(
    "div",
    {},
    h(
      "div",
      { class: "toolbar" },
      filter,
      h(
        "span",
        { class: "muted small" },
        "Colored vs the first tier. Click a row for the side-by-side view.",
      ),
    ),
    host,
  );
}

function runRouting(runId, summary, scores, tiers) {
  const keys = [
    "routing_precision",
    "routing_recall",
    "false_native_rate",
    "false_vlm_rate",
    "reason_agreement",
  ];
  return h(
    "div",
    {},
    h(
      "div",
      { class: "card pad" },
      metricRowsTable(
        tiers.map((t) => ({ label: t })),
        (i, k) => summary[tiers[i]].metrics[k],
        keys,
      ),
      h(
        "p",
        { class: "muted small" },
        tiers
          .map(
            (t) =>
              `${t}: ${summary[t].routing.scored_pages}/${summary[t].routing.labelled_pages} labelled pages scored`,
          )
          .join(" · "),
      ),
    ),
    tiers.map((t) => {
      const r = summary[t].routing;
      return h(
        "div",
        { class: "card pad", style: "margin-top:14px" },
        h("h3", {}, `${t}: routing failures (${r.failures.length})`),
        r.failures.length
          ? h(
              "div",
              { class: "table-wrap" },
              h(
                "table",
                {},
                h(
                  "thead",
                  {},
                  h(
                    "tr",
                    {},
                    [
                      "Case",
                      "Page",
                      "Outcome",
                      "Expected",
                      "Actual",
                      "Classifier reasons",
                      "Expected reasons",
                      "Tags",
                    ].map((x) => h("th", {}, x)),
                  ),
                ),
                h(
                  "tbody",
                  {},
                  r.failures.map((f) => {
                    const unit = unitIdForPage(scores[t], f.case, f.page);
                    return h(
                      "tr",
                      {},
                      h(
                        "td",
                        {},
                        unit
                          ? h(
                              "a",
                              { href: runLink(runId, `/unit/${enc(f.case)}/${enc(unit)}`) },
                              f.case,
                            )
                          : f.case,
                      ),
                      h("td", { class: "num" }, f.page),
                      h(
                        "td",
                        {},
                        badge(
                          f.outcome.replace("_", "-"),
                          f.outcome === "false_native" ? "bad" : "warn",
                        ),
                      ),
                      h("td", {}, f.expected_route),
                      h(
                        "td",
                        {},
                        f.actual_route,
                        f.vlm_failed ? [" ", badge("VLM failed", "bad")] : null,
                      ),
                      h("td", { class: "small" }, f.classifier_reasons.join(", ") || "–"),
                      h("td", { class: "small" }, f.expected_classifier_reasons.join(", ") || "–"),
                      h("td", { class: "small" }, f.tags.join(", ")),
                    );
                  }),
                ),
              ),
            )
          : h("div", { class: "muted small" }, "No routing failures."),
        h(
          "div",
          { class: "two-col" },
          ["by_reason", "by_tag"].map((k) =>
            h(
              "div",
              {},
              h("h3", {}, k === "by_reason" ? "By classifier reason" : "By tag"),
              h(
                "table",
                {},
                h(
                  "thead",
                  {},
                  h(
                    "tr",
                    {},
                    ["name", "correct", "false native", "false VLM"].map((x) =>
                      h("th", { class: x === "name" ? "" : "num" }, x),
                    ),
                  ),
                ),
                h(
                  "tbody",
                  {},
                  Object.entries(r[k]).map(([name, c]) =>
                    h(
                      "tr",
                      {},
                      h("td", {}, name),
                      h("td", { class: "num" }, c.correct || 0),
                      h(
                        "td",
                        { class: "num" + (c.false_native ? " worse" : "") },
                        c.false_native || 0,
                      ),
                      h("td", { class: "num" }, c.false_vlm || 0),
                    ),
                  ),
                ),
              ),
            ),
          ),
        ),
      );
    }),
  );
}

function runOperational(summary, tiers) {
  const cols = [
    "status",
    "duration_seconds",
    "seconds_per_page",
    "total_pages",
    "vlm_page_count",
    "vlm_page_ratio",
    "vlm_calls",
    "vlm_failures",
    "retries",
    "input_tokens",
    "output_tokens",
    "cost_usd",
    "output_chars",
    "wall_seconds",
  ];
  const unitOf = {
    total_pages: "count",
    vlm_page_count: "count",
    vlm_calls: "count",
    wall_seconds: "seconds",
  };
  return h(
    "div",
    {},
    tiers.map((t) => {
      const ops = summary[t].operational_by_case;
      const perf = summary[t].perf || {};
      return h(
        "div",
        { class: "card pad", style: "margin-bottom:14px" },
        h("h3", {}, t),
        h(
          "div",
          { class: "table-wrap" },
          h(
            "table",
            {},
            h(
              "thead",
              {},
              h(
                "tr",
                {},
                h("th", {}, "Case"),
                cols.map((c) =>
                  h(
                    "th",
                    { class: "num" },
                    spec(c).label !== c ? spec(c).label : c.replaceAll("_", " "),
                  ),
                ),
                h("th", { class: "num" }, "perf median / min / max"),
              ),
            ),
            h(
              "tbody",
              {},
              Object.entries(ops).map(([caseId, o]) =>
                h(
                  "tr",
                  {},
                  h(
                    "td",
                    {},
                    caseId,
                    o.vlm_available === false ? [" ", badge("no VLM key", "bad")] : null,
                    o.error
                      ? h("div", { class: "small", style: "color:var(--bad)" }, o.error)
                      : null,
                  ),
                  cols.map((c) =>
                    c === "status"
                      ? h("td", {}, statusBadge(o.status))
                      : h("td", { class: "num" }, fmt(c, o[c], unitOf[c])),
                  ),
                  h(
                    "td",
                    { class: "num" },
                    perf[caseId]
                      ? `${fmt("x", perf[caseId].median, "seconds")} / ${fmt("x", perf[caseId].min, "seconds")} / ${fmt("x", perf[caseId].max, "seconds")} (n=${perf[caseId].n})`
                      : "–",
                  ),
                ),
              ),
            ),
          ),
        ),
      );
    }),
  );
}

// ---------- views: unit side-by-side (one run, all tiers) ----------

async function viewUnit(runId, caseId, unitId) {
  const [u, run] = await Promise.all([
    get(`/api/runs/${enc(runId)}/units/${enc(caseId)}/${enc(unitId)}`),
    get(`/api/runs/${enc(runId)}`),
  ]);
  const tiers = Object.keys(u.tiers);
  // prev / next unit across the run
  const order = [];
  for (const s of run.scores[run.run.config.tiers[0]] || [])
    for (const x of s.units) order.push([s.case, x.id]);
  const idx = order.findIndex(([c, x]) => c === caseId && x === unitId);
  const nav = (d) =>
    order[idx + d]
      ? runLink(runId, `/unit/${enc(order[idx + d][0])}/${enc(order[idx + d][1])}`)
      : null;

  const columns = [
    { key: "gold", title: "Gold", text: u.gold },
    ...tiers.map((t) => ({ key: t, title: t, text: u.tiers[t].candidate, tier: u.tiers[t] })),
  ];
  await renderSideBySide({
    crumbs: [
      h("a", { href: "#/runs" }, "Runs"),
      " / ",
      h("a", { href: runLink(runId, "?tab=cases") }, run.record.name),
      " / ",
      caseId,
      " / ",
      unitId,
    ],
    title: `${caseId} · ${unitId}`,
    caseId,
    pages: u.pages,
    notes: [
      u.gold_status !== "verified"
        ? "Gold for this case is a DRAFT (not human-verified yet)."
        : null,
      u.gold_note,
      u.pdf_changed ? "The case PDF changed since this run." : null,
    ],
    columns,
    navLinks: [nav(-1), nav(1)],
    commentHosts: [{ runId, name: run.record.name, comments: run.comments, tiers }],
    target: { case: caseId, unit: unitId },
  });
}

/** Shared by the unit view and the compare-unit view. columns[0] is gold. */
async function renderSideBySide({
  crumbs,
  title,
  caseId,
  pages,
  notes,
  columns,
  navLinks,
  commentHosts,
  target,
}) {
  let mode = local.get("paneMode", "raw");
  let diffBase = "gold";
  const hidden = new Set();
  const grid = h("div", { class: "sbs" });
  const controls = h("div", { class: "toolbar" });

  const drawControls = () =>
    controls.replaceChildren(
      seg(
        [
          ["raw", "Raw"],
          ["rendered", "Rendered"],
          ["diff", "Diff"],
        ],
        mode,
        (m) => {
          mode = m;
          local.set("paneMode", m);
          drawControls();
          drawPanes();
        },
      ),
      mode === "diff" && columns.length > 2
        ? h(
            "label",
            { class: "small muted" },
            "diff against ",
            h(
              "select",
              {
                onchange: (e) => {
                  diffBase = e.target.value;
                  drawPanes();
                },
              },
              columns.map((c) =>
                h("option", { value: c.key, selected: c.key === diffBase }, c.title),
              ),
            ),
          )
        : null,
      h("span", { class: "small muted" }, "show:"),
      columns.map((c) =>
        h(
          "label",
          { class: "small" },
          h("input", {
            type: "checkbox",
            checked: !hidden.has(c.key),
            onchange: (e) => {
              e.target.checked ? hidden.delete(c.key) : hidden.add(c.key);
              drawPanes();
            },
          }),
          " ",
          c.title,
        ),
      ),
      h("span", { style: "flex:1" }),
      navLinks && navLinks[0] ? h("a", { class: "btn", href: navLinks[0] }, "← prev") : null,
      navLinks && navLinks[1] ? h("a", { class: "btn", href: navLinks[1] }, "next →") : null,
    );

  const drawPanes = () => {
    const shown = columns.filter((c) => !hidden.has(c.key));
    grid.style.setProperty("--cols", String(Math.max(1, shown.length)));
    const base = columns.find((c) => c.key === diffBase) || columns[0];
    grid.replaceChildren(
      ...shown.map((c) => {
        const body = h("div", { class: "pane-body" });
        const t = c.tier;
        const routes = t
          ? t.trace_pages.map((p) =>
              badge(
                `p${p.page_number} ${p.route}${p.vlm_failed ? " (VLM failed)" : ""}`,
                p.vlm_failed ? "bad" : p.route === "vlm" ? "info" : "",
              ),
            )
          : null;
        const head = h(
          "div",
          { class: "pane-head" },
          h("span", { class: "title" }, c.title),
          h(
            "span",
            { class: "chips" },
            routes,
            t ? badge(`text ${fmt("text_similarity", t.unit.metrics.text_similarity)}`) : null,
            t && t.unit.metrics.judge_overall != null
              ? badge(`judge ${fmt("judge_overall", t.unit.metrics.judge_overall)}`, "info")
              : null,
          ),
        );
        fillPane(
          body,
          c.text,
          mode,
          mode === "diff" && c.key !== base.key ? base.text : null,
        ).catch((e) => body.replaceChildren(h("div", { class: "notice" }, e.message)));
        return h("div", { class: "card pane" }, head, body);
      }),
    );
  };

  const cand = columns.filter((c) => c.tier);
  const unitKeys = CATALOG_LIST.map((m) => m.key).filter((k) =>
    cand.some((c) => c.tier.unit.metrics[k] != null),
  );
  const metricsCard = h(
    "div",
    { class: "card pad" },
    h("h3", {}, "Unit metrics (colored vs the first column)"),
    metricRowsTable(
      cand.map((c) => ({ label: c.title })),
      (i, k) => cand[i].tier.unit.metrics[k],
      unitKeys,
      { grouped: true },
    ),
  );
  const detailCards = cand.map((c) => {
    const d = c.tier.unit.details || {};
    return h(
      "div",
      { class: "card pad" },
      h("h3", {}, `${c.title}: judge`),
      judgeView(c.tier.judge),
      h("h3", {}, "Routing & VLM trace"),
      traceView(c.tier),
      d.markdown_issues && d.markdown_issues.length
        ? [
            h("h3", {}, "Markdown issues"),
            h(
              "div",
              {},
              d.markdown_issues.map((i) => badge(i.replaceAll("_", " "), "warn")),
            ),
          ]
        : null,
      d.missing_phrases && d.missing_phrases.length
        ? [
            h("h3", {}, "Missing required phrases"),
            h(
              "ul",
              { class: "small" },
              d.missing_phrases.map((p) => h("li", {}, p)),
            ),
          ]
        : null,
      d.missing_numbers && d.missing_numbers.length
        ? [
            h("h3", {}, "Missing numbers"),
            h("div", { class: "mono small" }, d.missing_numbers.join("  ")),
          ]
        : null,
      d.links && (d.links.missing.length || d.links.unexpected.length)
        ? [
            h("h3", {}, "Links"),
            h(
              "div",
              { class: "small" },
              d.links.missing.map((l) =>
                h("div", {}, badge("missing", "bad"), ` [${l.anchor}](${l.url})`),
              ),
              d.links.unexpected.map((l) =>
                h("div", {}, badge("unexpected", "warn"), ` [${l.anchor}](${l.url})`),
              ),
            ),
          ]
        : null,
    );
  });

  drawControls();
  drawPanes();
  $app.replaceChildren(
    h("div", { class: "crumbs" }, crumbs),
    h("div", { class: "page-head" }, h("h1", {}, title)),
    notes.filter(Boolean).map((n) => h("div", { class: "notice" }, n)),
    h("div", { class: "card pad", style: "margin:10px 0" }, pagesStrip(caseId, pages)),
    controls,
    grid,
    h("div", { style: "margin-top:14px" }, metricsCard),
    h(
      "div",
      { class: "sbs", style: `margin-top:14px;--cols:${Math.max(1, detailCards.length)}` },
      detailCards,
    ),
    h(
      "div",
      { class: "card pad", style: "margin-top:14px" },
      h("h3", {}, "Comments on this unit"),
      commentHosts.map((host) =>
        h(
          "div",
          { style: "margin-bottom:12px" },
          commentHosts.length > 1
            ? h(
                "div",
                { class: "small muted", style: "margin-bottom:4px" },
                "Run: ",
                h("a", { href: runLink(host.runId) }, host.name),
              )
            : null,
          commentsPanel({
            runId: host.runId,
            comments: host.comments,
            target,
            tiers: host.tiers,
            filter: (c) =>
              c.target && c.target.case === target.case && c.target.unit === target.unit,
          }),
        ),
      ),
    ),
  );
}

// ---------- views: compare ----------

async function viewCompare(params) {
  const ids = (params.get("ids") || selection.ids().join(",")).split(",").filter(Boolean);
  if (!ids.length) {
    $app.replaceChildren(
      h(
        "div",
        { class: "card empty" },
        "Select runs on the ",
        h("a", { href: "#/runs" }, "Runs"),
        " page (checkboxes), then Compare. One run alone compares its tiers.",
      ),
    );
    return;
  }
  const scope = params.get("scope") || "all";
  const tiersParam = params.get("tiers") || "";
  const data = await get(
    `/api/compare?ids=${enc(ids.join(","))}&scope=${scope}&tiers=${enc(tiersParam)}`,
  );
  const isClaims = data.kind === "claims";
  let unitMetric = local.get("compareUnitMetric", data.unit_metrics[0]);
  if (!data.unit_metrics.includes(unitMetric)) unitMetric = data.unit_metrics[0];

  const head = h(
    "div",
    {},
    h("div", { class: "crumbs" }, h("a", { href: "#/runs" }, "Runs"), " / compare"),
    h(
      "div",
      { class: "page-head" },
      h("h1", {}, `Comparing ${data.columns.length} column${data.columns.length > 1 ? "s" : ""}`),
    ),
    h(
      "div",
      { class: "chips", style: "margin:6px 0 10px" },
      data.columns.map((c, i) =>
        h(
          "span",
          { class: "chip" },
          i === 0 ? "base: " : "",
          h("a", { href: runLink(c.run) }, c.label),
        ),
      ),
    ),
    isClaims
      ? null
      : h(
          "div",
          { class: "toolbar" },
          seg(
            [
              ["all", "All units"],
              ["verified", "Verified gold only"],
            ],
            scope,
            (s) => setHashParams("#/compare", { ids: ids.join(","), scope: s, tiers: tiersParam }),
          ),
          h("input", {
            type: "text",
            placeholder: "tiers, e.g. max",
            value: tiersParam,
            style: "width:150px",
            "aria-label": "Tier filter",
            onchange: (e) =>
              setHashParams("#/compare", { ids: ids.join(","), scope, tiers: e.target.value }),
          }),
          h(
            "span",
            { class: "muted small" },
            "Green/red = better/worse than the base column beyond tolerance.",
          ),
        ),
  );

  const rowsByKey = Object.fromEntries(data.rows.map((r) => [r.key, r]));
  const metricTable = isClaims
    ? h(
        "div",
        { class: "table-wrap" },
        h(
          "table",
          {},
          h(
            "thead",
            {},
            h(
              "tr",
              {},
              h("th", {}, "Metric"),
              data.columns.map((c) => h("th", { class: "num" }, c.label)),
            ),
          ),
          h(
            "tbody",
            {},
            data.rows.map((r) =>
              h(
                "tr",
                {},
                h("td", { title: r.description }, r.label),
                r.values.map((v, i) => {
                  const cell = metricCell(
                    r.key,
                    v,
                    r.values[0],
                    i === 0,
                    r,
                    r.unit === "ms" ? "ms" : "ratio",
                  );
                  if (r.stds && r.stds[i] != null)
                    cell.append(
                      h(
                        "span",
                        { class: "muted small" },
                        ` ±${r.unit === "ms" ? Math.round(r.stds[i]) : r.stds[i].toFixed(3)}`,
                      ),
                    );
                  return cell;
                }),
              ),
            ),
          ),
        ),
      )
    : metricRowsTable(
        data.columns,
        (i, k) => rowsByKey[k].values[i],
        data.rows.map((r) => r.key),
        { grouped: true },
      );

  const consistency = h(
    "div",
    { class: "card pad", style: "margin-top:14px" },
    h("h3", {}, `Settings that differ (${data.consistency.length})`),
    data.consistency.length
      ? h(
          "details",
          { open: data.consistency.length <= 12 },
          h(
            "summary",
            { class: "small muted" },
            "A score change should be explained by one of these.",
          ),
          h(
            "div",
            { class: "table-wrap" },
            h(
              "table",
              {},
              h(
                "thead",
                {},
                h(
                  "tr",
                  {},
                  h("th", {}, "Setting"),
                  data.columns.map((c) => h("th", {}, c.label)),
                ),
              ),
              h(
                "tbody",
                {},
                data.consistency.map((d) =>
                  h(
                    "tr",
                    {},
                    h("td", { class: "mono small" }, d.key),
                    d.values.map((v) =>
                      h(
                        "td",
                        { class: "mono small" },
                        v == null ? "–" : typeof v === "object" ? JSON.stringify(v) : String(v),
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ),
        )
      : h(
          "div",
          { class: "muted small" },
          "All recorded settings are identical: differences come from the parser's own variance.",
        ),
  );

  const unitsHost = h("div");
  const drawUnits = () => {
    const filter = local.get("compareFilter", "");
    unitsHost.replaceChildren(
      h(
        "div",
        { class: "toolbar" },
        seg(
          data.unit_metrics.map((m) => [m, isClaims ? m : spec(m).label]),
          unitMetric,
          (m) => {
            unitMetric = m;
            local.set("compareUnitMetric", m);
            drawUnits();
          },
        ),
        h("input", {
          type: "search",
          class: "grow",
          placeholder: "Filter units…",
          value: filter,
          "aria-label": "Filter units",
          oninput: (e) => {
            local.set("compareFilter", e.target.value);
            drawUnits();
          },
        }),
      ),
      h(
        "div",
        { class: "table-wrap" },
        h(
          "table",
          {},
          h(
            "thead",
            {},
            h(
              "tr",
              {},
              h("th", {}, isClaims ? "Episode" : "Case / unit"),
              data.columns.map((c) => h("th", { class: "num" }, c.label)),
            ),
          ),
          h(
            "tbody",
            {},
            data.units
              .filter((u) => !filter || u.key.toLowerCase().includes(filter.toLowerCase()))
              .map((u) => {
                const vals = u.values[unitMetric] || [];
                const href = `#/compare/unit?ids=${enc(ids.join(","))}&case=${enc(u.case)}&unit=${enc(u.unit)}&tiers=${enc(tiersParam)}`;
                return h(
                  "tr",
                  { class: "clickable", onclick: () => (location.hash = href) },
                  h("td", {}, h("a", { href }, u.key)),
                  vals.map((v, i) =>
                    metricCell(
                      unitMetric,
                      v,
                      vals[0],
                      i === 0,
                      isClaims ? { better: "higher", unit: "ratio" } : null,
                    ),
                  ),
                );
              }),
          ),
        ),
      ),
    );
  };
  drawUnits();

  // Comments across the compared runs
  const runIds = [...new Set(data.columns.map((c) => c.run))];
  const runDetails = await Promise.all(runIds.map((id) => get(`/api/runs/${enc(id)}`)));
  const commentsCard = h(
    "div",
    { class: "card pad", style: "margin-top:14px" },
    h("h3", {}, "Comments"),
    runDetails.map((d) =>
      h(
        "div",
        { style: "margin-bottom:12px" },
        h(
          "div",
          { class: "small muted", style: "margin-bottom:4px" },
          "Run: ",
          h("a", { href: runLink(d.record.id) }, d.record.name),
        ),
        commentsPanel({
          runId: d.record.id,
          comments: d.comments,
          target: null,
          tiers: d.record.tiers,
          linkFor: (t) =>
            t.case && t.unit
              ? runLink(d.record.id, `/unit/${enc(t.case)}/${enc(t.unit)}`)
              : runLink(d.record.id),
        }),
      ),
    ),
  );

  $app.replaceChildren(
    head,
    h("div", { class: "card pad" }, metricTable),
    consistency,
    h(
      "div",
      { class: "card pad", style: "margin-top:14px" },
      h("h3", {}, isClaims ? "Per episode" : "Per unit"),
      unitsHost,
    ),
    commentsCard,
  );
}

async function viewCompareUnit(params) {
  const ids = (params.get("ids") || "").split(",").filter(Boolean);
  const caseId = params.get("case");
  const unitId = params.get("unit") || "";
  const tierFilter = (params.get("tiers") || "").split(",").filter(Boolean);
  if (ids[0] && ids[0].startsWith("claims:")) return viewClaimsEpisodeCompare(ids, caseId);
  const [units, runs] = await Promise.all([
    Promise.all(
      ids.map((id) =>
        get(`/api/runs/${enc(id)}/units/${enc(caseId)}/${enc(unitId)}`).catch(() => null),
      ),
    ),
    Promise.all(ids.map((id) => get(`/api/runs/${enc(id)}`))),
  ]);
  const first = units.find(Boolean);
  if (!first) {
    $app.replaceChildren(
      h("div", { class: "card empty" }, "This unit is not in the selected runs."),
    );
    return;
  }
  const columns = [{ key: "gold", title: "Gold", text: first.gold }];
  units.forEach((u, i) => {
    if (!u) return;
    for (const t of Object.keys(u.tiers)) {
      if (tierFilter.length && !tierFilter.includes(t)) continue;
      columns.push({
        key: `${ids[i]}|${t}`,
        title: `${runs[i].record.name} · ${t}`,
        text: u.tiers[t].candidate,
        tier: u.tiers[t],
      });
    }
  });
  await renderSideBySide({
    crumbs: [
      h("a", { href: "#/runs" }, "Runs"),
      " / ",
      h("a", { href: `#/compare?ids=${enc(ids.join(","))}` }, "compare"),
      " / ",
      caseId,
      " / ",
      unitId,
    ],
    title: `${caseId} · ${unitId}`,
    caseId,
    pages: first.pages,
    notes: [
      first.gold_status !== "verified"
        ? "Gold for this case is a DRAFT (not human-verified yet)."
        : null,
      first.gold_note,
    ],
    columns,
    navLinks: null,
    commentHosts: runs.map((r) => ({
      runId: r.record.id,
      name: r.record.name,
      comments: r.comments,
      tiers: r.record.tiers,
    })),
    target: { case: caseId, unit: unitId },
  });
}

// ---------- views: claims ----------

function claimsEpisodeStats(data) {
  const stats = {};
  for (const r of data.results || []) {
    if (!r.metrics) continue;
    const s = (stats[r.episodeId] ||= { f1: [], entity: [], mut: [], latency: [] });
    s.f1.push(r.metrics.claims.f1);
    s.entity.push(r.metrics.entities.f1);
    if (r.metrics.mutationAccuracy != null) s.mut.push(r.metrics.mutationAccuracy);
    s.latency.push(r.latencyMs);
  }
  const mean = (a) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : null);
  return Object.fromEntries(
    Object.entries(stats).map(([k, s]) => [
      k,
      { f1: mean(s.f1), entity: mean(s.entity), mut: mean(s.mut), latency: mean(s.latency) },
    ]),
  );
}

async function viewClaimsRun(runId, d, params) {
  const data = d.claims;
  let comments = d.comments;
  const episode = params.get("episode");
  const allGroups = Object.keys(await get("/api/groups"));
  const catalog = Object.fromEntries((data.catalog || []).map((c) => [c.key, c]));
  const header = h(
    "div",
    {},
    h(
      "div",
      { class: "crumbs" },
      h("a", { href: "#/runs?kind=claims" }, "Runs"),
      " / ",
      d.record.id,
    ),
    h(
      "div",
      { class: "page-head" },
      h(
        "h1",
        {},
        editableName(d.record, () => {}),
      ),
      h(
        "button",
        {
          onclick: () => {
            selection.toggle(runId, !selection.has(runId));
            route();
          },
        },
        selection.has(runId) ? "✓ In compare" : "Add to compare",
      ),
    ),
    h(
      "div",
      { class: "meta-line" },
      badge("claims", "info"),
      h("span", {}, "created ", h("b", {}, fmtDate(d.record.created_at))),
      h("span", {}, "model ", h("b", {}, data.model)),
      h("span", {}, "embeddings ", h("b", {}, data.embeddingModel)),
      h("span", {}, "runs ", h("b", {}, data.runs)),
      h("span", {}, "commit ", h("b", { class: "mono" }, data.gitSha)),
    ),
    groupEditor(d.record, null, allGroups),
  );
  if (episode) {
    $app.replaceChildren(header, claimsEpisode(runId, data, episode, comments));
    return;
  }
  const summaryTable = h(
    "table",
    {},
    h(
      "thead",
      {},
      h(
        "tr",
        {},
        ["Metric", "Mean", "Std"].map((x) => h("th", { class: x === "Metric" ? "" : "num" }, x)),
      ),
    ),
    h(
      "tbody",
      {},
      Object.entries(data.summary || {}).map(([k, v]) =>
        h(
          "tr",
          {},
          h("td", { title: catalog[k]?.description || "" }, catalog[k]?.label || k),
          h("td", { class: "num" }, fmt(k, v.mean, catalog[k]?.unit === "ms" ? "ms" : "ratio")),
          h(
            "td",
            { class: "num muted" },
            v.std == null ? "–" : catalog[k]?.unit === "ms" ? Math.round(v.std) : v.std.toFixed(3),
          ),
        ),
      ),
    ),
  );
  const stats = claimsEpisodeStats(data);
  const episodes = h(
    "table",
    {},
    h(
      "thead",
      {},
      h(
        "tr",
        {},
        ["Episode", "Tags", "Claim F1", "Entity F1", "Mutation acc.", "Latency", ""].map((x, i) =>
          h("th", { class: i > 1 && i < 6 ? "num" : "" }, x),
        ),
      ),
    ),
    h(
      "tbody",
      {},
      Object.entries(data.episodes || {}).map(([id, e]) => {
        const s = stats[id] || {};
        const n = comments.filter((c) => c.target && c.target.case === id).length;
        const href = runLink(runId, `?episode=${enc(id)}`);
        return h(
          "tr",
          { class: "clickable", onclick: () => (location.hash = href) },
          h("td", {}, h("a", { href }, id)),
          h("td", { class: "small" }, (e.tags || []).join(", ")),
          h("td", { class: "num" }, fmt("x", s.f1)),
          h("td", { class: "num" }, fmt("x", s.entity)),
          h("td", { class: "num" }, fmt("x", s.mut)),
          h("td", { class: "num" }, fmt("x", s.latency, "ms")),
          h("td", {}, n ? badge(`💬 ${n}`, "info") : null),
        );
      }),
    ),
  );
  $app.replaceChildren(
    header,
    h(
      "div",
      { class: "two-col", style: "margin-top:14px" },
      h("div", { class: "card pad" }, h("h3", {}, "Summary"), summaryTable),
      h(
        "div",
        { class: "card pad" },
        h("h3", {}, "Comments"),
        commentsPanel({
          runId,
          comments,
          target: null,
          linkFor: (t) => runLink(runId, `?episode=${enc(t.case)}`),
          onChange: (c) => (comments = c),
        }),
      ),
    ),
    h(
      "div",
      { class: "card pad", style: "margin-top:14px" },
      h("h3", {}, "Episodes"),
      h("div", { class: "table-wrap" }, episodes),
    ),
  );
}

function claimList(items, render) {
  return items && items.length
    ? h(
        "ul",
        { class: "small", style: "padding-left:18px;margin:4px 0" },
        items.map((x) => h("li", {}, render(x))),
      )
    : h("div", { class: "muted small" }, "(none)");
}

function claimsPrediction(r) {
  if (!r) return h("div", { class: "muted small" }, "No result.");
  const res = r.result || {};
  return h(
    "div",
    {},
    h(
      "div",
      { class: "small muted" },
      `run ${r.run} · ${Math.round(r.latencyMs)} ms · claim F1 ${fmt("x", r.metrics?.claims?.f1)}`,
    ),
    h("h3", {}, "Claims"),
    claimList(res.claims, (c) => [badge(c.kind || "?"), " ", c.content]),
    h("h3", {}, "Entities"),
    claimList(res.entityRelationships, (e) => `${e.subject} —${e.predicate}→ ${e.object}`),
    h("h3", {}, "Mutations"),
    claimList(res.mutations, (m) => [
      badge(m.action, m.action === "noop" ? "" : "warn"),
      ` ${m.targetClaimId}${m.replacementContent ? " → " + m.replacementContent : ""}`,
    ]),
  );
}

function claimsExpected(e) {
  return h(
    "div",
    {},
    h("div", { class: "small" }, e.rawText),
    h("h3", {}, "Expected claims"),
    claimList(e.expected.claims, (c) => [badge(c.kind || "?"), " ", c.content]),
    h("h3", {}, "Expected entities"),
    claimList(e.expected.entities, (x) => `${x.subject} —${x.predicate}→ ${x.object}`),
    h("h3", {}, "Expected mutations"),
    claimList(e.expected.mutations, (m) => [badge(m.action), " ", m.targetClaimId]),
  );
}

function claimsEpisode(runId, data, episodeId, comments) {
  const e = data.episodes[episodeId];
  const results = (data.results || []).filter((r) => r.episodeId === episodeId);
  return h(
    "div",
    {},
    h("h2", {}, episodeId, " ", h("a", { class: "small", href: runLink(runId) }, "← all episodes")),
    h(
      "div",
      { class: "sbs", style: `--cols:${1 + results.length}` },
      h(
        "div",
        { class: "card pane" },
        h("div", { class: "pane-head" }, h("span", { class: "title" }, "Expected")),
        h("div", { class: "pane-body" }, claimsExpected(e)),
      ),
      results.map((r) =>
        h(
          "div",
          { class: "card pane" },
          h("div", { class: "pane-head" }, h("span", { class: "title" }, `Run ${r.run}`)),
          h("div", { class: "pane-body" }, claimsPrediction(r)),
        ),
      ),
    ),
    h(
      "div",
      { class: "card pad", style: "margin-top:14px" },
      h("h3", {}, "Comments on this episode"),
      commentsPanel({
        runId,
        comments,
        target: { case: episodeId },
        filter: (c) => c.target && c.target.case === episodeId,
      }),
    ),
  );
}

async function viewClaimsEpisodeCompare(ids, episodeId) {
  const runs = await Promise.all(ids.map((id) => get(`/api/runs/${enc(id)}`)));
  const e = runs.map((r) => r.claims.episodes[episodeId]).find(Boolean);
  $app.replaceChildren(
    h(
      "div",
      { class: "crumbs" },
      h("a", { href: "#/runs" }, "Runs"),
      " / ",
      h("a", { href: `#/compare?ids=${enc(ids.join(","))}` }, "compare"),
      " / ",
      episodeId,
    ),
    h("h1", {}, episodeId),
    h(
      "div",
      { class: "sbs", style: `--cols:${1 + runs.length};margin-top:12px` },
      h(
        "div",
        { class: "card pane" },
        h("div", { class: "pane-head" }, h("span", { class: "title" }, "Expected")),
        h("div", { class: "pane-body" }, e ? claimsExpected(e) : "–"),
      ),
      runs.map((r) =>
        h(
          "div",
          { class: "card pane" },
          h("div", { class: "pane-head" }, h("span", { class: "title" }, r.record.name)),
          h(
            "div",
            { class: "pane-body" },
            claimsPrediction((r.claims.results || []).find((x) => x.episodeId === episodeId)),
          ),
        ),
      ),
    ),
    h(
      "div",
      { class: "card pad", style: "margin-top:14px" },
      h("h3", {}, "Comments"),
      runs.map((r) =>
        h(
          "div",
          { style: "margin-bottom:12px" },
          h("div", { class: "small muted" }, r.record.name),
          commentsPanel({
            runId: r.record.id,
            comments: r.comments,
            target: { case: episodeId },
            filter: (c) => c.target && c.target.case === episodeId,
          }),
        ),
      ),
    ),
  );
}

// ---------- router ----------

async function route() {
  leaveView();
  updateNav();
  const [path, query] = location.hash.replace(/^#/, "").split("?");
  const params = new URLSearchParams(query || "");
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  try {
    if (!parts.length || parts[0] === "runs") await viewRuns(params);
    else if (parts[0] === "run" && parts[2] === "unit")
      await viewUnit(parts[1], parts[3], parts[4]);
    else if (parts[0] === "run") await viewRun(parts[1], params);
    else if (parts[0] === "compare" && parts[1] === "unit") await viewCompareUnit(params);
    else if (parts[0] === "compare") await viewCompare(params);
    else if (parts[0] === "dataset" && parts[1]) await viewCase(parts[1]);
    else if (parts[0] === "dataset") await viewDataset(params);
    else if (parts[0] === "new-run") await viewNewRun(params);
    else if (parts[0] === "jobs" && parts[1]) await viewJob(parts[1]);
    else if (parts[0] === "jobs") await viewJobs();
    else if (parts[0] === "setup") await viewSetup();
    else
      $app.replaceChildren(
        h(
          "div",
          { class: "card empty" },
          "Not found. ",
          h("a", { href: "#/runs" }, "Back to runs"),
        ),
      );
  } catch (e) {
    $app.replaceChildren(
      h(
        "div",
        { class: "card pad" },
        h("div", { class: "notice" }, e.message),
        h("a", { href: "#/runs" }, "Back to runs"),
      ),
    );
  }
  window.scrollTo(0, 0);
}

function initTheme() {
  const saved = local.get("theme", null);
  if (saved) document.documentElement.dataset.theme = saved;
  document.getElementById("theme-toggle").addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.dataset.theme = dark ? "light" : "dark";
    local.set("theme", document.documentElement.dataset.theme);
  });
}

(async function main() {
  initTheme();
  try {
    CATALOG_LIST = await get("/api/catalog");
    CATALOG = Object.fromEntries(CATALOG_LIST.map((m) => [m.key, m]));
  } catch (e) {
    toast("Could not load the metric catalog: " + e.message, true);
  }
  window.addEventListener("hashchange", route);
  route();
})();
