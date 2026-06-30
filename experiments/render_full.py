"""Render the FULL card synergy graph (all ~34k cards) to a PNG — igraph layout, matplotlib draw.

card_synergy.build_graph() gives the buff edges; this lays them out for viewing. A plain force layout
collapses the hub-dominated creature supercluster into a blob, so instead:
  * the connected core (~16k creatures) is split into tribal COMMUNITIES (igraph Louvain), each drawn as
    its own non-overlapping sunflower disc, the discs positioned by a community meta-graph FR layout and
    pushed apart so the tribes read as distinct clusters (the left "galaxy");
  * the ~18k isolated cards (no tribal synergy) are parked in a dense grid panel down the right side.
Nodes are coloured by MTG colour identity; edges are red (grant_keyword) / blue (modify_pt).

Needs networkx, igraph, matplotlib. Run: python3 render_full.py  ->  /tmp/all_cards_synergy.png
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (run as script from experiments/)
import time
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
import matplotlib.patches as mpatches
import numpy as np
import networkx as nx
import igraph as ig
import card_synergy as cs

t0 = time.perf_counter()
g = cs.build_graph(kinds=("subtype",))
nodes = list(g.nodes())
idx = {n: i for i, n in enumerate(nodes)}
edges = [(idx[u], idx[v]) for u, v in g.edges()]
print(f"built graph {g.number_of_nodes()} nodes / {g.number_of_edges()} edges  [{time.perf_counter()-t0:.1f}s]")

# --- layout: split the pool into the connected core (laid out by tribal community) and the isolated cards ---
U = g.to_undirected()
conn = [n for comp in nx.connected_components(U) if len(comp) > 1 for n in comp]
cset = set(conn)
cidx = {n: i for i, n in enumerate(conn)}
sub = ig.Graph(n=len(conn))                                # igraph view of the connected subgraph (for community + layout)
sub.add_edges([(cidx[u], cidx[v]) for u, v in g.edges() if u in cset and v in cset])
print(f"laying out connected core ({len(conn)} nodes) by community...")
# force layout collapses this hub-dominated supercluster; instead detect tribal COMMUNITIES (Louvain) and
# lay each out as its own non-overlapping sunflower disc, positioning the communities by their interconnection.
from collections import Counter, defaultdict
comm = sub.community_multilevel()
mem = comm.membership
K = max(mem) + 1
members = defaultdict(list)
for i, m in enumerate(mem):
    members[m].append(i)
sizes = {c: len(v) for c, v in members.items()}
print(f"  {K} communities (modularity {comm.modularity:.3f}); placing as discs...")

inter = Counter()                                          # community meta-graph: inter-community edge weights
for e in sub.es:
    a, b = mem[e.source], mem[e.target]
    if a != b:
        inter[(min(a, b), max(a, b))] += 1
meta = ig.Graph(n=K); ekeys = list(inter.keys())
meta.add_edges(ekeys)
mc = np.array(meta.layout_fruchterman_reingold(weights=[inter[k] for k in ekeys]).coords).astype(float)
mc -= mc.mean(0)
total = len(conn)
R = np.array([0.21 * np.sqrt(sizes[c] / total) for c in range(K)])   # disc radius ∝ sqrt(community size)

# FR pulls the interconnected communities together; push the discs apart so the tribal clusters are distinct
for _ in range(400):
    moved = False
    for i in range(K):
        for j in range(i + 1, K):
            d = mc[j] - mc[i]; dist = float(np.hypot(*d)) or 1e-6
            need = R[i] + R[j] + 0.02
            if dist < need:
                u = d / dist; push = (need - dist) / 2
                mc[i] -= u * push; mc[j] += u * push; moved = True
    if not moved:
        break

GOLD = np.pi * (3 - np.sqrt(5))
lay = np.zeros((len(conn), 2))
for c, idxs in members.items():
    cx, cy = mc[c]
    for k, ni in enumerate(idxs):                         # sunflower fill: even spacing, no node overlap
        r = R[c] * np.sqrt((k + 0.5) / len(idxs))
        th = k * GOLD
        lay[ni] = (cx + r * np.cos(th), cy + r * np.sin(th))
lo, hi = np.percentile(lay, [1, 99], axis=0)              # percentile so a stray community doesn't squish the core
lay = np.clip((lay - lo) / (hi - lo).clip(1e-9), 0, 1)
print(f"  layout done [{time.perf_counter()-t0:.1f}s]")

pos = np.zeros((len(nodes), 2))
singles = [n for n in nodes if n not in cset]
# connected tribal communities: a LARGE square galaxy filling the left of the canvas
gx0, gy0, gside = 0.012, 0.135, 0.76
for n in conn:
    lx, ly = lay[cidx[n]]
    pos[idx[n]] = (gx0 + lx * gside, gy0 + ly * gside)
# isolated cards: parked in a DENSE grid panel down the RIGHT side (dangling nodes, not part of the geometry)
px0, px1, py0, py1 = 0.795, 0.998, 0.02, 0.98
ncols = max(1, int(round(np.sqrt(len(singles) * (px1 - px0) / (py1 - py0)))))
nrows = int(np.ceil(len(singles) / ncols))
for k, n in enumerate(singles):
    r, c = divmod(k, ncols)
    pos[idx[n]] = (px0 + (c + 0.5) / ncols * (px1 - px0), py0 + (r + 0.5) / nrows * (py1 - py0))
print(f"  galaxy {len(conn)} (left {gside:.0%}) + isolated {len(singles)} ({ncols}x{nrows} grid, right) [{time.perf_counter()-t0:.1f}s]")

# --- node colors by MTG color identity (the "mesh of colors") ---
CMAP = {"white": "#e6c94d", "blue": "#3182ce", "black": "#3b3b3b",
        "red": "#e53e3e", "green": "#38a169"}
def ncolor(n):
    cols = g.nodes[n].get("colors", [])
    if not cols:
        return "#b8c2cc"                                   # colorless
    if len(cols) > 1:
        return "#d69e2e"                                   # multicolor -> gold
    return CMAP.get(cols[0], "#b8c2cc")
core_colors = [ncolor(n) for n in conn]

# --- draw: star-field first (faint grey), then core edges (red/blue), then the colored tribal galaxy ---
seg_mod, seg_kw = [], []
for u, v, d in g.edges(data=True):
    seg = [pos[idx[u]], pos[idx[v]]]
    (seg_kw if d["verb"] == "grant_keyword" else seg_mod).append(seg)
print(f"drawing {len(seg_mod)+len(seg_kw)} edges...")

fig, ax = plt.subplots(figsize=(34, 34))
sp = np.array([pos[idx[n]] for n in singles])
ax.scatter(sp[:, 0], sp[:, 1], s=3.0, c="#94a3b8", linewidths=0, alpha=0.85)   # ~18k unconnected cards (dense side panel)
ax.text(0.789, 0.5, f"{len(singles)} cards · no tribal synergy", rotation=90, ha="right", va="center",
        fontsize=15, color="#718096")
ax.add_collection(LineCollection(seg_mod, colors="#3182ce", linewidths=0.15, alpha=0.075))
ax.add_collection(LineCollection(seg_kw,  colors="#e53e3e", linewidths=0.15, alpha=0.075))
cp = np.array([pos[idx[n]] for n in conn])
ax.scatter(cp[:, 0], cp[:, 1], s=7, c=core_colors, linewidths=0, alpha=0.92)   # the tribal galaxy (now larger)
ax.legend(handles=[mpatches.Patch(color="#3182ce", label="modify_pt edge (+X/+X)"),
                   mpatches.Patch(color="#e53e3e", label="grant_keyword edge (ability)"),
                   mpatches.Patch(color="#d69e2e", label="node: multicolor"),
                   mpatches.Patch(color="#cbd5e0", label="isolated card (no tribal synergy)")],
          loc="upper left", fontsize=18, framealpha=0.92)
ax.text(0.5, 0.995, f"Every Magic card as a synergy node — {g.number_of_nodes()} cards, {g.number_of_edges()} tribal buff edges",
        transform=ax.transAxes, ha="center", va="top", fontsize=24, weight="bold")
ax.text(0.5, 0.975, "centre galaxy = ~16k creatures in interconnected tribal communities · "
        "field = ~18k cards with no tribal synergy · node colour = MTG colour identity",
        transform=ax.transAxes, ha="center", va="top", fontsize=15, color="#444")
# fill the whole frame — axes span the figure, data spans [0,1], no wasted margin
ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
ax.set_position([0, 0, 1, 1])
plt.savefig("/tmp/all_cards_synergy.png", dpi=150, pad_inches=0)
print(f"wrote /tmp/all_cards_synergy.png  [{time.perf_counter()-t0:.1f}s total]")
