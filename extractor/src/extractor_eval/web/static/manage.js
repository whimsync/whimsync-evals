"use strict";

/* Dataset, new runs, jobs and setup pages: everything the CLI can do besides reading
 * results. Long-running work (image build, dataset add/synth, run, resume, rescore) is
 * submitted as a job, which the server executes by running the same CLI command.
 * Uses the helpers defined in app.js (h, api, get, toast, attempt, ...). */

/** replaceChildren that, like h(), flattens arrays and skips null/false children. */
function mount(el, ...kids) {
  el.replaceChildren(...kids.flat(Infinity).filter((k) => k != null && k !== false));
}

// ---- view lifecycle: timers registered here stop when the route changes ----

const viewTimers = [];
function every(ms, fn) {
  viewTimers.push(setInterval(fn, ms));
}
function leaveView() {
  while (viewTimers.length) clearInterval(viewTimers.pop());
}

const JOB_BADGE = {
  queued: "info",
  running: "info",
  succeeded: "good",
  failed: "bad",
  cancelled: "warn",
  lost: "warn",
  orphaned: "warn",
};
function jobBadge(status) {
  return badge(status, JOB_BADGE[status]);
}

function duration(job) {
  if (!job.started_at) return "–";
  const end = job.finished_at ? new Date(job.finished_at) : new Date();
  const s = Math.max(0, Math.round((end - new Date(job.started_at)) / 1000));
  return s < 90 ? `${s}s` : s < 5400 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`;
}

async function startJob(method, path, body, okText) {
  const job = await attempt(() => api(method, path, body), okText || "Job queued");
  pollStatus();
  location.hash = `#/jobs/${enc(job.id)}`;
  return job;
}

function field(label, control, hint) {
  return h(
    "label",
    { class: "field" },
    h("span", { class: "field-label" }, label),
    control,
    hint ? h("span", { class: "muted small" }, hint) : null,
  );
}

const csvList = (s) =>
  (s || "")
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);

// ---- global status (jobs badge in the nav) ----

let lastRunningJob = null;
async function pollStatus() {
  let s;
  try {
    s = await get("/api/status");
  } catch {
    return;
  }
  const link = document.getElementById("nav-jobs");
  if (link) link.textContent = s.active_jobs ? `Jobs (${s.active_jobs})` : "Jobs";
  const running = s.running ? s.running.id : null;
  if (lastRunningJob && running !== lastRunningJob) {
    get(`/api/jobs/${enc(lastRunningJob)}`)
      .then(({ job }) => toast(`${job.title}: ${job.status}`, job.status === "failed"))
      .catch(() => {});
  }
  lastRunningJob = running;
  window.EVAL_STATUS = s;
  const pill = document.getElementById("job-pill");
  if (pill) {
    pill.hidden = !s.active_jobs;
    if (s.running) {
      pill.href = `#/jobs/${enc(s.running.id)}`;
      const queued = s.active_jobs - 1;
      mount(
        pill,
        h("span", { class: "dot", "aria-hidden": "true" }),
        `${s.running.title}${queued ? `, ${queued} waiting` : ""}`,
      );
      pill.title = "Open the live log";
    } else if (s.active_jobs) {
      pill.href = "#/jobs";
      mount(pill, `${s.active_jobs} job${s.active_jobs === 1 ? "" : "s"} waiting`);
    }
  }
}
setInterval(pollStatus, 5000);
pollStatus();

// ---- Dataset list ----

