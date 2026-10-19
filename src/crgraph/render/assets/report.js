(function () {
  "use strict";
  var DATA = JSON.parse(document.getElementById("crgraph-data").textContent);
  var STATUSES = ["added", "modified", "removed", "moved", "impacted", "unchanged"];
  var CHANGED = { added: 1, modified: 1, removed: 1, moved: 1 };
  var RANK = { removed: 5, added: 4, modified: 3, moved: 2, impacted: 1, unchanged: 0 };
  var KIND_LABEL = { depends_on: "depends on", imports: "imports", calls: "calls", references: "references", inherits: "inherits" };
  var ICON = { red: "🔴", yellow: "🟡", info: "⚪" };
  var LABEL = { red: "Needs attention", yellow: "Worth a look", info: "Informational" };
  var MAX_VISIBLE = 300;
  var MAX_REVEAL_ZOOM = 1.2;  // jumping to a single box: show it with its neighbours, not filling the screen
  var MIN_START_ZOOM = 0.45;

  DATA.edges.forEach(function (e) { e.id = e.src + "->" + e.dst + ":" + e.kind; });  // same as model.edge_id
  var HINT = document.getElementById("hint").textContent;
  var edgesById = new Map(DATA.edges.map(function (e) { return [e.id, e]; }));
  var nodes = new Map(DATA.nodes.map(function (n) { return [n.id, n]; }));
  var children = new Map();
  DATA.nodes.forEach(function (n) {
    if (n.parent && nodes.has(n.parent)) {
      if (!children.has(n.parent)) children.set(n.parent, []);
      children.get(n.parent).push(n.id);
    }
  });
  var findingsById = new Map(DATA.findings.map(function (f) { return [f.id, f]; }));
  var RED_TARGETS = new Set();
  DATA.findings.forEach(function (f) { if (f.severity === "red") f.targets.forEach(function (t) { RED_TARGETS.add(t); }); });
  var incoming = new Map(), outgoing = new Map();
  DATA.edges.forEach(function (e) {
    if (e.kind === "depends_on" && !(nodes.has(e.src) && nodes.get(e.src).kind === "module")) return;
    if (!outgoing.has(e.src)) outgoing.set(e.src, []);
    if (!incoming.has(e.dst)) incoming.set(e.dst, []);
    outgoing.get(e.src).push(e);
    incoming.get(e.dst).push(e);
  });

  var state = { mode: "merged", expanded: new Set(), focus: true, tests: true, statuses: new Set(STATUSES), highlight: new Set(), selected: null };
  var cy = null;

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  // Box width from the label's real rendered width, so short names get snug boxes and long ones never overflow.
  var NODE_FONT_SIZE = 12, NODE_H = 30, measureCtx = document.createElement("canvas").getContext("2d");
  function boxWidth(text) {
    measureCtx.font = "400 " + NODE_FONT_SIZE + "px " + (css("--sans") || "sans-serif");
    return Math.max(56, Math.ceil(measureCtx.measureText(text).width) + 28);
  }
  function ancestors(id) {
    var out = [], p = nodes.has(id) ? nodes.get(id).parent : null;
    while (p && nodes.has(p)) { out.push(p); p = nodes.get(p).parent; }
    return out;
  }

  // focus mode: changed and impacted nodes (impact is already the one hop around a change),
  // the endpoints of added/removed edges, and their containers
  var interesting = new Set();
  function addWithAncestors(id) { interesting.add(id); ancestors(id).forEach(function (a) { interesting.add(a); }); }
  DATA.nodes.forEach(function (n) { if (n.status !== "unchanged") addWithAncestors(n.id); });
  DATA.edges.forEach(function (e) {
    if (e.status !== "unchanged" && nodes.has(e.src) && nodes.has(e.dst)) { addWithAncestors(e.src); addWithAncestors(e.dst); }
  });

  function inMode(status) {
    if (state.mode === "before") return status !== "added";
    if (state.mode === "after") return status !== "removed";
    return true;
  }
  function passes(n) {
    return inMode(n.status) && state.statuses.has(n.status) && (state.tests || !n.test) && (!state.focus || interesting.has(n.id));
  }
  function visible(id) {
    var n = nodes.get(id);
    if (!n || !passes(n)) return false;
    return ancestors(id).every(function (a) { return state.expanded.has(a) && passes(nodes.get(a)); });
  }
  function lift(id) {
    if (visible(id)) return id;
    var up = ancestors(id);
    for (var i = 0; i < up.length; i++) if (visible(up[i])) return up[i];
    return null;
  }
  function expandable(id) {
    return (children.get(id) || []).some(function (c) { return passes(nodes.get(c)); });
  }
  function shortName(n) {
    if (n.kind !== "module") return n.name;
    var parts = n.name.split(/[:/]/).filter(Boolean);
    return parts.length > 2 ? "…" + parts.slice(-2).join(":") : n.name;  // full name is in the details panel
  }
  function label(n) {
    return (expandable(n.id) && !state.expanded.has(n.id) ? "▸ " : "") + shortName(n);
  }
  function countVisible() {
    var c = 0;
    DATA.nodes.forEach(function (n) { if (visible(n.id)) c++; });
    return c;
  }

  // Big module graphs: group modules by their top-level folder (android/, ios/, shared/, ...).
  // Purely visual boxes, never collapsed; folders holding a single module get no box.
  var AREA_MIN_MODULES = 8;
  function moduleAreas(shown) {
    var mods = [], count = new Map(), areaOf = new Map();
    shown.forEach(function (id) {
      var n = nodes.get(id);
      if (n.kind !== "module" || !n.path) return;
      var area = n.path.split("/")[0];
      mods.push([id, area]);
      count.set(area, (count.get(area) || 0) + 1);
    });
    if (mods.length < AREA_MIN_MODULES || count.size < 2) return areaOf;
    mods.forEach(function (m) { if (count.get(m[1]) > 1) areaOf.set(m[0], m[1]); });
    return areaOf;
  }
  // An unchanged module dependency A -> C is not drawn when another drawn path A -> ... -> C exists.
  function hideImpliedDependencies(agg) {
    var adj = new Map();
    agg.forEach(function (a) { if (!adj.has(a.s)) adj.set(a.s, []); adj.get(a.s).push(a.d); });
    // checked one by one against the lines still drawn, so two lines can never hide each other
    Array.from(agg.keys()).forEach(function (key) {
      var a = agg.get(key);
      if (a.status !== "unchanged" || nodes.get(a.s).kind !== "module" || nodes.get(a.d).kind !== "module") return;
      if (!a.ids.every(function (i) { return edgesById.get(i).kind === "depends_on"; })) return;
      var stack = adj.get(a.s).filter(function (x) { return x !== a.d; }), seen = new Set();
      while (stack.length) {
        var x = stack.pop();
        if (x === a.d) {
          adj.set(a.s, adj.get(a.s).filter(function (y) { return y !== a.d; }));
          agg.delete(key);
          return;
        }
        if (seen.has(x)) continue;
        seen.add(x);
        (adj.get(x) || []).forEach(function (y) { stack.push(y); });
      }
    });
  }

  // One drawn line can bundle several connections. It is "added" only when all of them are new (a new dependency),
  // "removed" only when all of them are gone; a line that existed before and gains or loses connections is "modified".
  function lineStatus(statuses) {
    var all = Array.from(statuses);
    if (all.every(function (s) { return s === "added"; })) return "added";
    if (all.every(function (s) { return s === "removed"; })) return "removed";
    if (statuses.has("added") || statuses.has("removed")) return "modified";
    return all.reduce(function (best, s) { return RANK[s] > RANK[best] ? s : best; }, "unchanged");
  }

  function elements() {
    var els = [], shown = new Set();
    DATA.nodes.forEach(function (n) { if (visible(n.id)) shown.add(n.id); });
    var areaOf = moduleAreas(shown);
    new Set(areaOf.values()).forEach(function (area) {
      els.push({ group: "nodes", data: { id: "area:" + area, label: area, w: 50 }, pannable: true, classes: "area" });
    });
    shown.forEach(function (id) {
      var n = nodes.get(id), text = label(n);
      var parent = n.parent && shown.has(n.parent) ? n.parent : areaOf.has(id) ? "area:" + areaOf.get(id) : undefined;
      els.push({
        group: "nodes",
        data: { id: id, parent: parent, label: text, w: boxWidth(text) }, pannable: true,
        classes: [n.status, n.kind, state.highlight.has(id) ? "hl" : ""].join(" ")
      });
    });
    var agg = new Map();
    DATA.edges.forEach(function (e) {
      if (!inMode(e.status)) return;
      var s = lift(e.src), d = lift(e.dst);
      if (!s || !d || s === d) return;
      if (ancestors(d).indexOf(s) >= 0 || ancestors(s).indexOf(d) >= 0) return;
      // between collapsed modules, unchanged code-level edges are noise: module deps carry the structure
      if (e.status === "unchanged" && e.kind !== "depends_on" && nodes.get(s).kind === "module" && nodes.get(d).kind === "module") return;
      var key = s + "→" + d, a = agg.get(key);
      if (!a) {
        agg.set(key, { s: s, d: d, statuses: new Set([e.status]), heuristic: e.confidence === "heuristic", ids: [e.id] });
      } else {
        a.ids.push(e.id);
        a.statuses.add(e.status);
        a.heuristic = a.heuristic && e.confidence === "heuristic";
      }
    });
    agg.forEach(function (a) { a.status = lineStatus(a.statuses); });
    hideImpliedDependencies(agg);
    agg.forEach(function (a, key) {
      var red = a.ids.some(function (i) { return RED_TARGETS.has(i); });
      var hl = a.ids.some(function (i) { return state.highlight.has(i); });
      els.push({
        group: "edges",
        data: { id: "e:" + key, source: a.s, target: a.d, count: a.ids.length, ids: a.ids },
        classes: [a.status, a.heuristic ? "heuristic" : "", red ? "violation" : "", hl ? "hl" : ""].join(" ")
      });
    });
    return els;
  }

  function style() {
    var st = function (s) { return css("--st-" + s); };
    var rules = [
      { selector: "node", style: { label: "data(label)", "font-size": NODE_FONT_SIZE, "font-weight": 400, "font-family": css("--sans") || "sans-serif",
          color: css("--fg"), "text-valign": "center", "text-halign": "center", "background-color": css("--node-bg"),
          "border-width": 1, "border-color": st("unchanged"), shape: "round-rectangle", "corner-radius": 7, width: "data(w)", height: NODE_H,
          "min-zoomed-font-size": 5 } },
      { selector: "node:parent", style: { "text-valign": "top", "text-halign": "center", "text-margin-y": -5, "background-opacity": 0.12,
          padding: 14, "font-size": 11, "font-weight": 600, color: css("--muted"), "corner-radius": 10 } },
      { selector: "node.area", style: { "text-valign": "top", "text-halign": "center", "text-margin-y": -6, "font-size": 12, "font-weight": 600,
          color: css("--muted"), "border-style": "dashed", "border-dash-pattern": [4, 4], "border-width": 1, "border-color": css("--line"),
          "background-opacity": 0, padding: 20, "corner-radius": 12 } },
      { selector: "node.impacted", style: { "border-style": "dashed", "border-dash-pattern": [4, 3] } },
      { selector: "node.hl", style: { "underlay-color": css("--accent"), "underlay-opacity": 0.25, "underlay-padding": 5, "underlay-shape": "round-rectangle" } },
      { selector: "node:selected", style: { "border-width": 2, "border-color": css("--accent"), "underlay-color": css("--accent"),
          "underlay-opacity": 0.18, "underlay-padding": 4, "underlay-shape": "round-rectangle" } },
      { selector: "edge", style: { "curve-style": "bezier", "target-arrow-shape": "triangle", "arrow-scale": 0.7, width: 1,
          "line-color": st("unchanged"), "target-arrow-color": st("unchanged"), opacity: 0.5 } },
      { selector: "edge.added", style: { "line-color": st("added"), "target-arrow-color": st("added"), width: 1.8, opacity: 0.95 } },
      { selector: "edge.modified", style: { "line-color": st("modified"), "target-arrow-color": st("modified"), width: 1.6, opacity: 0.95 } },
      { selector: "edge.removed", style: { "line-color": st("removed"), "target-arrow-color": st("removed"), "line-style": "dotted", width: 1.6, opacity: 0.95 } },
      { selector: "edge.heuristic", style: { "line-style": "dashed", "line-dash-pattern": [5, 4] } },
      { selector: "edge.violation", style: { "line-color": css("--danger"), "target-arrow-color": css("--danger"), width: 2.4, opacity: 1 } },
      { selector: "edge.hl", style: { width: 3, opacity: 1 } },
      { selector: "node.faded", style: { opacity: 0.22 } },
      { selector: "edge.faded", style: { opacity: 0.06 } },
      { selector: "edge.conn", style: { opacity: 1, width: 2.4, "arrow-scale": 0.9, "z-index": 10 } },
      { selector: "edge:selected", style: { width: 2.6, opacity: 1, "line-color": css("--accent"), "target-arrow-color": css("--accent") } }
    ];
    STATUSES.forEach(function (s) {
      rules.push({ selector: "node." + s, style: { "border-color": st(s), "background-color": css("--st-" + s + "-bg") } });
    });
    return rules;
  }

  function render(fit) {
    var els = elements();
    if (!cy) {
      cy = cytoscape({ container: document.getElementById("cy"), elements: els, style: style(), minZoom: 0.05, maxZoom: 3,
        boxSelectionEnabled: false });
      dragBoxes(document.getElementById("cy"));
      cy.on("tap", "node", function (ev) { select(ev.target.id()); });
      cy.on("tap", "edge", function (ev) { selectEdge(ev.target); });
      cy.on("dbltap", "node", function (ev) { toggle(ev.target.id()); });
      cy.on("tap", function (ev) { if (ev.target === cy) select(null); });
    } else {
      cy.elements().remove();
      cy.add(els);
    }
    layoutNested();
    focusConnections(state.selected);
    if (fit !== false) fitView();
    if (fit !== false && cy.zoom() < MIN_START_ZOOM) {  // big graphs: start readable, centred on the change
      var focus = cy.nodes(".removed, .added, .modified, .moved");
      cy.zoom(MIN_START_ZOOM);
      cy.center(focus.length ? focus : cy.nodes());
    }
  }

  // A plain drag pans the view, also on a box (boxes can fill the whole view). Shift+drag moves the box through
  // cytoscape's own grab; the middle button moves it here, because cytoscape ignores that button.
  function dragBoxes(container) {
    container.addEventListener("mousedown", function (ev) {  // capture: runs before cytoscape sees the press
      cy.nodes().forEach(function (n) { if (ev.shiftKey) n.unpanify(); else n.panify(); });
      if (ev.button !== 1) return;
      ev.preventDefault(); ev.stopPropagation();  // no autoscroll, no cytoscape handling
      var box = boxAt(container, ev);
      if (!box) return;
      var movers = box.isParent() ? box.descendants().filter(function (n) { return !n.isParent(); }) : box;
      var last = { x: ev.clientX, y: ev.clientY };
      var move = function (e) {
        movers.shift({ x: (e.clientX - last.x) / cy.zoom(), y: (e.clientY - last.y) / cy.zoom() });
        last = { x: e.clientX, y: e.clientY };
      };
      var up = function () { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); };
      window.addEventListener("mousemove", move);
      window.addEventListener("mouseup", up);
    }, true);
  }
  function boxAt(container, ev) {  // the innermost box under the pointer
    var r = container.getBoundingClientRect(), pan = cy.pan(), z = cy.zoom();
    var x = (ev.clientX - r.left - pan.x) / z, y = (ev.clientY - r.top - pan.y) / z, best = null;
    cy.nodes().forEach(function (n) {
      var bb = n.boundingBox({ includeLabels: false });
      if (x < bb.x1 || x > bb.x2 || y < bb.y1 || y > bb.y2) return;
      if (!best || n.ancestors().length > best.ancestors().length) best = n;
    });
    return best;
  }

  // Layout of nested boxes, inside out: every box's direct children are placed with a flat dagre layout (lines lifted
  // to those children), then the box is a fixed-size block one level up. dagre's own compound mode is never used:
  // on some graphs it throws halfway and leaves every box on one spot.
  var LEAF_H = NODE_H, LABEL_H = 20;
  function padOf(n) { return n.hasClass("area") ? 20 : 14; }
  function flatLayout(els) {
    var pos = new Map(), h = cytoscape({ headless: true, styleEnabled: true, elements: els,
      style: [{ selector: "node", style: { width: "data(w)", height: "data(h)", shape: "rectangle" } }] });
    try {
      h.layout({ name: "dagre", rankDir: "LR", nodeSep: 16, rankSep: 70, animate: false, fit: false }).run();
      h.nodes().forEach(function (n) { pos.set(n.id(), { x: n.position("x"), y: n.position("y") }); });
    } catch (err) {  // rows of blocks: plain but never overlapping
      var x = 0, y = 0, rowH = 0, rowW = Math.sqrt(h.nodes().reduce(function (t, n) { return t + n.data("w") * n.data("h"); }, 0)) * 1.6;
      h.nodes().forEach(function (n) {
        if (x > 0 && x + n.data("w") > rowW) { x = 0; y += rowH + 16; rowH = 0; }
        pos.set(n.id(), { x: x + n.data("w") / 2, y: y + n.data("h") / 2 });
        x += n.data("w") + 16; rowH = Math.max(rowH, n.data("h"));
      });
    }
    h.destroy();
    return pos;
  }
  function arrange(kids) {
    var sizes = new Map(), inner = new Map(), ids = new Set(), els = [];
    kids.forEach(function (k) {
      ids.add(k.id());
      if (k.isParent()) {
        var r = arrange(k.children());
        inner.set(k.id(), r);
        sizes.set(k.id(), { w: Math.max(r.w, k.data("w")) + 2 * padOf(k), h: r.h + 2 * padOf(k) + LABEL_H });
      } else sizes.set(k.id(), { w: k.data("w") || 50, h: LEAF_H });
      els.push({ group: "nodes", data: { id: k.id(), w: sizes.get(k.id()).w, h: sizes.get(k.id()).h } });
    });
    var lift = function (n) { while (n.nonempty() && !ids.has(n.id())) n = n.parent(); return n.nonempty() ? n.id() : null; };
    var seen = new Set();
    cy.edges().forEach(function (e) {
      var a = lift(e.source()), b = lift(e.target());
      if (!a || !b || a === b || seen.has(a + ">" + b)) return;
      seen.add(a + ">" + b);
      els.push({ group: "edges", data: { id: "l" + seen.size, source: a, target: b } });
    });
    var pos = flatLayout(els), x1 = Infinity, y1 = Infinity, x2 = -Infinity, y2 = -Infinity;
    pos.forEach(function (p, id) {
      var s = sizes.get(id);
      x1 = Math.min(x1, p.x - s.w / 2); y1 = Math.min(y1, p.y - s.h / 2);
      x2 = Math.max(x2, p.x + s.w / 2); y2 = Math.max(y2, p.y + s.h / 2);
    });
    pos.forEach(function (p) { p.x -= x1; p.y -= y1; });
    return { pos: pos, sizes: sizes, inner: inner, w: x2 - x1, h: y2 - y1 };
  }
  function place(r, ox, oy) {
    r.pos.forEach(function (p, id) {
      var x = ox + p.x, y = oy + p.y, sub = r.inner.get(id);
      if (!sub) { cy.getElementById(id).position({ x: x, y: y }); return; }
      var s = r.sizes.get(id), pad = padOf(cy.getElementById(id));
      place(sub, x - s.w / 2 + pad + (s.w - 2 * pad - sub.w) / 2, y - s.h / 2 + LABEL_H + pad);
    });
  }
  function layoutNested() {
    var r = arrange(cy.nodes().orphans());
    cy.batch(function () { place(r, 0, 0); });
  }

  function fitView() {  // small graphs: readable size, not blown up to the maximum zoom
    cy.fit(undefined, 20);
    if (cy.zoom() > MAX_REVEAL_ZOOM) { cy.zoom(MAX_REVEAL_ZOOM); cy.center(); }
  }

  function toggle(id) {
    if (!expandable(id)) return;
    if (state.expanded.has(id)) collapse(id); else state.expanded.add(id);
    render(false);
  }
  function collapse(id) {
    state.expanded.delete(id);
    (children.get(id) || []).forEach(collapse);
  }
  function setDepth(depth) {
    // Open containers level by level, most-changed first, while the graph stays under MAX_VISIBLE boxes;
    // what does not fit stays collapsed (double-click opens it) instead of the whole view falling back.
    state.expanded.clear();
    var level = function (id) { return ancestors(id).length; };
    var weight = function (n) { return CHANGED[n.status] ? 2 : n.status === "impacted" ? 1 : 0; };
    var shown = function (c) { return passes(nodes.get(c)); };
    var order = DATA.nodes.filter(function (n) {
      return children.has(n.id) && depth !== "module" && (n.kind === "module" || depth === "symbol");
    }).sort(function (a, b) {
      return level(a.id) - level(b.id) || weight(b) - weight(a) ||
        children.get(b.id).filter(shown).length - children.get(a.id).filter(shown).length || (a.id < b.id ? -1 : 1);
    });
    var budget = MAX_VISIBLE - countVisible(), skipped = 0;
    order.forEach(function (n) {
      if (!visible(n.id)) return;
      var cost = children.get(n.id).filter(shown).length;
      if (!cost) return;
      if (cost <= budget) { state.expanded.add(n.id); budget -= cost; } else skipped++;
    });
    document.getElementById("hint").textContent = skipped
      ? "Large change: opened the most-changed parts first; " + skipped + " more box(es) can be opened with a double-click."
      : HINT;
    document.querySelectorAll("[data-depth]").forEach(function (b) { b.classList.toggle("on", b.dataset.depth === depth); });
    render(true);
  }

  function reveal(ids) {
    var targets = ids.filter(function (id) { return nodes.has(id); });
    targets.forEach(function (id) {
      var n = nodes.get(id);
      if (!state.statuses.has(n.status)) state.statuses.add(n.status);
      if (state.focus && !interesting.has(id)) { state.focus = false; document.getElementById("focus").checked = false; }
      if (n.test && !state.tests) { state.tests = true; document.getElementById("tests").checked = true; }
      if (!inMode(n.status)) setMode("merged", false);
      ancestors(id).forEach(function (a) { state.expanded.add(a); });
    });
    syncStatusFilters();
    render(false);
    var lifted = targets.map(lift).filter(Boolean);
    var sel = cy.collection();
    lifted.forEach(function (id) { sel = sel.union(cy.getElementById(id)); });
    if (!sel.length) return;
    var bb = sel.boundingBox(), zoom = Math.min(cy.width() / (bb.w + 120), cy.height() / (bb.h + 120), MAX_REVEAL_ZOOM);
    cy.animate({ zoom: Math.max(zoom, cy.minZoom()), center: { eles: sel } }, { duration: 250 });
  }

  function linkList(edges, pick) {
    if (!edges.length) return '<p class="muted">none</p>';
    var seen = new Set(), items = [];
    edges.forEach(function (e) {
      var other = pick(e);
      if (seen.has(other) || !nodes.has(other)) return;
      seen.add(other);
      var n = nodes.get(other);
      items.push('<li><a href="#" data-node="' + esc(other) + '">' + esc(n.kind === "file" ? n.path : n.name) + "</a> <span class=\"muted\">" + esc(KIND_LABEL[e.kind] || e.kind) + (e.status !== "unchanged" ? " · " + esc(e.status) : "") + "</span></li>");
    });
    return '<ul class="links">' + items.slice(0, 40).join("") + (items.length > 40 ? "<li class=\"muted\">… " + (items.length - 40) + " more</li>" : "") + "</ul>";
  }
  function badge(status) {
    var s = STATUSES.indexOf(status) >= 0 ? status : "unchanged";
    return '<span class="badge" style="border-color:var(--st-' + s + ');background:var(--st-' + s + '-bg)">' + esc(status) + "</span>";
  }
  function nodeLink(id) {
    var n = nodes.get(id);
    return n ? '<a href="#" data-node="' + esc(id) + '">' + esc(n.kind === "file" ? n.path : n.name) + "</a>" : esc(id);
  }
  // Clicking a box lights up all its lines and the boxes at their other ends; everything else steps back.
  // Boxes holding other boxes are never faded (that would fade their contents too).
  function spotlight(lines, keep) {
    cy.elements().removeClass("faded conn");
    if (!lines) return;
    lines.addClass("conn");
    cy.edges().not(lines).addClass("faded");
    cy.nodes().filter(function (x) { return !x.isParent() && !keep.has(x); }).addClass("faded");
  }
  function focusConnections(id) {
    if (!cy) return;
    var n = id ? cy.getElementById(id) : null;
    if (!n || n.empty()) { spotlight(null); return; }
    var own = n.union(n.descendants()), lines = own.connectedEdges();
    spotlight(lines, own.union(lines.connectedNodes()));
  }
  function selectEdge(edge) {
    state.selected = null;
    spotlight(edge, edge.connectedNodes());
    var box = document.getElementById("details"), ids = edge.data("ids") || [];
    var list = ids.map(function (i) { return edgesById.get(i); }).filter(Boolean);
    var kinds = new Map();
    list.forEach(function (e) {
      var k = kinds.get(e.kind) || new Map();
      k.set(e.status, (k.get(e.status) || 0) + 1);
      kinds.set(e.kind, k);
    });
    var html = "<h2>connection</h2><p>" + nodeLink(edge.data("source")) + " → " + nodeLink(edge.data("target")) + "</p>";
    html += '<p class="muted">' + list.length + " underlying connection(s)</p><dl class=\"meta\">";
    kinds.forEach(function (byStatus, kind) {
      var parts = [];
      byStatus.forEach(function (n, s) { parts.push(n + " " + s); });
      html += "<dt>" + esc(KIND_LABEL[kind] || kind) + "</dt><dd>" + esc(parts.join(", ")) + "</dd>";
    });
    html += "</dl><h2>Connections</h2><ul class=\"links\">" + list.slice(0, 40).map(function (e) {
      return "<li>" + nodeLink(e.src) + " <span class=\"muted\">" + esc(KIND_LABEL[e.kind] || e.kind) + "</span> " + nodeLink(e.dst) +
        ' <span class="muted">' + esc(e.status) + (e.confidence === "heuristic" ? " · by name only" : "") + "</span></li>";
    }).join("") + (list.length > 40 ? '<li class="muted">… ' + (list.length - 40) + " more</li>" : "") + "</ul>";
    box.innerHTML = html;
  }
  function select(id) {
    state.selected = id;
    focusConnections(id);
    var box = document.getElementById("details");
    if (!id || !nodes.has(id)) { box.innerHTML = '<p class="muted">Select a node or a finding.</p>'; return; }
    var n = nodes.get(id);
    var html = "<h2>" + esc(n.kind) + "</h2><p><strong>" + esc(n.name) + "</strong> " + badge(n.status) + "</p>";
    if (expandable(id)) html += '<p><button type="button" data-toggle="' + esc(id) + '">' + (state.expanded.has(id) ? "Collapse" : "Expand") + "</button></p>";
    html += "<dl class=\"meta\">";
    if (n.path) html += "<dt>path</dt><dd><code>" + esc(n.path) + "</code></dd>";
    if (n.from_path) html += "<dt>moved from</dt><dd><code>" + esc(n.from_path) + "</code></dd>";
    if (n.module) html += "<dt>module</dt><dd>" + esc(n.module) + "</dd>";
    if (n.lang) html += "<dt>language</dt><dd>" + esc(n.lang) + "</dd>";
    if (n.kind !== "module") html += "<dt>visibility</dt><dd>" + esc(n.visibility) + "</dd>";
    if (n.loc_before != null || n.loc_after != null) html += "<dt>lines</dt><dd>" + esc(n.loc_before == null ? "–" : n.loc_before) + " → " + esc(n.loc_after == null ? "–" : n.loc_after) + "</dd>";
    if (n.kind === "function") html += "<dt>complexity</dt><dd>" + esc(n.complexity_before == null ? "–" : n.complexity_before) + " → " + esc(n.complexity_after == null ? "–" : n.complexity_after) + "</dd>";
    var inc = incoming.get(id) || [], out = outgoing.get(id) || [];
    html += "<dt>used by</dt><dd>" + inc.length + "</dd><dt>uses</dt><dd>" + out.length + "</dd></dl>";
    if (n.signature_before && n.signature_before !== n.signature_after) html += '<code class="sig before">' + esc(n.signature_before) + "</code>";
    if (n.signature_after) html += '<code class="sig' + (n.signature_before && n.signature_before !== n.signature_after ? " after" : "") + '">' + esc(n.signature_after) + "</code>";
    else if (n.signature_before && n.signature_before === n.signature_after) html += '<code class="sig">' + esc(n.signature_before) + "</code>";
    var related = DATA.findings.filter(function (f) { return f.targets.indexOf(id) >= 0; });
    if (related.length) {
      html += "<h2>Findings</h2><ul class=\"links\">" + related.map(function (f) {
        return '<li><a href="#" data-finding="' + esc(f.id) + '">' + ICON[f.severity] + " " + esc(f.title) + "</a></li>";
      }).join("") + "</ul>";
    }
    var owners = n.path && DATA.owners ? DATA.owners[n.path] : null;
    if (owners && owners.length) html += "<h2>Owners</h2><p>" + owners.map(esc).join(", ") + "</p>";
    html += "<h2>Used by</h2>" + linkList(inc, function (e) { return e.src; });
    html += "<h2>Uses</h2>" + linkList(out, function (e) { return e.dst; });
    box.innerHTML = html;
  }
  function showFinding(fid) {
    var f = findingsById.get(fid);
    if (!f) return;
    document.querySelectorAll(".finding").forEach(function (b) { b.classList.toggle("on", b.dataset.finding === fid); });
    state.highlight = new Set(f.targets);
    reveal(f.targets);
    var box = document.getElementById("details");
    box.innerHTML = '<h2 class="sev ' + f.severity + '"><span class="dot"></span>' + esc(ruleName(f.rule)) + "</h2><p><strong>" + esc(f.title) + "</strong></p>" +
      (f.detail ? '<pre class="detail">' + esc(f.detail) + "</pre>" : "") +
      "<h2>Involves</h2><ul class=\"links\">" + f.targets.filter(function (t) { return nodes.has(t); }).slice(0, 60).map(function (t) {
        var n = nodes.get(t);
        return '<li><a href="#" data-node="' + esc(t) + '">' + esc(n.kind === "file" ? n.path : n.name) + "</a> " + badge(n.status) + "</li>";
      }).join("") + "</ul>";
  }

  // The findings column is a compact index: long module names and paths are cut to their last parts, long lists
  // to their first items. The full title is in the tooltip and in the details panel.
  var RULE_NAME = { "api-change": "API change", "new-cycle": "New cycle", "rule-violation": "Rule violation",
    "module-dep-added": "New module dependency", "module-dep-removed": "Removed module dependency",
    "external-dep-added": "New external dependency", "coupling-increase": "Coupling increase", "test-gap": "Test gap",
    "blast-radius": "Blast radius", "dead-on-arrival": "Dead on arrival", "contract-surface": "Contract surface",
    "analysis-config": "Analysis config" };
  var MAX_LIST_ITEMS = 3;
  function ruleName(rule) {
    return RULE_NAME[rule] || rule.charAt(0).toUpperCase() + rule.slice(1).replace(/-/g, " ");
  }
  function compactNames(text) {
    return text
      .replace(/(^|[\s(])((?:[\w.-]+\/){2,}([\w.-]+))/g, "$1$3")  // a/b/c/File.kt -> File.kt
      .replace(/(^|[\s(])(:[\w.-]+(?::[\w.-]+)*|[\w.-]+(?::[\w.-]+)+)/g, function (m, pre, name) {  // :a:b:c:d -> c:d, :a -> a
        return pre + name.split(":").filter(Boolean).slice(-2).join(":");
      });
  }
  function compactList(text) {
    var items = text.split(", ");
    return items.length <= MAX_LIST_ITEMS ? text : items.slice(0, MAX_LIST_ITEMS).join(", ") + " +" + (items.length - MAX_LIST_ITEMS) + " more";
  }
  function findingParts(f) {
    var title = f.title, note = "", m = title.match(/^(.*?)\s*\(([^()]*)\)$/);
    if (m) { title = m[1]; note = m[2]; }
    var cut = title.indexOf(": "), dep = title.match(/^(\w+ dependency) (.*→.*)$/);
    var head = cut > 0 ? title.slice(0, cut) : dep ? dep[1] : title;
    var rest = cut > 0 ? title.slice(cut + 2) : dep ? dep[2] : "";
    // "Public API removed: Foo" reads best as a small "Public API removed" over a prominent "Foo"
    return rest ? { meta: compactNames(head), main: compactList(compactNames(rest)), note: compactNames(note) }
                : { meta: ruleName(f.rule), main: compactNames(head), note: compactNames(note) };
  }
  function renderFindings() {
    var box = document.getElementById("findings");
    var html = "<h2>Findings</h2>";
    if (!DATA.findings.length) html += '<p class="muted">No findings.</p>';
    ["red", "yellow", "info"].forEach(function (sev) {
      var list = DATA.findings.filter(function (f) { return f.severity === sev; });
      if (!list.length) return;
      html += '<h3 class="sev ' + sev + '"><span class="dot"></span>' + LABEL[sev] + '<span class="count">' + list.length + "</span></h3><ul>" +
        list.map(function (f) {
          var p = findingParts(f);
          return '<li><button type="button" class="finding" data-finding="' + esc(f.id) + '" title="' + esc(f.title) + '">' +
            '<span class="rule">' + esc(p.meta) + '</span><span class="head">' + esc(p.main) + "</span>" +
            (p.note ? '<span class="note">' + esc(p.note) + "</span>" : "") + "</button></li>";
        }).join("") + "</ul>";
    });
    box.innerHTML = html;
  }

  function counts(bucket) {
    var parts = [];
    ["added", "modified", "removed", "moved", "impacted"].forEach(function (s) { if (bucket && bucket[s]) parts.push(bucket[s] + " " + s); });
    return parts.join(", ") || "no changes";
  }
  function pm(l) { return l && (l.added || l.deleted) ? "+" + l.added + " −" + l.deleted : "—"; }
  function verdictHtml(ov) {
    return (ov.size_label ? '<span class="size" title="Review size: S, M, L or XL">Size ' + esc(ov.size_label) + "</span> · " : "") + esc(ov.verdict);
  }
  function overviewHtml(ov) {
    var size = ov.size, lines = ["code " + pm(size.code)];
    lines.push(size.tests.added || size.tests.deleted ? "tests " + pm(size.tests) + " (" + size.test_share + " % of added lines)" : "no test changes");
    if (size.other.added || size.other.deleted) lines.push("other " + pm(size.other));
    if (size.binary_files) lines.push(size.binary_files + " binary file(s)");
    var html = '<section class="overview"><h2>Overview</h2><p>' + verdictHtml(ov) + "</p><p><b>Lines</b> " + esc(lines.join(" · ")) + "</p>";
    if (ov.modules.length) {
      html += "<table><tr><th>Module</th><th>Status</th><th>Files</th><th>Symbols</th><th>Code lines</th><th>Test lines</th><th>Findings</th><th>Used by</th></tr>" +
        ov.modules.map(function (r) {
          var s = r.symbols, sym = [["+", s.added], ["−", s.removed], ["~", s.modified], ["↷", s.moved]]
            .filter(function (x) { return x[1]; }).map(function (x) { return x[0] + x[1]; }).join(" ") || "—";
          var sev = (r.findings.red ? "🔴 " + r.findings.red + " " : "") + (r.findings.yellow ? "🟡 " + r.findings.yellow : "") || "—";
          return '<tr><td><a href="#" data-node="' + esc(r.id) + '">' + esc(r.name) + "</a></td><td>" + badge(r.status) + '</td><td class="num">' + r.files +
            '</td><td class="num">' + esc(sym) + '</td><td class="num">' + esc(pm(r.lines)) + '</td><td class="num">' + esc(pm(r.test_lines)) +
            "</td><td>" + sev + '</td><td class="num">' + r.used_by + "</td></tr>";
        }).join("") + "</table>";
    }
    var quiet = ov.other_modules || [];
    if (quiet.length) html += '<p class="muted">Only docs/config changed in ' + quiet.length + " more: " + esc(quiet.slice(0, 5).join(", ") + (quiet.length > 5 ? ", …" : "")) + "</p>";
    if (ov.start_here.length) {
      html += "<h3>Start here</h3><ol>" + ov.start_here.map(function (f) {
        return '<li><a href="#" data-node="file:' + esc(f.path) + '"><code>' + esc(f.path) + "</code></a> — " + esc(f.reasons.join(", ")) + "</li>";
      }).join("") + "</ol>";
    }
    return html + "</section>";
  }
  function renderHeader() {
    var st = DATA.stats, sev = st.findings || {};
    document.getElementById("range").textContent = DATA.range.label;
    document.getElementById("range").title = DATA.range.label;
    document.getElementById("counts").innerHTML = [
      "<b>Modules</b> " + esc(counts(st.modules)), "<b>Files</b> " + esc(counts(st.files)), "<b>Symbols</b> " + esc(counts(st.symbols)),
      "🔴 " + (sev.red || 0) + " 🟡 " + (sev.yellow || 0) + " ⚪ " + (sev.info || 0)
    ].map(function (g) { return '<span class="grp">' + g + "</span>"; }).join(" · ");
    if (DATA.overview && DATA.overview.verdict) {
      var v = document.getElementById("verdict");
      v.hidden = false;
      v.innerHTML = verdictHtml(DATA.overview);
    }
    if (DATA.explain) {  // just the verdict here; the review itself is in the story view
      var r = DATA.review || {}, btn = document.getElementById("btn-review");
      btn.hidden = false;
      btn.className = verdictClass(r.verdict);
      btn.innerHTML = '<span aria-hidden="true">✦</span>' + esc(r.verdict || "AI review");
      btn.title = "AI review" + (r.verdict ? ": " + r.verdict + (r.reason ? " — " + r.reason : "") : "") + " · open in the story view";
    }
    if (DATA.warnings && DATA.warnings.length) {
      document.getElementById("warnings").hidden = false;
      document.getElementById("warnings-summary").textContent = DATA.warnings.length + " warning(s)";
      document.getElementById("warnings-list").innerHTML = DATA.warnings.map(function (w) { return "<li>" + esc(w) + "</li>"; }).join("");
    }
  }
  function verdictClass(verdict) { return verdict ? verdict.toLowerCase().replace(" ", "-") : ""; }
  function reviewHeadline() {
    var r = DATA.review;
    if (!r || !r.verdict) return "AI review";
    return 'AI review <span class="rv ' + esc(verdictClass(r.verdict)) + '">' + esc(r.verdict) + "</span>" + (r.reason ? " " + esc(r.reason) : "");
  }
  function openReview() {
    if (!document.body.classList.contains("story")) toggleStory();
    document.getElementById("story-review").scrollIntoView({ block: "start" });
  }
  function reviewBody() {
    var r = DATA.review;
    if (!r || !r.items.length) return '<p class="explain-text">' + esc(DATA.explain) + "</p>";
    return '<dl class="review">' + r.items.map(function (it) { return "<dt>" + esc(it[0]) + "</dt><dd>" + esc(it[1]) + "</dd>"; }).join("") + "</dl>";
  }
  function syncStatusFilters() {
    document.querySelectorAll("#status-filters input").forEach(function (i) { i.checked = state.statuses.has(i.value); });
  }
  function renderStatusFilters() {
    document.getElementById("status-filters").innerHTML = STATUSES.map(function (s) {
      return '<label class="status-filter"><input type="checkbox" value="' + s + '" checked><span class="swatch" style="border-color:var(--st-' + s + ');background:var(--st-' + s + '-bg)"></span>' + s + "</label>";
    }).join("");
  }
  function setMode(mode, rerender) {
    state.mode = mode;
    document.querySelectorAll("[data-mode]").forEach(function (b) { b.classList.toggle("on", b.dataset.mode === mode); });
    if (rerender !== false) render(true);
  }

  // story view: a printable document with small focused graphs
  var MINI_MIN_H = 70, MINI_MAX_H = 260, minis = [];
  function miniGraph(container, ids, level, findingId) {
    var keep = new Set();
    function up(id) {
      var n = nodes.get(id);
      while (n && n.kind !== level && n.parent && nodes.has(n.parent) && !(level === "file" && n.kind === "module")) n = nodes.get(n.parent);
      return n ? n.id : null;
    }
    ids.forEach(function (id) { var u = up(id); if (u) keep.add(u); });
    var els = [];
    keep.forEach(function (id) {
      var n = nodes.get(id), text = shortName(n);
      els.push({ group: "nodes", data: { id: id, label: text, w: boxWidth(text) }, classes: n.status + " " + n.kind });
    });
    var seen = new Set();
    DATA.edges.forEach(function (e) {
      var s = up(e.src), d = up(e.dst);
      if (!s || !d || s === d || !keep.has(s) || !keep.has(d) || seen.has(s + d)) return;
      if (e.status === "unchanged" && e.kind !== "depends_on") return;
      seen.add(s + d);
      els.push({ group: "edges", data: { id: "m:" + s + "→" + d, source: s, target: d }, classes: e.status + (RED_TARGETS.has(e.id) ? " violation" : "") });
    });
    var mini = cytoscape({ container: container, elements: els, style: style(), userZoomingEnabled: false, userPanningEnabled: false, autoungrabify: true,
      maxZoom: 1 });  // small maps keep the graph view's text size instead of blowing up to fill the box
    minis.push(mini);
    try { mini.layout({ name: "dagre", rankDir: "LR", nodeSep: 12, rankSep: 50, padding: 12 }).run(); }
    catch (err) { mini.layout({ name: "breadthfirst", directed: true, padding: 12 }).run(); }
    if (!container.classList.contains("tall")) {  // as tall as the drawing needs, not a fixed empty frame
      container.style.height = Math.min(MINI_MAX_H, Math.max(MINI_MIN_H, mini.elements().boundingBox().h + 40)) + "px";
      mini.resize(); mini.fit(undefined, 12); mini.center();
    }
    // a box opens the graph on that box, empty space opens the graph on the whole finding
    mini.on("tap", function (ev) {
      if (ev.target !== mini && ev.target.isNode()) openNode(ev.target.id());
      else if (findingId) openFinding(findingId);
    });
    mini.on("mouseover", "node", function () { container.style.cursor = "pointer"; });
    mini.on("mouseout", "node", function () { container.style.cursor = ""; });
  }
  function openNode(id) {
    if (document.body.classList.contains("story")) toggleStory();  // the graph is behind the story: show it first
    state.highlight = new Set([id]); reveal([id]); select(id);
  }
  function openFinding(fid) {
    if (document.body.classList.contains("story")) toggleStory();
    showFinding(fid);
  }
  var storyBuilt = false;
  // Theme: follows the system until the button picks one; the choice is remembered per browser.
  var THEME_KEY = "crgraph-theme", darkQuery = window.matchMedia("(prefers-color-scheme: dark)");
  function isDark() {
    var t = document.documentElement.dataset.theme;
    return t ? t === "dark" : darkQuery.matches;
  }
  function applyTheme(theme) {
    if (theme) document.documentElement.dataset.theme = theme; else delete document.documentElement.dataset.theme;
    var btn = document.getElementById("btn-theme"), dark = isDark();
    btn.textContent = dark ? "☀" : "☾";
    btn.title = dark ? "Light theme" : "Dark theme";
    btn.setAttribute("aria-label", btn.title);
    if (cy) cy.style(style());  // graph colours are read from the CSS variables
    if (storyBuilt) { storyBuilt = false; if (document.body.classList.contains("story")) renderStory(); }
  }
  function toggleTheme() {
    var theme = isDark() ? "light" : "dark";
    try { localStorage.setItem(THEME_KEY, theme); } catch (err) { /* private mode: not remembered */ }
    applyTheme(theme);
  }
  function savedTheme() {
    try { var t = localStorage.getItem(THEME_KEY); return t === "light" || t === "dark" ? t : null; } catch (err) { return null; }
  }
  function renderStory() {
    if (storyBuilt) return;
    storyBuilt = true;
    var box = document.getElementById("story"), html = "";
    minis.forEach(function (m) { m.destroy(); });  // rebuilt after a theme switch
    minis = [];
    html += "<section><h1>" + esc(DATA.range.label) + "</h1><p>" + document.getElementById("counts").innerHTML + "</p>" +
      "</section>";
    if (DATA.explain) html += '<section id="story-review"><h2>' + reviewHeadline() + "</h2>" + reviewBody() + "</section>";
    if (DATA.overview && DATA.overview.verdict) html += overviewHtml(DATA.overview);
    html += '<section><h2>Module map</h2><div class="mini tall" id="mini-modules"></div></section>';
    ["red", "yellow", "info"].forEach(function (sev) {
      var list = DATA.findings.filter(function (f) { return f.severity === sev; });
      if (!list.length) return;
      html += '<section><h2 class="sev ' + sev + '"><span class="dot"></span>' + LABEL[sev] + '<span class="count">' + list.length + "</span></h2>";
      list.forEach(function (f) {
        var p = findingParts(f);
        html += '<article class="story-finding"><header title="' + esc(f.title) + '"><span class="rule">' + esc(p.meta) +
          '</span><span class="head">' + esc(p.main) + "</span>" + (p.note ? '<span class="note">' + esc(p.note) + "</span>" : "") +
          '<a href="#" class="open" data-finding="' + esc(f.id) + '">Show in graph →</a></header>' +
          (f.detail ? '<pre class="detail">' + esc(f.detail) + "</pre>" : "");
        if (f.targets.length && f.targets.length <= 40) html += '<div class="mini" data-mini="' + esc(f.id) + '"></div>';
        html += "</article>";
      });
      html += "</section>";
    });
    var api = DATA.nodes.filter(function (n) { return n.signature_before && n.signature_after && n.signature_before !== n.signature_after; });
    if (api.length) {
      html += "<section><h2>Signature changes</h2><table><tr><th>Symbol</th><th>Before</th><th>After</th></tr>" + api.map(function (n) {
        return "<tr><td>" + esc(n.name) + "</td><td><code>" + esc(n.signature_before) + "</code></td><td><code>" + esc(n.signature_after) + "</code></td></tr>";
      }).join("") + "</table></section>";
    }
    var files = DATA.nodes.filter(function (n) { return n.kind === "file" && CHANGED[n.status]; });
    html += "<section><h2>Changed files</h2><table><tr><th>Status</th><th>Path</th><th>Module</th></tr>" + files.map(function (n) {
      return "<tr><td>" + badge(n.status) + "</td><td><code>" + esc(n.path) + "</code>" + (n.from_path ? ' <span class="muted">from ' + esc(n.from_path) + "</span>" : "") + "</td><td>" + esc(n.module) + "</td></tr>";
    }).join("") + "</table></section>";
    box.innerHTML = html;
    miniGraph(document.getElementById("mini-modules"), DATA.nodes.filter(function (n) { return n.kind === "module" && interesting.has(n.id); }).map(function (n) { return n.id; }), "module");
    box.querySelectorAll("[data-mini]").forEach(function (el) { miniGraph(el, findingsById.get(el.dataset.mini).targets, "file", el.dataset.mini); });
  }
  function toggleStory() {
    var on = !document.body.classList.contains("story");
    document.body.classList.toggle("story", on);
    document.getElementById("story").hidden = !on;
    var btn = document.getElementById("btn-story");
    btn.setAttribute("aria-pressed", String(on));
    btn.textContent = on ? "Graph view" : "Story view";
    if (on) renderStory(); else if (cy) { cy.resize(); fitView(); }
  }

  document.addEventListener("click", function (ev) {
    var t = ev.target.closest("[data-finding],[data-node],[data-mode],[data-depth],[data-toggle]");
    if (!t) return;
    if (t.dataset.toggle) { toggle(t.dataset.toggle); select(t.dataset.toggle); }
    else if (t.dataset.finding) { ev.preventDefault(); openFinding(t.dataset.finding); }
    else if (t.dataset.node) { ev.preventDefault(); openNode(t.dataset.node); }
    else if (t.dataset.mode) setMode(t.dataset.mode);
    else if (t.dataset.depth) setDepth(t.dataset.depth);
  });
  document.getElementById("btn-story").addEventListener("click", toggleStory);
  document.getElementById("btn-theme").addEventListener("click", toggleTheme);
  document.getElementById("btn-review").addEventListener("click", openReview);
  // the legend sits just above the hint line, which wraps to two lines on narrower windows
  new ResizeObserver(function () {
    document.getElementById("legend").style.bottom = document.getElementById("hint").offsetHeight + "px";
  }).observe(document.getElementById("hint"));
  darkQuery.addEventListener("change", function () { if (!document.documentElement.dataset.theme) applyTheme(null); });
  document.getElementById("focus").addEventListener("change", function (ev) { state.focus = ev.target.checked; render(true); });
  document.getElementById("tests").addEventListener("change", function (ev) { state.tests = ev.target.checked; render(false); });
  document.getElementById("status-filters").addEventListener("change", function (ev) {
    if (ev.target.checked) state.statuses.add(ev.target.value); else state.statuses.delete(ev.target.value);
    render(false);
  });
  var searchTimer = null;
  document.getElementById("search").addEventListener("input", function (ev) {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(function () {
      var q = ev.target.value.trim().toLowerCase();
      if (!q) { state.highlight = new Set(); render(false); return; }
      var hits = DATA.nodes.filter(function (n) { return n.name.toLowerCase().indexOf(q) >= 0; }).slice(0, 50).map(function (n) { return n.id; });
      state.highlight = new Set(hits);
      reveal(hits);
    }, 250);
  });

  applyTheme(savedTheme());
  renderHeader();
  renderFindings();
  renderStatusFilters();
  document.getElementById("tests-filter").hidden = !DATA.nodes.some(function (n) { return n.test; });
  setMode("merged", false);
  setDepth((DATA.ui && DATA.ui.depth) || "module");
  window.__crgraph = { state: state, cy: function () { return cy; }, showFinding: showFinding, toggleTheme: toggleTheme };
})();
