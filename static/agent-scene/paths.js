// Граф точек: агенты ходят только по рёбрам, поэтому не проходят сквозь стены и мебель.
export class Graph {
  constructor(nodes, edges) {
    this.nodes = nodes; // { name: [x, z] }
    this.links = new Map(Object.keys(nodes).map((name) => [name, []]));
    edges.forEach(([a, b]) => {
      this.links.get(a).push(b);
      this.links.get(b).push(a);
    });
  }

  point(name) {
    const [x, z] = this.nodes[name];
    return { x, z };
  }

  distance(a, b) {
    const [ax, az] = this.nodes[a];
    const [bx, bz] = this.nodes[b];
    return Math.hypot(ax - bx, az - bz);
  }

  nearest(x, z) {
    let best = null;
    let bestDistance = Infinity;
    for (const [name, [nx, nz]] of Object.entries(this.nodes)) {
      const d = Math.hypot(nx - x, nz - z);
      if (d < bestDistance) { best = name; bestDistance = d; }
    }
    return best;
  }

  // Дейкстра по небольшому графу.
  path(from, to) {
    if (from === to) return [to];
    const dist = new Map([[from, 0]]);
    const prev = new Map();
    const open = new Set([from]);
    while (open.size) {
      let current = null;
      for (const name of open) if (current === null || dist.get(name) < dist.get(current)) current = name;
      open.delete(current);
      if (current === to) break;
      for (const next of this.links.get(current)) {
        const d = dist.get(current) + this.distance(current, next);
        if (d < (dist.get(next) ?? Infinity)) {
          dist.set(next, d);
          prev.set(next, current);
          open.add(next);
        }
      }
    }
    if (!prev.has(to)) return [to];
    const route = [to];
    while (route[0] !== from) route.unshift(prev.get(route[0]));
    return route;
  }
}