async function viewDataset(params) {
  const data = await get("/api/dataset");
  const suite = params.get("suite") || "";
  let q = params.get("q") || "";
  const chosen = new Set(local.get("datasetSelected", []));
  const host = h("div", { class: "card" });
  const bar = h("div");
  const addHost = h("div");

  const search = h("input", {
    type: "search",
    class: "grow",
    placeholder: "Filter cases, tags…",
    value: q,
    "aria-label": "Filter cases",
    oninput: (e) => {
      q = e.target.value;
      draw();
    },
  });

  const visible = () =>
    data.cases.filter(
      (c) =>
        (!suite || c.suite === suite) &&
        (!q ||
          `${c.id} ${c.suite} ${c.description} ${c.tags.join(" ")}`
            .toLowerCase()
            .includes(q.toLowerCase())),
    );
  const persist = () => local.set("datasetSelected", [...chosen]);

  function draw() {
    const rows = visible();
    mount(
      host,
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
                  h(
                    "th",
                    {},
                    h("input", {
                      type: "checkbox",
                      "aria-label": "Select all visible",
                      checked: rows.every((c) => chosen.has(c.id)),
                      onchange: (e) => {
                        for (const c of rows)
                          e.target.checked ? chosen.add(c.id) : chosen.delete(c.id);
                        persist();
                        draw();
                      },
                    }),
                  ),
                  [
                    "Case",
                    "Suite",
                    "Mode",
                    "Units",
                    "Labelled pages",
                    "Gold",
                    "Labels",
                    "Tags",
                  ].map((x) => h("th", {}, x)),
                ),
              ),
              h(
                "tbody",
                {},
                rows.map((c) =>
                  h(
                    "tr",
                    {},
                    h(
                      "td",
                      {},
                      h("input", {
                        type: "checkbox",
                        "aria-label": `Select ${c.id}`,
                        checked: chosen.has(c.id),
                        onchange: (e) => {
                          e.target.checked ? chosen.add(c.id) : chosen.delete(c.id);
                          persist();
                          draw();
                        },
                      }),
                    ),
                    h(
                      "td",
                      {},
                      h("a", { href: `#/dataset/${enc(c.id)}` }, c.id),
                      c.description ? h("div", { class: "muted small" }, c.description) : null,
                    ),
                    h("td", {}, c.suite),
                    h("td", { class: "small" }, c.quality_mode),
                    h("td", { class: "num" }, c.units),
                    h("td", { class: "num" }, `${c.labelled_pages} (${c.vlm_required} VLM)`),
                    h(
                      "td",
                      {},
                      badge(c.gold_status, c.gold_status === "verified" ? "good" : "warn"),
                    ),
                    h(
                      "td",
                      {},
                      badge(c.labels_status, c.labels_status === "verified" ? "good" : "warn"),
                    ),
                    h("td", { class: "small" }, c.tags.join(", ")),
                  ),
                ),
              ),
            ),
          )
        : h(
            "div",
            { class: "empty" },
            data.cases.length
              ? "No cases match."
              : "No cases yet. Add a PDF or rebuild the synthetic suites.",
          ),
    );
    const ids = [...chosen].filter((id) => data.cases.some((c) => c.id === id));
    mount(
      bar,
      ids.length
        ? h(
            "div",
            { class: "selection-bar" },
            h("b", {}, `${ids.length} selected`),
            h(
              "button",
              {
                class: "primary",
                onclick: () => setHashParams("#/new-run", { cases: ids.join(",") }),
              },
              "Run selected…",
            ),
            h("span", { style: "flex:1" }),
            h(
              "button",
              {
                class: "ghost",
                onclick: () => {
                  chosen.clear();
                  persist();
                  draw();
                },
              },
              "Clear",
            ),
          )
        : null,
    );
  }

  const suites = Object.entries(data.suites);
  const sidebar = h(
    "aside",
    { class: "card sidebar" },
    h("h3", {}, "Suites"),
    sideItem("All cases", data.cases.length, !suite, () => setHashParams("#/dataset", { q })),
    suites.map(([s, n]) =>
      sideItem(s, n, suite === s, () => setHashParams("#/dataset", { suite: s, q })),
    ),
    h(
      "div",
      { style: "margin-top:12px;display:grid;gap:6px" },
      suite
        ? h(
            "button",
            { onclick: () => setHashParams("#/new-run", { suite }) },
            `Run suite ${suite}…`,
          )
        : null,
      h(
        "button",
        {
          onclick: async () => {
            const r = await attempt(() => get("/api/dataset/check"));
            if (r.problems.length) toast(`${r.problems.length} problem(s): ${r.problems[0]}`, true);
            else
              toast(
                `${r.cases} cases OK${r.drafts.length ? ` · ${r.drafts.length} with draft gold` : ""}`,
              );
          },
        },
        "Check dataset",
      ),
      h(
        "button",
        {
          title: "Regenerate the synthetic and link suites (extractor-eval dataset synth)",
          onclick: () => startJob("POST", "/api/dataset/synth", { links: true }),
        },
        "Rebuild synthetic suites",
      ),
    ),
  );

  draw();
  mount(
    $app,
    h(
      "div",
      { class: "layout" },
      sidebar,
      h(
        "section",
        {},
        h(
          "div",
          { class: "page-head" },
          h(
            "div",
            {},
            h("h1", {}, "Dataset"),
            h(
              "p",
              { class: "lede" },
              "The documents runs are scored on. Each case has its PDF, the Markdown it should produce, and labels for each page.",
            ),
          ),
          h(
            "button",
            {
              class: "primary",
              onclick: () =>
                mount(
                  addHost,
                  addForm(Object.keys(data.suites), () => mount(addHost)),
                ),
            },
            "Add PDFs…",
          ),
        ),
        addHost,
        h("div", { class: "toolbar" }, search),
        host,
        bar,
      ),
    ),
  );
  if (params.get("add"))
    mount(
      addHost,
      addForm(Object.keys(data.suites), () => mount(addHost)),
    );
}

