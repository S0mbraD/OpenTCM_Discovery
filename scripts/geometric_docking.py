#!/usr/bin/env python3
"""Geometry-based protein-protein docking: place a ligand protein onto a receptor
surface such that a known binding epitope faces the receptor.

This reproduces the method used for the S100A8-TLR4/MD-2 proposed model:
literature-constrained geometric search (NOT energy minimization):
  1. Compute receptor interface center (or accept explicit center)
  2. Sample outer surface points near the interface (sphere-direction buckets)
  3. For each surface point: translate ligand so its epitope centroid sits at
     gap A outside the point, epitope normal aligned to surface normal
  4. Rotate around the normal (spins) x several gap values = candidates
  5. Hard filter: all-atom min distance >= 2.5 A (no severe clashes)
  6. Score: mean distance of key residues to receptor + contact-count bonus
  7. Write best placement as a combined multi-chain PDB

Output models are "proposed docking models" - must be labeled as such.

Example:
  python geometric_docking.py --receptor TLR4MD2.pdb --ligand S100A8.pdb \
      --ligand-chain A --epitope 68-77 --key-residues 73,74,75,77 \
      --surface-region 60-89 --ligand-out-chain S --out model.pdb
"""
import argparse
import math
import random


def read_atoms(path, chain_filter=None):
    atoms = []
    with open(path) as f:
        for line in f:
            if line.startswith("ATOM"):
                chain = line[21] or " "
                if chain_filter and chain not in chain_filter:
                    continue
                atoms.append((chain, int(line[22:26]), line[17:20].strip(),
                              line[12:16].strip(), (line[30:54])))
    return atoms


def parse_atoms_full(path, chain_filter=None):
    """Return list of dicts with raw coordinate floats and the full raw line."""
    out = []
    with open(path) as f:
        for line in f:
            if line.startswith("ATOM"):
                chain = line[21] or " "
                if chain_filter and chain not in chain_filter:
                    continue
                out.append({
                    "raw": line,
                    "chain": chain,
                    "resnum": int(line[22:26]),
                    "resname": line[17:20].strip(),
                    "atom": line[12:16].strip(),
                    "xyz": (float(line[30:38]), float(line[38:46]), float(line[46:54])),
                })
    return out


