/* KCD table helper: resizable columns + click-to-sort + simple printer filter.
 *
 * Design goals:
 * - Optional: if JS fails, tables still render and forms still work.
 * - Non-destructive: sorting/filtering only reorders/hides existing rows.
 * - LocalStorage persistence for column widths + sort state.
 */

(function () {
  const MIN_WIDTH_PX = 60;

  function tableKey(table) {
    return table.getAttribute("data-kcd-table-id") || table.id || "";
  }

  function storageKeyForWidths(table) {
    const key = tableKey(table);
    return key ? `kcd_colwidths::${key}` : "";
  }

  function storageKeyForSort(table) {
    const key = tableKey(table);
    return key ? `kcd_sort::${key}` : "";
  }

  function storageKeyForHiddenCols(table) {
    const key = tableKey(table);
    return key ? `kcd_hidden_cols::${key}` : "";
  }

  function loadJson(key, fallback) {
    if (!key) return fallback;
    try {
      const raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch (e) {
      return fallback;
    }
  }

  function saveJson(key, value) {
    if (!key) return;
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch (e) { }
  }

  function ensureColgroup(table, colCount) {
    let colgroup = table.querySelector("colgroup");
    if (!colgroup) {
      colgroup = document.createElement("colgroup");
      const thead = table.querySelector("thead");
      if (thead) {
        table.insertBefore(colgroup, thead);
      } else {
        table.insertBefore(colgroup, table.firstChild);
      }
    }
    const cols = colgroup.querySelectorAll("col");
    const existing = cols.length;
    for (let i = existing; i < colCount; i++) {
      colgroup.appendChild(document.createElement("col"));
    }
    while (colgroup.children.length > colCount) {
      colgroup.removeChild(colgroup.lastChild);
    }
    return colgroup.querySelectorAll("col");
  }

  function getColKey(th, index) {
    return th.getAttribute("data-col") || th.textContent.trim() || `col_${index}`;
  }

  function isFixedColKey(colKey) {
    return colKey === "__select__" || colKey === "expand" || colKey === "actions";
  }

  function getHeaderCols(table) {
    const headerRow = table.querySelector("thead tr");
    if (!headerRow) return [];
    const ths = Array.from(headerRow.querySelectorAll("th"));
    return ths.map((th, i) => {
      const key = getColKey(th, i);
      const label = (th.getAttribute("data-col-label") || th.textContent || "").trim() || key;
      return { th, index: i, key, label, fixed: isFixedColKey(key) || th.getAttribute("data-colvis") === "fixed" };
    });
  }

  function setColHidden(table, colKey, hidden) {
    table.querySelectorAll(`th[data-col="${colKey}"], td[data-col="${colKey}"]`).forEach((el) => {
      el.classList.toggle("kcd-col-hidden", !!hidden);
    });

    const cols = getHeaderCols(table);
    if (!cols.length) return;
    const idx = cols.findIndex(c => c.key === colKey);
    if (idx < 0) return;

    const colEls = ensureColgroup(table, cols.length);
    if (colEls[idx]) {
      colEls[idx].style.display = hidden ? "none" : "";
    }
  }

  function initColumnVisibility(table) {
    if (!table || table.dataset.kcdColvisInit === "1") return;
    if (table.getAttribute("data-kcd-colvis") !== "true") return;
    if (!tableKey(table)) return;

    table.dataset.kcdColvisInit = "1";

    const cols = getHeaderCols(table);
    if (!cols.length) return;

    const skey = storageKeyForHiddenCols(table);
    const hiddenRaw = loadJson(skey, []);
    const hidden = new Set(Array.isArray(hiddenRaw) ? hiddenRaw.filter(v => typeof v === "string") : []);

    // Fixed columns are always visible.
    cols.forEach((c) => {
      if (c.fixed) hidden.delete(c.key);
    });

    cols.forEach((c) => setColHidden(table, c.key, hidden.has(c.key)));

    // Build the "Columns" dropdown UI
    const key = tableKey(table);
    let container = null;
    try {
      container = document.querySelector(`[data-kcd-colvis-for="${CSS.escape(key)}"]`);
    } catch (e) {
      container = document.querySelector(`[data-kcd-colvis-for="${key}"]`);
    }
    if (!container) {
      container = document.createElement("div");
      container.className = "d-flex justify-content-end mb-2";
      table.parentElement?.insertBefore(container, table);
    }
    container.innerHTML = "";

    const dropdown = document.createElement("div");
    dropdown.className = "dropdown";

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-sm btn-outline-secondary dropdown-toggle";
    btn.setAttribute("data-bs-toggle", "dropdown");
    btn.setAttribute("data-bs-auto-close", "outside");
    btn.setAttribute("aria-expanded", "false");
    btn.textContent = "Columns";
    dropdown.appendChild(btn);

    const menu = document.createElement("div");
    menu.className = "dropdown-menu dropdown-menu-end p-2";
    menu.style.minWidth = "220px";

    cols.forEach((c) => {
      const row = document.createElement("div");
      row.className = "form-check";

      const input = document.createElement("input");
      input.className = "form-check-input";
      input.type = "checkbox";
      input.id = `kcd-colvis-${key}-${c.index}`;
      input.checked = !hidden.has(c.key);
      input.disabled = !!c.fixed;

      const label = document.createElement("label");
      label.className = "form-check-label";
      label.htmlFor = input.id;
      label.textContent = c.label;

      input.addEventListener("change", () => {
        if (c.fixed) {
          input.checked = true;
          return;
        }
        if (input.checked) {
          hidden.delete(c.key);
        } else {
          hidden.add(c.key);
        }
        saveJson(skey, Array.from(hidden));
        setColHidden(table, c.key, hidden.has(c.key));
      });

      row.appendChild(input);
      row.appendChild(label);
      menu.appendChild(row);
    });

    dropdown.appendChild(menu);
    container.appendChild(dropdown);
  }

  function initResizableTable(table) {
    if (!table || table.dataset.kcdResizableInit === "1") return;
    if (table.getAttribute("data-kcd-resizable") !== "true") return;
    if (!tableKey(table)) return;

    table.dataset.kcdResizableInit = "1";
    table.classList.add("kcd-col-resize-table");

    const headerRow = table.querySelector("thead tr");
    if (!headerRow) return;

    const ths = Array.from(headerRow.querySelectorAll("th"));
    if (!ths.length) return;

    const cols = ensureColgroup(table, ths.length);
    const skey = storageKeyForWidths(table);
    const widths = loadJson(skey, {});

    ths.forEach((th, i) => {
      const colKey = getColKey(th, i);
      const w = widths[colKey];
      if (typeof w === "number" && w >= MIN_WIDTH_PX) {
        cols[i].style.width = `${w}px`;
      }
    });

    ths.forEach((th, i) => {
      if (th.querySelector(".kcd-col-resizer")) return;
      const resizer = document.createElement("div");
      resizer.className = "kcd-col-resizer";
      resizer.title = "Drag to resize. Double-click to reset.";
      th.appendChild(resizer);

      function resetColumn() {
        const colKey = getColKey(th, i);
        const current = loadJson(skey, {});
        delete current[colKey];
        saveJson(skey, current);
        cols[i].style.width = "";
      }

      resizer.addEventListener("dblclick", (e) => {
        e.preventDefault();
        e.stopPropagation();
        resetColumn();
      });

      resizer.addEventListener("mousedown", (e) => {
        e.preventDefault();
        e.stopPropagation();

        const startX = e.clientX;
        const startWidth = cols[i].getBoundingClientRect().width || th.getBoundingClientRect().width;

        document.body.classList.add("kcd-col-resize-active");

        function onMove(ev) {
          const dx = ev.clientX - startX;
          const next = Math.max(MIN_WIDTH_PX, Math.round(startWidth + dx));
          cols[i].style.width = `${next}px`;
        }

        function onUp() {
          document.removeEventListener("mousemove", onMove);
          document.removeEventListener("mouseup", onUp);
          document.body.classList.remove("kcd-col-resize-active");

          const finalWidth = Math.round(cols[i].getBoundingClientRect().width);
          const colKey = getColKey(th, i);
          const current = loadJson(skey, {});
          current[colKey] = Math.max(MIN_WIDTH_PX, finalWidth);
          saveJson(skey, current);
        }

        document.addEventListener("mousemove", onMove);
        document.addEventListener("mouseup", onUp);
      });
    });
  }

  function parseDateTime(text) {
    const s = String(text || "").trim();
    // Expected: YYYY-MM-DD HH:MM:SS (or two-line date/time)
    const m = s.match(/(\d{4})-(\d{2})-(\d{2})[ T\n]+(\d{2}):(\d{2})(?::(\d{2}))?/);
    if (!m) return NaN;
    const y = Number(m[1]);
    const mo = Number(m[2]) - 1;
    const d = Number(m[3]);
    const hh = Number(m[4]);
    const mm = Number(m[5]);
    const ss = Number(m[6] || "0");
    const dt = new Date(y, mo, d, hh, mm, ss, 0);
    const t = dt.getTime();
    return Number.isFinite(t) ? t : NaN;
  }

  function getCellValue(row, colIndex) {
    const cells = row && row.cells ? row.cells : [];
    const cell = cells[colIndex];
    if (!cell) return "";
    const raw = cell.getAttribute("data-sort");
    return raw != null ? raw : (cell.textContent || "").trim();
  }

  function initSortableTable(table) {
    if (!table || table.dataset.kcdSortableInit === "1") return;
    if (table.getAttribute("data-kcd-sortable") !== "true") return;
    if (!tableKey(table)) return;

    const headerRow = table.querySelector("thead tr");
    const tbody = table.querySelector("tbody");
    if (!headerRow || !tbody) return;

    table.dataset.kcdSortableInit = "1";

    const ths = Array.from(headerRow.querySelectorAll("th"));
    if (!ths.length) return;

    const sortKey = storageKeyForSort(table);
    const state = loadJson(sortKey, { colIndex: null, dir: "asc" });

    function setIndicators(activeIndex, dir) {
      ths.forEach((th, idx) => {
        const el = th.querySelector(".kcd-sort-indicator");
        if (!el) return;
        el.textContent = (idx === activeIndex) ? (dir === "asc" ? "▲" : "▼") : "";
      });
    }

    function sortBy(colIndex, dir) {
      const th = ths[colIndex];
      if (!th) return;

      const type = (th.getAttribute("data-sort-type") || "text").toLowerCase();
      const rows = Array.from(tbody.querySelectorAll("tr"));

      rows.sort((a, b) => {
        const av = getCellValue(a, colIndex);
        const bv = getCellValue(b, colIndex);

        if (type === "number") {
          const an = parseFloat(String(av).replace(/[^0-9.\-]/g, "")) || 0;
          const bn = parseFloat(String(bv).replace(/[^0-9.\-]/g, "")) || 0;
          return dir === "asc" ? an - bn : bn - an;
        }

        if (type === "date") {
          const at = parseDateTime(av);
          const bt = parseDateTime(bv);
          if (Number.isFinite(at) && Number.isFinite(bt)) {
            return dir === "asc" ? (at - bt) : (bt - at);
          }
          // Fallback: some tables provide numeric timestamps via data-sort.
          const an = parseFloat(av);
          const bn = parseFloat(bv);
          if (Number.isFinite(an) && Number.isFinite(bn)) {
            return dir === "asc" ? (an - bn) : (bn - an);
          }
        }

        const as = String(av).toLowerCase();
        const bs = String(bv).toLowerCase();
        const cmp = as.localeCompare(bs);
        return dir === "asc" ? cmp : -cmp;
      });

      rows.forEach(r => tbody.appendChild(r));

      saveJson(sortKey, { colIndex, dir });
      setIndicators(colIndex, dir);
    }

    ths.forEach((th, idx) => {
      if (th.getAttribute("data-sortable") === "false") return;
      if (th.querySelector("input,button,select,a")) return;

      th.style.cursor = "pointer";
      const ind = th.querySelector(".kcd-sort-indicator");
      if (!ind) {
        const span = document.createElement("span");
        span.className = "text-muted small ms-1 kcd-sort-indicator";
        th.appendChild(span);
      }

      th.addEventListener("click", () => {
        const current = loadJson(sortKey, { colIndex: null, dir: "asc" });
        const nextDir = (current.colIndex === idx) ? (current.dir === "asc" ? "desc" : "asc") : "asc";
        sortBy(idx, nextDir);
      });
    });

    if (typeof state.colIndex === "number") {
      sortBy(state.colIndex, state.dir || "asc");
    }
  }

  function initPrinterFilter(table) {
    const selectId = table.getAttribute("data-kcd-printer-filter");
    if (!selectId) return;
    const filterEl = document.getElementById(selectId);
    if (!filterEl) return;

    const tbody = table.querySelector("tbody");
    if (!tbody) return;
    const rows = Array.from(tbody.querySelectorAll("tr"));

    const printers = new Set();
    for (const r of rows) {
      const p = (r.getAttribute("data-printer") || "").trim();
      if (p) printers.add(p);
    }

    const list = Array.from(printers).sort((a, b) => a.localeCompare(b));
    for (const p of list) {
      const opt = document.createElement("option");
      opt.value = p;
      opt.textContent = p;
      filterEl.appendChild(opt);
    }

    const key = `kcd_printer_filter::${tableKey(table) || selectId}`;
    const saved = localStorage.getItem(key) || "";
    if (saved) filterEl.value = saved;

    function applyFilter() {
      const printer = (filterEl.value || "").trim();
      localStorage.setItem(key, printer);
      for (const r of rows) {
        const rp = (r.getAttribute("data-printer") || "").trim();
        const match = !printer || rp === printer;
        r.classList.toggle("d-none", !match);

        const cb = r.querySelector('input[type="checkbox"]');
        if (cb) {
          cb.disabled = !match;
          if (!match) cb.checked = false;
        }
      }
    }

    filterEl.addEventListener("change", applyFilter);
    applyFilter();
  }

  function initTable(table) {
    initColumnVisibility(table);
    initResizableTable(table);
    initSortableTable(table);
    initPrinterFilter(table);
  }

  function initAll() {
    const tables = new Set();
    document.querySelectorAll("table.kcd-table").forEach(t => tables.add(t));
    document.querySelectorAll('table[data-kcd-resizable="true"]').forEach(t => tables.add(t));
    document.querySelectorAll('table[data-kcd-sortable="true"]').forEach(t => tables.add(t));
    document.querySelectorAll("table[data-kcd-printer-filter]").forEach(t => tables.add(t));
    tables.forEach(initTable);
  }

  window.KcdTable = {
    initAll,
    initTable,
  };

  document.addEventListener("DOMContentLoaded", initAll);
})();