/** Upload one or more PDFs, then add each as a case (one `dataset add` job per file). */
function addForm(suites, close) {
  const uploads = []; // {file, token, pages, row fields}
  const list = h("div", { class: "upload-list" });
  const status = h("span", { class: "muted small" });
  const suiteInput = h("input", {
    type: "text",
    value: "mine",
    list: "suite-list",
    style: "width:140px",
    "aria-label": "Suite",
  });
  const tierSel = h(
    "select",
    { "aria-label": "Draft tier" },
    ["max", "balanced", "none"].map((t) =>
      h("option", { value: t }, t === "none" ? "none (empty gold)" : t),
    ),
  );
  const force = h("input", { type: "checkbox" });
  const fileInput = h("input", {
    type: "file",
    accept: "application/pdf,.pdf",
    multiple: true,
    onchange: (e) => handleFiles([...e.target.files]),
  });
  const drop = h(
    "div",
    {
      class: "dropzone",
      tabindex: "0",
      role: "button",
      onclick: () => fileInput.click(),
      onkeydown: (e) => e.key === "Enter" && fileInput.click(),
      ondragover: (e) => {
        e.preventDefault();
        drop.classList.add("over");
      },
      ondragleave: () => drop.classList.remove("over"),
      ondrop: (e) => {
        e.preventDefault();
        drop.classList.remove("over");
        handleFiles(
          [...e.dataTransfer.files].filter(
            (f) => /\.pdf$/i.test(f.name) || f.type === "application/pdf",
          ),
        );
      },
    },
    h("b", {}, "Drop PDFs here"),
    " or click to choose files",
  );

  async function handleFiles(files) {
    for (const file of files) {
      const entry = { file, token: null, error: null };
      uploads.push(entry);
      drawList();
      try {
        const res = await fetch("/api/uploads", {
          method: "POST",
          headers: {
            "X-Filename": encodeURIComponent(file.name),
            "Content-Type": "application/pdf",
          },
          body: file,
        });
        const out = await res.json();
        if (!res.ok) throw new Error(out.detail || res.statusText);
        Object.assign(entry, { token: out.upload, pages: out.pages, id: out.suggested_id });
      } catch (e) {
        entry.error = e.message;
      }
      drawList();
    }
  }

  function drawList() {
    mount(
      list,
      ...uploads.map((u, i) =>
        h(
          "div",
          { class: "upload-row" },
          h(
            "div",
            {},
            h("b", {}, u.file.name),
            h(
              "div",
              { class: "muted small" },
              u.error
                ? h("span", { style: "color:var(--bad)" }, u.error)
                : u.token
                  ? `${u.pages} page(s) uploaded`
                  : "uploading…",
            ),
          ),
          u.token
            ? [
                field(
                  "Case id",
                  h("input", {
                    type: "text",
                    value: u.id,
                    oninput: (e) => (u.id = e.target.value),
                    "aria-label": "Case id",
                  }),
                ),
                field(
                  "Description",
                  h("input", {
                    type: "text",
                    value: u.description || "",
                    oninput: (e) => (u.description = e.target.value),
                    "aria-label": "Description",
                  }),
                ),
                field(
                  "Score only these ranges",
                  h("input", {
                    type: "text",
                    placeholder: "e.g. income:40-43, notes:88-90",
                    value: u.ranges || "",
                    oninput: (e) => (u.ranges = e.target.value),
                    "aria-label": "Ranges",
                  }),
                  u.pages > 15
                    ? "Large PDF: name the pages to score; the rest only counts for speed and cost."
                    : "Empty = score every page.",
                ),
              ]
            : null,
          h(
            "button",
            {
              class: "ghost small",
              "aria-label": `Remove ${u.file.name}`,
              onclick: () => {
                uploads.splice(i, 1);
                drawList();
              },
            },
            "×",
          ),
        ),
      ),
    );
  }

  async function submit() {
    const ready = uploads.filter((u) => u.token);
    if (!ready.length) return toast("Upload at least one PDF first", true);
    let last = null;
    for (const u of ready) {
      try {
        last = await api("POST", "/api/dataset/add", {
          upload: u.token,
          id: (u.id || "").trim(),
          suite: suiteInput.value.trim() || "core",
          description: u.description || "",
          ranges: csvList(u.ranges),
          draft_tier: tierSel.value,
          force: force.checked,
        });
        u.done = true;
      } catch (e) {
        toast(`${u.file.name}: ${e.message}`, true);
      }
    }
    if (last) {
      toast(`${ready.filter((u) => u.done).length} case(s) queued`);
      pollStatus();
      location.hash =
        ready.filter((u) => u.done).length === 1 ? `#/jobs/${enc(last.id)}` : "#/jobs";
    }
  }

  return h(
    "div",
    { class: "card pad", style: "margin-bottom:14px" },
    h("h3", {}, "Add PDFs to the dataset"),
    h(
      "p",
      { class: "muted small" },
      "Each PDF becomes a case. The classifier suggests page labels, and the draft tier's output seeds DRAFT expected Markdown that you correct on the case page, then mark verified.",
    ),
    drop,
    fileInput,
    list,
    h(
      "div",
      { class: "toolbar", style: "margin-top:10px" },
      field("Suite", suiteInput),
      h(
        "datalist",
        { id: "suite-list" },
        suites.map((s) => h("option", { value: s })),
      ),
      field("Seed draft gold from", tierSel),
      h("label", { class: "small" }, force, " replace existing case"),
      h("span", { style: "flex:1" }),
      status,
      h("button", { class: "ghost", onclick: close }, "Cancel"),
      h("button", { class: "primary", onclick: submit }, "Add cases"),
    ),
  );
}

// ---- Case detail: edit gold, labels, status ----

async function viewCase(caseId) {
  const c = await get(`/api/dataset/cases/${enc(caseId)}`);
  const data = await get("/api/dataset");
  const patch = async (body, msg) => {
    await attempt(() => api("PATCH", `/api/dataset/cases/${enc(caseId)}`, body), msg);
    route();
  };
  const statusSeg = (key, value) =>
    seg(
      [
        ["draft", "draft"],
        ["verified", "verified"],
      ],
      value,
      (v) => patch({ [key]: v }, `${key.replace("_status", "")} marked ${v}`),
    );

  const descInput = h("input", {
    type: "text",
    value: c.description,
    class: "grow",
    "aria-label": "Description",
  });
  const suiteInput = h("input", {
    type: "text",
    value: c.suite,
    list: "suite-list2",
    style: "width:140px",
    "aria-label": "Suite",
  });

  const header = h(
    "div",
    {},
    h("div", { class: "crumbs" }, h("a", { href: "#/dataset" }, "Dataset"), " / ", c.id),
    h(
      "div",
      { class: "page-head" },
      h("h1", {}, c.id),
      h(
        "div",
        { class: "toolbar", style: "margin:0" },
        h(
          "button",
          { class: "primary", onclick: () => setHashParams("#/new-run", { cases: c.id }) },
          "Run this case…",
        ),
        h(
          "button",
          {
            class: "danger",
            onclick: async () => {
              if (
                !confirm(
                  `Delete case ${c.id}, its PDF and expected Markdown? Past runs keep their outputs.`,
                )
              )
                return;
              await attempt(() => api("DELETE", `/api/dataset/cases/${enc(c.id)}`), "Case deleted");
              location.hash = "#/dataset";
            },
          },
          "Delete case",
        ),
      ),
    ),
    h(
      "div",
      { class: "meta-line" },
      h("span", {}, "mode ", h("b", {}, c.quality_mode)),
      h("span", {}, "pages ", h("b", {}, c.page_count)),
      h("span", {}, "units ", h("b", {}, c.units)),
      h("span", { class: "mono" }, c.pdf),
    ),
    c.problems.map((p) => h("div", { class: "notice" }, p)),
    h(
      "div",
      { class: "card pad", style: "margin:10px 0" },
      h(
        "div",
        { class: "toolbar", style: "margin:0" },
        field("Expected Markdown (gold)", statusSeg("gold_status", c.gold_status)),
        field("Page labels", statusSeg("labels_status", c.labels_status)),
        field("Suite", suiteInput),
        h(
          "datalist",
          { id: "suite-list2" },
          Object.keys(data.suites).map((s) => h("option", { value: s })),
        ),
        field("Description", descInput),
        h(
          "button",
          {
            onclick: () =>
              patch({ suite: suiteInput.value, description: descInput.value }, "Saved"),
          },
          "Save details",
        ),
      ),
      c.gold_status !== "verified"
        ? h(
            "p",
            { class: "small muted", style: "margin:8px 0 0" },
            "Draft: correct each unit's Markdown below against the page images, then mark it verified. Until then its scores only appear in the 'All units' view.",
          )
        : null,
    ),
  );

  const units = c.unit_list.map((u) => goldEditor(c, u));
  mount(
    $app,
    header,
    h("h2", {}, "Expected Markdown"),
    units.length
      ? units
      : h("div", { class: "card empty" }, "No units: this case has no gold files."),
    h("h2", {}, "Page labels"),
    labelsTable(c, data),
    c.links.length
      ? h(
          "div",
          { class: "card pad", style: "margin-top:14px" },
          h("h3", {}, `Expected links (${c.links.length})`),
          h(
            "div",
            { class: "small" },
            c.links.map((l) =>
              h("div", {}, `p${l.page} `, h("span", { class: "mono" }, `[${l.anchor}](${l.url})`)),
            ),
          ),
        )
      : null,
  );
}