def vsub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def vadd(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def vscale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def vdot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def vnorm(a):
    return math.sqrt(vdot(a, a))


def vunit(a):
    n = vnorm(a)
    return (a[0] / n, a[1] / n, a[2] / n) if n else (0, 0, 0)


def vcross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def rotation_matrix(axis, angle):
    """Rodrigues rotation matrix about unit axis."""
    x, y, z = axis
    c, s = math.cos(angle), math.sin(angle)
    t = 1 - c
    return ((t * x * x + c, t * x * y - s * z, t * x * z + s * y),
            (t * x * y + s * z, t * y * y + c, t * y * z - s * x),
            (t * x * z - s * y, t * y * z + s * x, t * z * z + c))


def matvec(m, v):
    return tuple(m[i][0] * v[0] + m[i][1] * v[1] + m[i][2] * v[2] for i in range(3))


def transform_ligand(atoms, rot, trans):
    """Apply rotation then translation to all atom coords; returns new atom dicts."""
    new = []
    for a in atoms:
        x = matvec(rot, a["xyz"])
        new.append({**a, "xyz": vadd(x, trans)})
    return new


def interface_center(rec_atoms):
    """Centroid of midpoints of inter-chain contact pairs (< 6 A)."""
    chains = sorted(set(a["chain"] for a in rec_atoms))
    if len(chains) < 2:
        return None
    by_chain = {c: [a for a in rec_atoms if a["chain"] == c] for c in chains}
    mids = []
    for i in range(len(chains)):
        for j in range(i + 1, len(chains)):
            for a in by_chain[chains[i]]:
                for b in by_chain[chains[j]]:
                    if math.dist(a["xyz"], b["xyz"]) < 6.0:
                        mids.append(vscale(vadd(a["xyz"], b["xyz"]), 0.5))
    if not mids:
        return None
    c = tuple(sum(m[k] for m in mids) / len(mids) for k in range(3))
    print(f"Interface center from {len(mids)} contact pairs: "
          f"({c[0]:.1f}, {c[1]:.1f}, {c[2]:.1f})")
    return c


def sample_surface_points(rec_atoms, center, radius, n_buckets=54):
    """Outer-most receptor atoms within radius of center, one per direction bucket."""
    pts = []
    for a in rec_atoms:
        d = vsub(a["xyz"], center)
        if vnorm(d) <= radius:
            pts.append(a["xyz"])
    buckets = {}
    for p in pts:
        v = vsub(p, center)
        n = vunit(v)
        # fibonacci sphere direction hash
        key = tuple(round(c * 4) for c in n)  # ~cube-face buckets
        r = vnorm(v)
        if key not in buckets or r > buckets[key][1]:
            buckets[key] = (p, r)
    surface = [b[0] for b in buckets.values()]
    print(f"Sampled {len(surface)} surface points (from {len(pts)} atoms in {radius} A)")
    return surface


def min_dist_group(atoms_a, atoms_b, early_stop=None):
    best = 1e9
    for a in atoms_a:
        for b in atoms_b:
            d = math.dist(a["xyz"], b["xyz"])
            if d < best:
                best = d
                if early_stop and best < early_stop:
                    return best
    return best


class Grid:
    """Uniform 3D cell grid for fast nearest-distance queries.

    Atoms in cells at chebyshev ring r+1 are at least r*cell away, so the search
    can stop once the best distance found is below that bound.
    """

    def __init__(self, atoms, cell=6.0):
        self.cell = cell
        self.cells = {}
        for a in atoms:
            k = (int(a["xyz"][0] // cell), int(a["xyz"][1] // cell),
                 int(a["xyz"][2] // cell))
            self.cells.setdefault(k, []).append(a["xyz"])

    def min_dist(self, xyz, early_stop=None):
        best = 1e9
        k0 = (int(xyz[0] // self.cell), int(xyz[1] // self.cell),
              int(xyz[2] // self.cell))
        for r in range(0, 13):
            if r > 0 and best < r * self.cell:
                break
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    for dz in range(-r, r + 1):
                        if max(abs(dx), abs(dy), abs(dz)) != r:
                            continue  # shell cells only
                        kk = (k0[0] + dx, k0[1] + dy, k0[2] + dz)
                        for p in self.cells.get(kk, ()):
                            d = math.dist(xyz, p)
                            if d < best:
                                best = d
                                if early_stop and best < early_stop:
                                    return best
        return best


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--receptor", required=True, help="cleaned receptor PDB")
    p.add_argument("--ligand", required=True, help="ligand protein PDB (single chain)")
    p.add_argument("--ligand-chain", default=None, help="chain to use from ligand PDB")
    p.add_argument("--epitope", required=True,
                   help="binding epitope residue range, e.g. 68-77 (faces receptor)")
    p.add_argument("--key-residues", required=True,
                   help="comma-separated residues that must stay near receptor, e.g. 73,74,75,77")
    p.add_argument("--surface-region", default=None,
                   help="ligand region expected at the interface, e.g. 60-89 (score bonus)")
    p.add_argument("--center", default=None, help="explicit interface center 'x,y,z'")
    p.add_argument("--search-radius", type=float, default=28.0)
    p.add_argument("--min-dist", type=float, default=2.5, help="hard clash threshold")
    p.add_argument("--spins", type=int, default=12)
    p.add_argument("--gaps", default="1.5,3.0,4.5,6.0,7.5")
    p.add_argument("--ligand-out-chain", default="S", help="chain ID for output ligand")
    p.add_argument("--out", required=True)
    p.add_argument("--report", default=None, help="save JSON report")
    args = p.parse_args()

    rec = parse_atoms_full(args.receptor)
    lig = parse_atoms_full(args.ligand,
                           chain_filter={args.ligand_chain} if args.ligand_chain else None)
    print(f"Receptor: {len(rec)} atoms | Ligand: {len(lig)} atoms")

    e0, e1 = (int(v) for v in args.epitope.split("-"))
    epitope = [a for a in lig if e0 <= a["resnum"] <= e1]
    key = {int(v) for v in args.key_residues.split(",")}
    key_atoms = [a for a in lig if a["resnum"] in key]
    region_atoms = ([a for a in lig if e0 <= a["resnum"] <= e1]
                    if not args.surface_region else
                    [a for a in lig if int(args.surface_region.split("-")[0])
                     <= a["resnum"] <= int(args.surface_region.split("-")[1])])
    epitope_c = tuple(sum(a["xyz"][k] for a in epitope) / len(epitope) for k in range(3))
    ligand_c = tuple(sum(a["xyz"][k] for a in lig) / len(lig) for k in range(3))
    # local epitope normal: from epitope centroid away from ligand body
    ep_normal = vunit(vsub(epitope_c, ligand_c))

    center = None
    if args.center:
        center = tuple(float(v) for v in args.center.split(","))
    else:
        center = interface_center(rec)
        if center is None:
            raise SystemExit("Receptor has a single chain; pass --center explicitly")
    surface_pts = sample_surface_points(rec, center, args.search_radius)
    gaps = [float(g) for g in args.gaps.split(",")]

    # spatial grid for fast clash checks and scoring
    grid = Grid(rec)

    def clash_ok(lig_transformed, threshold):
        # any atom closer than threshold to receptor = clash
        for a in lig_transformed:
            if grid.min_dist(a["xyz"], early_stop=args.min_dist) < args.min_dist:
                return False
        return True

    best = None
    best_score = 1e9
    candidates = 0
    for pt in surface_pts:
        # surface outward normal (approx: from center to surface point)
        n_surf = vunit(vsub(pt, center))
        for gap in gaps:
            for spin_i in range(args.spins):
                # rotation aligning epitope normal to -surface normal
                # (epitope must point INTO the receptor surface)
                axis = vcross(ep_normal, vscale(n_surf, -1))
                if vnorm(axis) < 1e-6:
                    rot = rotation_matrix(n_surf, spin_i * 2 * math.pi / args.spins)
                else:
                    axis = vunit(axis)
                    angle = math.acos(max(-1, min(1, vdot(ep_normal, vscale(n_surf, -1)))))
                    r_align = rotation_matrix(axis, angle)
                    r_spin = rotation_matrix(n_surf, spin_i * 2 * math.pi / args.spins)
                    # combine: first align, then spin about surface normal
                    rot = tuple(tuple(sum(r_spin[i][k] * r_align[k][j] for k in range(3))
                                      for j in range(3)) for i in range(3))
                # translation: transformed epitope centroid at pt + gap * n_surf
                ep_rot = matvec(rot, epitope_c)
                target = vadd(pt, vscale(n_surf, gap))
                trans = vsub(target, ep_rot)
                lig_t = transform_ligand(lig, rot, trans)
                if not clash_ok(lig_t, args.min_dist):
                    continue
                candidates += 1
                # score
                d_key = []
                for ka in key_atoms:
                    ka_t = vadd(matvec(rot, ka["xyz"]), trans)
                    d_key.append(grid.min_dist(ka_t))
                mean_key = sum(d_key) / len(d_key)
                contacts_region = 0
                for ra in region_atoms:
                    ra_t = vadd(matvec(rot, ra["xyz"]), trans)
                    if grid.min_dist(ra_t) < 8.0:
                        contacts_region += 1
                score = mean_key - 0.05 * contacts_region
                if score < best_score:
                    best_score = score
                    best = (rot, trans, lig_t, mean_key, contacts_region, d_key,
                            pt, gap, spin_i)
    if best is None:
        raise SystemExit("No clash-free placement found; relax --min-dist or widen search")

    rot, trans, lig_t, mean_key, contacts_region, d_key, pt, gap, spin_i = best
    mind = min_dist_group(lig_t, rec)
    print(f"\nBest placement: score={best_score:.3f} mean_key_dist={mean_key:.2f} "
          f"region_contacts(<8A)={contacts_region} mind={mind:.2f} "
          f"(surface pt {pt[0]:.1f},{pt[1]:.1f},{pt[2]:.1f} gap {gap} spin {spin_i})")
    print("Key residue distances to receptor:")
    for r, d in zip(sorted(key), d_key):
        print(f"  residue {r}: {d:.2f} A")

    # write combined PDB: receptor (orig chain IDs) + ligand as new chain
    with open(args.out, "w") as f:
        for a in rec:
            f.write(a["raw"])
        f.write("TER\n")
        for i, a in enumerate(lig_t, 1):
            name = f"{a['atom']:<4s}"[:4]
            x, y, z = a["xyz"]
            f.write(f"ATOM  {i:5d} {name} {a['resname']:<3s} "
                    f"{args.ligand_out_chain}{a['resnum']:4d}    "
                    f"{x:8.3f}{y:8.3f}{z:8.3f}  1.00  0.00\n")
        f.write("TER\nEND\n")
    print(f"Saved model: {args.out} (ligand as chain {args.ligand_out_chain})")
    print("NOTE: this is a proposed geometric model, not an energy-optimized or "
          "experimentally determined structure.")


if __name__ == "__main__":
    main()
