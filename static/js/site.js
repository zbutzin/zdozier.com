/* zdozier.com: progressive enhancement only. Every page works without this file. */
(function () {
  "use strict";
  var d = document;
  function all(sel, el) { return [].slice.call((el || d).querySelectorAll(sel)); }
  function make(tag, cls, text) {
    var n = d.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  // "synced N days ago", recomputed with the visitor's clock.
  all("[data-synced]").forEach(function (el) {
    var n = Math.floor((Date.now() - Date.parse(el.getAttribute("data-synced"))) / 864e5);
    if (!(n >= 0)) return;
    var t = n ? "synced " + n + (n === 1 ? " day ago" : " days ago") : "synced today";
    if (el.hasAttribute("data-cap")) t = t.charAt(0).toUpperCase() + t.slice(1);
    el.textContent = t;
    if (n > 14) el.parentNode.classList.add("is-warn");
  });

  // Chart tooltips. The SVG <title> is the no-JS fallback, removed here so
  // the browser's own tooltip does not double up. Text goes in via textContent.
  var tip;
  function hide() { if (tip) tip.hidden = true; }
  function show(g, x, y) {
    var data, box = g.closest("figure");
    try { data = JSON.parse(g.getAttribute("data-tip")); } catch (e) { return; }
    if (!box) return;
    if (!tip) { tip = make("div", "viz-tip"); tip.setAttribute("aria-hidden", "true"); }
    box.appendChild(tip);
    tip.textContent = "";
    tip.appendChild(make("div", "tip-h", data[0]));
    data.slice(1).forEach(function (row) {
      var line = make("div");
      if (row[2]) line.appendChild(make("span", "tk key-" + row[2]));
      line.appendChild(make("b", null, row[0]));
      line.appendChild(d.createTextNode(" " + row[1]));
      tip.appendChild(line);
    });
    tip.hidden = false;
    var r = box.getBoundingClientRect(), half = tip.offsetWidth / 2;
    tip.style.left = Math.max(half, Math.min(x - r.left, r.width - half)) + "px";
    tip.style.top = Math.max(y - r.top, tip.offsetHeight + 12) + "px";
  }
  all(".viz-col[data-tip]").forEach(function (g) {
    var t = g.querySelector("title");
    if (t) g.removeChild(t);
    g.addEventListener("pointermove", function (e) { show(g, e.clientX, e.clientY); });
    g.addEventListener("pointerleave", hide);
    g.addEventListener("focus", function () {
      var b = g.getBoundingClientRect();
      show(g, b.left + b.width / 2, b.top + 8);
    });
    g.addEventListener("blur", hide);
  });

  // Mobile menu (<details>): Escape or a tap outside closes it.
  var menu = d.querySelector("details.menu");
  if (menu) {
    d.addEventListener("keydown", function (e) {
      if ((e.key === "Escape" || e.key === "Esc") && menu.open) {
        menu.open = false;
        var sum = menu.querySelector("summary");
        if (sum) sum.focus();
      }
    });
    d.addEventListener("click", function (e) {
      if (menu.open && !menu.contains(e.target)) menu.open = false;
    });
  }

  // Publication filters (chips, type, year, search), mirrored in the URL hash.
  var form = d.getElementById("pub-filters"), list = d.getElementById("pub-list");
  if (!form || !list) return;
  var el = form.elements, items = all(".pub", list), groups = all(".year-group", list);
  var count = d.getElementById("pub-count"), empty = d.getElementById("pub-empty");
  var keys = ["show", "type", "year", "q"];
  function norm(s) {
    s = String(s || "");
    return (s.normalize ? s.normalize("NFD").replace(/[\u0300-\u036f]/g, "") : s).toLowerCase();
  }
  function has(words, w) { return (" " + words + " ").indexOf(" " + w + " ") > -1; }
  items.forEach(function (li) {
    li._q = norm(all(".pub-title, .pub-authors .au-list, .pub-venue", li)
      .map(function (n) { return n.textContent; }).join(" "));
  });
  function state() {
    return { show: el.show.value || "all", type: el.type.value, year: el.year.value, q: el.q.value.trim() };
  }
  function match(li, s, words) {
    var ds = li.dataset, i;
    if (s.show === "lead") { if (ds.role !== "first" && ds.role !== "senior") return false; }
    else if (s.show === "preprints") { if (ds.preprint !== "1") return false; }
    else if (s.show !== "all" && !has(ds.topics, s.show)) return false;
    if (s.type && !has(ds.kind, s.type)) return false;
    if (s.year && ds.year !== s.year) return false;
    for (i = 0; i < words.length; i++) if (li._q.indexOf(words[i]) < 0) return false;
    return true;
  }
  function decode() {
    var h = location.hash.slice(1), s = { show: "all", type: "", year: "", q: "" };
    if (h.indexOf("=") < 0) return false;
    h.split("&").forEach(function (kv) {
      var i = kv.indexOf("="), k = kv.slice(0, i);
      if (i < 1 || keys.indexOf(k) < 0) return;
      try { s[k] = decodeURIComponent(kv.slice(i + 1).replace(/\+/g, " ")); } catch (e) { /* ignore */ }
    });
    el.show.value = s.show;
    if (el.show.value !== s.show) el.show.value = "all";
    ["type", "year"].forEach(function (k) {
      el[k].value = s[k];
      if (el[k].selectedIndex < 0) el[k].selectedIndex = 0;
    });
    el.q.value = s.q;
    return true;
  }
  function apply(write) {
    var s = state(), n = 0, words = norm(s.q).split(/\s+/).filter(Boolean);
    items.forEach(function (li) {
      var ok = match(li, s, words);
      li.hidden = !ok;
      if (ok) n++;
    });
    groups.forEach(function (g) {
      var v = all(".pub:not([hidden])", g).length, c = g.querySelector(".year-n");
      g.hidden = !v;
      if (c) c.textContent = v;
    });
    var what = "";
    if (s.show !== "all") {
      var lab = checkedLabel();
      if (lab && lab.firstChild) what = ": " + String(lab.firstChild.textContent).trim();
    }
    count.textContent = n === items.length ? "Showing all " + n + " works"
      : "Showing " + n + " of " + items.length + " works" + what;
    empty.hidden = n > 0;
    if (write && history.replaceState) {
      var h = keys.filter(function (k) { return s[k] && s[k] !== "all"; })
        .map(function (k) { return k + "=" + encodeURIComponent(s[k]); }).join("&");
      history.replaceState(null, "", h ? "#" + h : location.pathname + location.search);
    }
  }
  function checkedLabel() {
    var i = form.querySelector("input[name=show]:checked");
    return i ? form.querySelector('label[for="' + i.id + '"]') : null;
  }
  // On a phone the chip row scrolls sideways; bring the active chip into view
  // (sideways only, so the page itself does not jump).
  function revealChip() {
    var c = form.querySelector(".chips"), l = checkedLabel();
    if (c && l && c.scrollWidth > c.clientWidth) {
      c.scrollLeft = l.offsetLeft - (parseFloat(getComputedStyle(c).paddingLeft) || 0);
    }
  }
  function reset() { form.reset(); apply(true); revealChip(); el.q.focus(); }
  form.hidden = false;
  form.addEventListener("input", function () { apply(true); });
  form.addEventListener("change", function () { apply(true); });
  form.addEventListener("submit", function (e) { e.preventDefault(); });
  all("#pub-reset, [data-reset]").forEach(function (b) { b.addEventListener("click", reset); });
  window.addEventListener("hashchange", function () { if (decode()) { apply(false); revealChip(); } });
  decode();
  apply(false);
  revealChip();
})();