function goldEditor(c, u) {
  let saved = u.gold;
  const ta = h(
    "textarea",
    { class: "gold-input", spellcheck: "false", "aria-label": `Gold Markdown for ${u.id}` },
    u.gold,
  );
  const preview = h("div", { class: "pane-body" });
  const state = h("span", { class: "muted small" });
  let mode = "rendered";
  let timer = null;
  const refresh = () => {
    clearTimeout(timer);
    timer = setTimeout(() => fillPane(preview, ta.value, mode).catch(() => {}), 250);
  };
  const markDirty = () => {
    const dirty = ta.value !== saved;
    state.textContent = dirty ? "unsaved changes" : "saved";
    state.style.color = dirty ? "var(--warn)" : "";
  };
  const save = async () => {
    await attempt(
      () => api("PUT", `/api/dataset/cases/${enc(c.id)}/gold/${enc(u.id)}`, { markdown: ta.value }),
      `${u.id} saved`,
    );
    saved = ta.value;
    markDirty();
  };
  ta.addEventListener("input", () => {
    markDirty();
    refresh();
  });
  ta.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "s") {
      e.preventDefault();
      save();
    }
  });
  const modeSeg = h("span");
  const drawSeg = () =>
    mount(
      modeSeg,
      seg(
        [
          ["rendered", "Rendered"],
          ["raw", "Raw"],
        ],
        mode,
        (m) => {
          mode = m;
          drawSeg();
          refresh();
        },
      ),
    );
  drawSeg();
  markDirty();
  refresh();
  return h(
    "div",
    { class: "card pad", style: "margin-bottom:14px" },
    h(
      "div",
      { class: "page-head", style: "margin:0 0 8px" },
      h(
        "h3",
        { style: "margin:0" },
        `${u.id} · page${u.pages.length > 1 ? "s" : ""} ${u.pages[0]}${u.pages.length > 1 ? "–" + u.pages[u.pages.length - 1] : ""}`,
      ),
      h("span", { class: "mono small muted" }, u.gold_file),
    ),
    h(
      "div",
      { class: "sbs gold-grid", style: "--cols:3;margin-top:10px" },
      h(
        "div",
        { class: "card pane" },
        h(
          "div",
          { class: "pane-head" },
          h("span", { class: "title" }, "Source PDF"),
          h("span", { class: "muted small" }, "click to enlarge"),
        ),
        h(
          "div",
          { class: "pane-body source-pages" },
          u.pages.slice(0, 20).map((p) =>
            h("img", {
              src: `/api/cases/${enc(c.id)}/pages/${p}.png?scale=1.6`,
              alt: `Source page ${p}`,
              loading: "lazy",
              onclick: () =>
                window.open(
                  `/api/cases/${enc(c.id)}/pages/${p}.png?scale=2.5`,
                  "_blank",
                  "noopener",
                ),
            }),
          ),
          u.pages.length > 20
            ? h("div", { class: "muted small" }, `+${u.pages.length - 20} more pages`)
            : null,
        ),
      ),
      h(
        "div",
        { class: "card pane" },
        h("div", { class: "pane-head" }, h("span", { class: "title" }, "Edit"), state),
        ta,
      ),
      h(
        "div",
        { class: "card pane" },
        h("div", { class: "pane-head" }, h("span", { class: "title" }, "Preview"), modeSeg),
        preview,
      ),
    ),
    h(
      "div",
      { class: "toolbar", style: "margin:8px 0 0" },
      h("button", { class: "primary", onclick: save }, "Save"),
      h(
        "button",
        {
          onclick: () => {
            ta.value = saved;
            markDirty();
            refresh();
          },
        },
        "Revert",
      ),
      h(
        "span",
        { class: "muted small" },
        "Ctrl+S saves. Run 'Rescore' on a past run to apply new gold without API calls.",
      ),
    ),
  );
}

function labelsTable(c, data) {
  const pages = new Set(Object.keys(c.page_labels).map(Number));
  for (const u of c.unit_list) for (const p of u.pages) pages.add(p);
  const rows = [...pages]
    .sort((a, b) => a - b)
    .map((p) => labelRow(c, data, p, c.page_labels[String(p)] || {}));
  const addInput = h("input", {
    type: "number",
    min: 1,
    max: c.page_count,
    placeholder: "page",
    style: "width:90px",
    "aria-label": "Page number to label",
  });
  return h(
    "div",
    { class: "card pad" },
    h(
      "p",
      { class: "small muted", style: "margin-top:0" },
      "Expected route drives the routing metrics (a vlm_required page parsed natively is a false native). Reasons must be classifier reason names; use tags for your own labels. Changes save immediately.",
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
            [
              "Page",
              "Expected route",
              "Expected classifier reasons",
              "Tags",
              "Required phrases (one per line)",
            ].map((x) => h("th", {}, x)),
          ),
        ),
        h("tbody", {}, rows),
      ),
    ),
    h(
      "div",
      { class: "toolbar", style: "margin:8px 0 0" },
      addInput,
      h(
        "button",
        {
          onclick: async () => {
            const p = Number(addInput.value);
            if (!p) return;
            await attempt(
              () =>
                api("PUT", `/api/dataset/cases/${enc(c.id)}/labels/${p}`, {
                  expected_route: "either_acceptable",
                }),
              `Page ${p} labelled`,
            );
            route();
          },
        },
        "Label another page",
      ),
    ),
  );
}

function labelRow(c, data, page, label) {
  const put = (body) =>
    attempt(
      () => api("PUT", `/api/dataset/cases/${enc(c.id)}/labels/${page}`, body),
      `p${page} saved`,
    );
  const routeSel = h(
    "select",
    {
      "aria-label": `Route for page ${page}`,
      onchange: (e) => put({ expected_route: e.target.value || null }),
    },
    h("option", { value: "" }, "(unlabelled)"),
    data.routes.map((r) =>
      h("option", { value: r, selected: label.expected_route === r }, r.replace("_", " ")),
    ),
  );
  const reasons = new Set(label.expected_classifier_reasons || []);
  const reasonBoxes = h(
    "div",
    { class: "chips" },
    data.reasons.map((r) =>
      h(
        "label",
        { class: "small nowrap" },
        h("input", {
          type: "checkbox",
          checked: reasons.has(r),
          onchange: (e) => {
            e.target.checked ? reasons.add(r) : reasons.delete(r);
            put({ expected_classifier_reasons: [...reasons] });
          },
        }),
        " ",
        r,
      ),
    ),
  );
  const tags = h("input", {
    type: "text",
    value: (label.tags || []).join(", "),
    placeholder: "comma separated",
    "aria-label": `Tags for page ${page}`,
    onchange: (e) => put({ tags: csvList(e.target.value) }),
  });
  const phrases = h(
    "textarea",
    {
      rows: 2,
      "aria-label": `Required phrases for page ${page}`,
      onchange: (e) => put({ required_phrases: e.target.value.split("\n") }),
    },
    (label.required_phrases || []).join("\n"),
  );
  return h(
    "tr",
    {},
    h(
      "td",
      { class: "num" },
      h(
        "a",
        {
          href: `/api/cases/${enc(c.id)}/pages/${page}.png?scale=1.5`,
          target: "_blank",
          rel: "noopener",
        },
        page,
      ),
    ),
    h("td", {}, routeSel),
    h("td", {}, reasonBoxes),
    h("td", {}, tags),
    h("td", {}, phrases),
  );
}

// ---- New run ----

async function viewNewRun(params) {
  const [data, imgs, status] = await Promise.all([
    get("/api/dataset"),
    get("/api/images"),
    get("/api/status"),
  ]);
  const byId = Object.fromEntries(data.cases.map((c) => [c.id, c]));
  const picked = new Set(csvList(params.get("cases")).filter((id) => byId[id]));
  const pickedSuite = params.get("suite");
  if (pickedSuite) for (const c of data.cases) if (c.suite === pickedSuite) picked.add(c.id);
  const tierParam = csvList(params.get("tiers")).filter((t) => status.tiers.includes(t));
  const tiers = new Set(tierParam.length ? tierParam : status.tiers);
  const bySuite = {};
  for (const c of data.cases) (bySuite[c.suite] ||= []).push(c);

  const casesHost = h("div", { class: "case-picker" });
  const summary = h("p", { class: "run-summary" });
  const startBtn = h("button", { class: "primary big" }, "Start run");

  const imageSel = h(
    "select",
    { "aria-label": "Extractor image" },
    imgs.images.map((i, idx) =>
      h(
        "option",
        { value: i.tag, selected: params.get("image") ? i.tag === params.get("image") : idx === 0 },
        `${i.tag}${idx === 0 ? " (newest)" : ""}${i.dirty ? ", uncommitted changes" : ""}`,
      ),
    ),
  );
  const label = h("input", {
    type: "text",
    value: params.get("label") || "",
    placeholder: "e.g. prompt-v2",
    "aria-label": "Run name",
  });
  const groups = h("input", {
    type: "text",
    value: params.get("groups") || "",
    placeholder: "comma separated",
    "aria-label": "Groups",
  });
  const judge = h("input", {
    type: "checkbox",
    checked: params.has("judge") ? params.get("judge") === "1" : status.gemini_key,
    onchange: () => {
      judgeModel.disabled = !judge.checked;
      drawSummary();
    },
  });
  const judgeModel = h("input", {
    type: "text",
    value: params.get("judge_model") || status.judge_model,
    "aria-label": "Judge model",
  });
  const model = h("input", {
    type: "text",
    value: params.get("model") || "",
    placeholder: "GEMINI_MODEL from .env",
    "aria-label": "Extraction model",
  });
  const perf = h("input", {
    type: "number",
    min: 0,
    max: 10,
    value: params.get("perf") || 0,
    style: "width:80px",
    "aria-label": "Timing repeats",
    oninput: () => drawSummary(),
  });
  const tierBoxes = h(
    "div",
    { class: "chips" },
    status.tiers.map((t) =>
      h(
        "label",
        { class: "check" },
        h("input", {
          type: "checkbox",
          checked: tiers.has(t),
          onchange: (e) => {
            e.target.checked ? tiers.add(t) : tiers.delete(t);
            drawSummary();
          },
        }),
        ` ${t}`,
      ),
    ),
  );

  function drawSummary() {
    const cases = [...picked].map((id) => byId[id]);
    const nTiers = status.tiers.filter((t) => tiers.has(t)).length;
    const units = cases.reduce((n, c) => n + c.units, 0);
    const problems = [];
    if (!cases.length) problems.push("Pick at least one case.");
    if (!nTiers) problems.push("Pick at least one tier.");
    if (!imgs.images.length) problems.push("Build an extractor image first (Setup).");
    const drafts = cases.filter((c) => c.gold_status !== "verified").length;
    mount(
      summary,
      problems.length
        ? h("span", { class: "warn-text" }, problems.join(" "))
        : [
            `Parses ${cases.length} case${cases.length === 1 ? "" : "s"} with ${nTiers === 1 ? "1 tier" : `${nTiers} tiers`} (${cases.length * nTiers} extractions)`,
            judge.checked
              ? ` and asks the judge about ${units * nTiers} unit${units * nTiers === 1 ? "" : "s"}.`
              : ", without the judge.",
            Number(perf.value)
              ? ` Adds ${perf.value} timing repeat${perf.value === "1" ? "" : "s"} per small case.`
              : "",
            drafts
              ? h(
                  "span",
                  { class: "muted" },
                  ` ${drafts} case${drafts === 1 ? " has" : "s have"} draft expected output.`,
                )
              : "",
          ],
    );
    startBtn.disabled = problems.length > 0;
  }

  const pickCases = (ids, on) => {
    for (const id of ids) on ? picked.add(id) : picked.delete(id);
    drawCases();
  };
  function drawCases() {
    mount(
      casesHost,
      Object.entries(bySuite).map(([suite, cases]) => {
        const all = cases.every((c) => picked.has(c.id));
        const some = cases.some((c) => picked.has(c.id));
        const head = h("input", {
          type: "checkbox",
          checked: all,
          onchange: (e) =>
            pickCases(
              cases.map((c) => c.id),
              e.target.checked,
            ),
        });
        head.indeterminate = some && !all;
        return h(
          "details",
          { class: "suite-block", open: some || Object.keys(bySuite).length <= 3 },
          h(
            "summary",
            {},
            h(
              "label",
              { class: "suite-head", onclick: (e) => e.stopPropagation() },
              head,
              ` ${suite}`,
            ),
            h(
              "span",
              { class: "muted small" },
              ` ${cases.filter((c) => picked.has(c.id)).length} of ${cases.length} picked`,
            ),
          ),
          h(
            "div",
            { class: "suite-cases" },
            cases.map((c) =>
              h(
                "label",
                { class: "check small", title: c.description },
                h("input", {
                  type: "checkbox",
                  checked: picked.has(c.id),
                  onchange: (e) => pickCases([c.id], e.target.checked),
                }),
                ` ${c.id}`,
                c.gold_status !== "verified" ? h("span", { class: "muted" }, " (draft)") : null,
              ),
            ),
          ),
        );
      }),
    );
    drawSummary();
  }
  drawCases();
  judgeModel.disabled = !judge.checked;

  startBtn.addEventListener("click", () =>
    startJob(
      "POST",
      "/api/runs/start",
      {
        cases: [...picked],
        tiers: status.tiers.filter((t) => tiers.has(t)),
        image: imageSel.value || null,
        label: label.value.trim() || null,
        groups: csvList(groups.value),
        judge: judge.checked,
        judge_model: judgeModel.value.trim() || null,
        perf_repeats: Number(perf.value) || 0,
        model: model.value.trim() || null,
      },
      "Run started",
    ),
  );

  const notices = [
    !imgs.images.length
      ? h(
          "div",
          { class: "notice" },
          "There's no extractor image yet. ",
          h("a", { href: "#/setup" }, "Build one on Setup"),
          ", then come back.",
        )
      : null,
    !status.gemini_key
      ? h(
          "div",
          { class: "notice" },
          "GEMINI_API_KEY isn't set in whimsync-evals/.env, so pages that need the VLM fall back to plain text and the judge is off.",
        )
      : null,
    !data.cases.length
      ? h(
          "div",
          { class: "notice" },
          "The dataset is empty. ",
          h("a", { href: "#/dataset?add=1" }, "Add PDFs"),
          " first.",
        )
      : null,
  ];

  mount(
    $app,
    h(
      "div",
      { class: "page-head" },
      h(
        "div",
        {},
        h("h1", {}, "New run"),
        h(
          "p",
          { class: "lede" },
          "Pick documents and tiers. The run parses each one in the extractor container, scores it, and appears under Runs when it finishes.",
        ),
      ),
    ),
    notices,
    h(
      "div",
      { class: "run-form" },
      h(
        "section",
        { class: "card pad" },
        h(
          "div",
          { class: "section-head" },
          h("h2", {}, "Documents"),
          h(
            "div",
            { class: "toolbar", style: "margin:0" },
            h(
              "button",
              {
                class: "ghost small",
                onclick: () =>
                  pickCases(
                    data.cases.map((c) => c.id),
                    true,
                  ),
              },
              "Select all",
            ),
            h(
              "button",
              { class: "ghost small", onclick: () => pickCases([...picked], false) },
              "Clear",
            ),
            h("a", { class: "small", href: "#/dataset?add=1" }, "Add PDFs"),
          ),
        ),
        casesHost,
      ),
      h(
        "aside",
        { class: "card pad run-settings" },
        h("h2", {}, "Settings"),
        field("Tiers", tierBoxes),
        field("Run name", label, "You can rename it later."),
        field(
          "Judge",
          h("label", { class: "check" }, judge, " Score each unit with the Gemini judge"),
          "Off scores with exact checks only and makes no judge calls.",
        ),
        h(
          "details",
          { class: "more" },
          h("summary", {}, "More settings"),
          field(
            "Extractor image",
            imageSel,
            "Rebuild on Setup after changing whimsync's extractor code.",
          ),
          field("Groups", groups),
          field("Judge model", judgeModel),
          field("Extraction model", model),
          field("Timing repeats", perf, "Extra unscored parses per small case, for speed numbers."),
        ),
        summary,
        startBtn,
      ),
    ),
  );
}

// ---- Jobs ----

async function viewJobs() {
  const host = h("div", { class: "card" });
  const draw = async () => {
    const jobs = await get("/api/jobs");
    mount(
      host,
      jobs.length
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
                  ["Job", "Status", "Queued", "Duration", "Result", ""].map((x) => h("th", {}, x)),
                ),
              ),
              h(
                "tbody",
                {},
                jobs.map((j) =>
                  h(
                    "tr",
                    { class: "clickable", onclick: () => (location.hash = `#/jobs/${enc(j.id)}`) },
                    h(
                      "td",
                      {},
                      h("a", { href: `#/jobs/${enc(j.id)}` }, j.title),
                      h("div", { class: "muted small mono" }, j.id),
                    ),
                    h(
                      "td",
                      {},
                      jobBadge(j.status),
                      j.error && j.status !== "succeeded"
                        ? h(
                            "div",
                            { class: "small", style: "color:var(--bad);max-width:360px" },
                            j.error,
                          )
                        : null,
                    ),
                    h("td", { class: "small nowrap" }, fmtDate(j.created_at)),
                    h("td", { class: "small nowrap" }, duration(j)),
                    h("td", { class: "small" }, jobLinks(j)),
                    h(
                      "td",
                      {},
                      ["queued", "running", "orphaned"].includes(j.status)
                        ? h(
                            "button",
                            {
                              class: "ghost small danger",
                              onclick: async (e) => {
                                e.stopPropagation();
                                await attempt(
                                  () => api("POST", `/api/jobs/${enc(j.id)}/cancel`),
                                  "Cancel requested",
                                );
                                draw();
                              },
                            },
                            "Cancel",
                          )
                        : null,
                    ),
                  ),
                ),
              ),
            ),
          )
        : h("div", { class: "empty" }, "No jobs yet. Start a run, add PDFs, or build an image."),
    );
  };
  await draw();
  every(3000, () => draw().catch(() => {}));
  mount(
    $app,
    h(
      "div",
      { class: "page-head" },
      h(
        "div",
        {},
        h("h1", {}, "Jobs"),
        h(
          "p",
          { class: "lede" },
          "Work you started from this UI: image builds, added PDFs and runs. One runs at a time, in order.",
        ),
      ),
      h(
        "div",
        { class: "toolbar", style: "margin:0" },
        h("a", { class: "btn", href: "#/new-run" }, "New run"),
        h("a", { class: "btn", href: "#/dataset?add=1" }, "Add PDFs"),
      ),
    ),
    host,
  );
}

function jobLinks(j) {
  const out = [];
  if (j.links.run) out.push(h("a", { href: `#/run/${enc(j.links.run)}` }, "open run"));
  if (j.links.case && j.status === "succeeded")
    out.push(h("a", { href: `#/dataset/${enc(j.links.case)}` }, `review ${j.links.case}`));
  if (j.kind === "image-build" && j.status === "succeeded")
    out.push(h("a", { href: "#/setup" }, "images"));
  return out.length ? out.flatMap((x, i) => (i ? [" · ", x] : [x])) : "–";
}

async function viewJob(jobId) {
  let offset = 0;
  const pre = h("pre", { class: "log" });
  const head = h("div");
  let follow = true;
  pre.addEventListener("scroll", () => {
    follow = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 30;
  });
  let job;
  const drawHead = () =>
    mount(
      head,
      h("div", { class: "crumbs" }, h("a", { href: "#/jobs" }, "Jobs"), " / ", job.id),
      h(
        "div",
        { class: "page-head" },
        h("h1", {}, job.title),
        h(
          "div",
          { class: "toolbar", style: "margin:0" },
          jobLinks(job),
          ["queued", "running", "orphaned"].includes(job.status)
            ? h(
                "button",
                {
                  class: "danger",
                  onclick: async () => {
                    await attempt(
                      () => api("POST", `/api/jobs/${enc(job.id)}/cancel`),
                      "Cancel requested",
                    );
                    tick();
                  },
                },
                "Cancel",
              )
            : null,
        ),
      ),
      h(
        "div",
        { class: "meta-line" },
        jobBadge(job.status),
        h("span", {}, "queued ", h("b", {}, fmtDate(job.created_at))),
        h("span", {}, "duration ", h("b", {}, duration(job))),
        job.exit_code != null ? h("span", {}, "exit ", h("b", {}, job.exit_code)) : null,
      ),
      h("div", { class: "mono small muted", style: "margin:6px 0" }, job.argv.join(" ")),
      job.error && job.status !== "succeeded" ? h("div", { class: "notice" }, job.error) : null,
      job.status === "queued"
        ? h("div", { class: "muted small" }, "Waiting for the job ahead of it to finish…")
        : null,
    );
  const tick = async () => {
    const res = await get(`/api/jobs/${enc(jobId)}?offset=${offset}`);
    const changed = !job || job.status !== res.job.status;
    job = res.job;
    if (res.log.text) {
      pre.append(document.createTextNode(res.log.text));
      offset = res.log.offset;
      if (follow) pre.scrollTop = pre.scrollHeight;
    }
    if (changed || ["running"].includes(job.status)) drawHead();
    if (!["queued", "running"].includes(job.status)) leaveView();
  };
  await tick();
  mount($app, head, pre);
  pre.scrollTop = pre.scrollHeight;
  if (["queued", "running"].includes(job.status)) every(1500, () => tick().catch(() => {}));
}

// ---- Setup: images ----

async function viewSetup() {
  const [imgs, status] = await Promise.all([get("/api/images"), get("/api/status")]);
  const root = h("input", {
    type: "text",
    value: imgs.default_extractor_root,
    class: "grow",
    "aria-label": "whimsync checkout",
  });
  const tag = h("input", {
    type: "text",
    placeholder: "default: commit[-dirty-hash]",
    "aria-label": "Image tag",
  });
  mount(
    $app,
    h(
      "div",
      { class: "page-head" },
      h(
        "div",
        {},
        h("h1", {}, "Setup"),
        h(
          "p",
          { class: "lede" },
          "Runs use a Docker image of whimsync's extractor. Rebuild it whenever the extractor code changes.",
        ),
      ),
    ),
    h(
      "div",
      { class: "stat-grid" },
      h(
        "div",
        { class: "card stat" },
        h("div", { class: "label" }, "Gemini API key"),
        h("div", { class: "value" }, status.gemini_key ? "set" : "missing"),
        h("div", { class: "sub" }, "whimsync-evals/.env"),
      ),
      h(
        "div",
        { class: "card stat" },
        h("div", { class: "label" }, "Judge model"),
        h("div", { class: "value", style: "font-size:16px" }, status.judge_model),
      ),
      h(
        "div",
        { class: "card stat" },
        h("div", { class: "label" }, "Extractor images"),
        h("div", { class: "value" }, imgs.images.length),
      ),
    ),
    h(
      "div",
      { class: "card pad", style: "margin-top:14px" },
      h("h3", { style: "margin-top:0" }, "Build an extractor image"),
      h(
        "p",
        { class: "muted small" },
        "Builds whimsync-extractor-eval:<commit> from apps/extractor/Dockerfile.eval. Rebuild whenever the extractor code changes; uncommitted changes get a -dirty-<hash> tag.",
      ),
      h(
        "div",
        { class: "toolbar" },
        field("whimsync checkout", root),
        field("Tag (optional)", tag),
        h(
          "button",
          {
            class: "primary",
            onclick: () =>
              startJob(
                "POST",
                "/api/images/build",
                { extractor_root: root.value.trim() || null, tag: tag.value.trim() || null },
                "Build queued",
              ),
          },
          "Build image",
        ),
      ),
    ),
    h(
      "div",
      { class: "card", style: "margin-top:14px" },
      imgs.images.length
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
                  ["Tag", "Commit", "Built", "Digest", "Checkout"].map((x) => h("th", {}, x)),
                ),
              ),
              h(
                "tbody",
                {},
                imgs.images.map((i, idx) =>
                  h(
                    "tr",
                    {},
                    h(
                      "td",
                      { class: "mono" },
                      i.tag,
                      idx === 0 ? [" ", badge("latest", "info")] : null,
                      i.dirty ? [" ", badge("uncommitted changes", "warn")] : null,
                    ),
                    h("td", { class: "mono small" }, i.commit),
                    h("td", { class: "small nowrap" }, fmtDate(i.built_at)),
                    h("td", { class: "mono small" }, (i.digest || "").slice(0, 19)),
                    h("td", { class: "mono small" }, i.extractor_root),
                  ),
                ),
              ),
            ),
          )
        : h("div", { class: "empty" }, "No images built yet."),
    ),
  );
}

// ---- run page actions (used by app.js's run header) ----

function runActions(runId, status, run, name) {
  const cfg = run?.config || {};
  return [
    h(
      "a",
      {
        class: "btn",
        title: "Open New run with this run's documents and settings",
        href:
          "#/new-run?" +
          new URLSearchParams({
            cases: (cfg.cases || []).join(","),
            tiers: (cfg.tiers || []).join(","),
            label: `${name || "run"}-again`,
            judge: cfg.judge ? "1" : "0",
            ...(cfg.judge_model ? { judge_model: cfg.judge_model } : {}),
            ...(cfg.perf_repeats ? { perf: String(cfg.perf_repeats) } : {}),
            ...(run?.image?.tag ? { image: run.image.tag } : {}),
          }).toString(),
      },
      "Run again",
    ),
    h(
      "button",
      {
        title:
          "Recompute scores from the saved outputs, e.g. after fixing expected Markdown. No API calls.",
        onclick: () => startJob("POST", `/api/runs/${enc(runId)}/rescore`, null, "Rescore started"),
      },
      "Rescore",
    ),
  ];
}

/** Shown on a run page while a job works on it, or when it stopped part-way. */
function runBanner(runId, status) {
  const host = h("div");
  get("/api/jobs")
    .then((jobs) => {
      const job = jobs.find(
        (j) =>
          ["queued", "running"].includes(j.status) &&
          (j.links.run === runId ||
            j.argv.includes(runId) ||
            j.argv.some((a) => a.endsWith(`=${runId}`))),
      );
      if (job)
        mount(
          host,
          h(
            "div",
            { class: "notice info" },
            `${job.title} is ${job.status === "queued" ? "waiting to start" : "in progress"}. Scores update when it finishes. `,
            h("a", { href: `#/jobs/${enc(job.id)}` }, "Watch the log"),
          ),
        );
      else if (status !== "complete")
        mount(
          host,
          h(
            "div",
            { class: "notice" },
            "This run stopped before every document was scored. ",
            h(
              "button",
              {
                class: "small",
                onclick: () =>
                  startJob("POST", `/api/runs/${enc(runId)}/resume`, null, "Resume started"),
              },
              "Resume",
            ),
            " continues from where it stopped.",
          ),
        );
    })
    .catch(() => {});
  return host;
}

/** Runs page title, actions and the first-time checklist. */
function runsHeader(runs) {
  const checklist = h("div");
  if (!local.get("hideChecklist", false))
    Promise.all([get("/api/status"), get("/api/dataset")])
      .then(([s, d]) => {
        const own = d.cases.filter((c) => !["synthetic", "links"].includes(c.suite));
        const steps = [
          [s.images > 0, "Build the extractor image", "#/setup", "Setup"],
          [s.gemini_key, "Add GEMINI_API_KEY to whimsync-evals/.env", null, null],
          [own.length > 0, "Add your own PDFs", "#/dataset?add=1", "Add PDFs"],
          [
            own.length > 0 && own.every((c) => c.gold_status === "verified"),
            "Correct their expected Markdown and mark it verified",
            "#/dataset",
            "Dataset",
          ],
          [runs.some((r) => r.kind === "extractor"), "Start a run", "#/new-run", "New run"],
        ];
        if (steps.every(([done]) => done)) return;
        mount(
          checklist,
          h(
            "div",
            { class: "card pad checklist" },
            h(
              "div",
              { class: "section-head" },
              h("h2", {}, "Getting started"),
              h(
                "button",
                {
                  class: "ghost small",
                  onclick: () => {
                    local.set("hideChecklist", true);
                    mount(checklist);
                  },
                },
                "Hide",
              ),
            ),
            h(
              "ol",
              {},
              steps.map(([done, text, href, cta]) =>
                h(
                  "li",
                  { class: done ? "done" : "" },
                  h("span", {}, text),
                  done
                    ? h("span", { class: "muted small" }, "done")
                    : href
                      ? h("a", { class: "btn small", href }, cta)
                      : null,
                ),
              ),
            ),
          ),
        );
      })
      .catch(() => {});
  return h(
    "div",
    {},
    h(
      "div",
      { class: "page-head" },
      h(
        "div",
        {},
        h("h1", {}, "Runs"),
        h(
          "p",
          { class: "lede" },
          "Each run scores a set of documents on one or more tiers. Tick two or more runs to compare them side by side.",
        ),
      ),
    ),
    checklist,
  );
}
