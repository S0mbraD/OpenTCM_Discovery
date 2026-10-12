#!/usr/bin/env python3
"""Contact analysis for a multi-chain complex / docked pose PDB.

For a ligand chain against all (or selected) receptor chains:
  - residues within a distance cutoff (default 4.5 A)
  - potential hydrogen bonds (polar-polar atoms within 3.5 A)
  - optional merge of ligand PDB into a complex PDB (correct column alignment)

Examples:
  python analyze_contacts.py --pdb complex.pdb --ligand-chain L \
      --receptor-chains A,C --cutoff 4.5 --out contacts.json

  # merge docked pose into receptor model (writes combined PDB)
  python analyze_contacts.py --merge receptor.pdb ligand_pose.pdb \
      --out complex_with_ligand.pdb
"""
import argparse
import json
import math

POLAR = ("N", "O", "S")


def read_atoms(path, keep_chains=None):
    atoms = []
    with open(path) as f:
        for line in f:
            if line.startswith("ATOM"):
                chain = line[21] or " "
                if keep_chains and chain not in keep_chains:
                    continue
                atoms.append({
                    "chain": chain,
                    "resnum": int(line[22:26]),
                    "resname": line[17:20].strip(),
                    "atom": line[12:16].strip(),
                    "elem": (line[76:78].strip() or line[12:16].strip()[0]),
                    "x": float(line[30:38]),
                    "y": float(line[38:46]),
                    "z": float(line[46:54]),
                })
    return atoms


def dist(a, b):
    return math.dist((a["x"], a["y"], a["z"]), (b["x"], b["y"], b["z"]))


def cmd_analyze(args):
    lig = read_atoms(args.pdb, keep_chains={args.ligand_chain})
    rec = read_atoms(args.pdb, keep_chains=set(args.receptor_chains.replace(" ", "").split(",")))
    if not lig:
        raise SystemExit(f"No atoms found for ligand chain {args.ligand_chain!r}")
    print(f"Ligand chain {args.ligand_chain}: {len(lig)} atoms")
    print(f"Receptor chains {args.receptor_chains}: {len(rec)} atoms")

    # min distance per receptor residue
    best = {}
    for la in lig:
        for ra in rec:
            if ra["chain"] != la["chain"]:
                d = dist(la, ra)
                key = (ra["chain"], ra["resnum"], ra["resname"])
                if d < best.get(key, (999,))[0]:
                    best[key] = (d, la, ra)

    contacts = sorted(((k[0], k[2], k[1], v[0]) for k, v in best.items()
                       if v[0] <= args.cutoff), key=lambda t: t[3])
    print(f"\n=== Contacts < {args.cutoff} A ===")
    for ch, resname, resnum, d in contacts:
        print(f"  chain {ch} {resname}{resnum}: {d:.2f} A")
    print(f"Total: {len(contacts)} residues")

    # H-bond candidates
    hbonds = []
    for la in lig:
        if la["elem"] not in POLAR:
            continue
        for ra in rec:
            if ra["elem"] not in POLAR:
                continue
            d = dist(la, ra)
            if d <= args.hbond:
                hbonds.append((la, ra, d))
    print(f"\n=== Potential H-bonds (polar-polar < {args.hbond} A) ===")
    for la, ra, d in hbonds:
        print(f"  lig {la['atom']}({la['elem']}) -- chain {ra['chain']} "
              f"{ra['resname']}{ra['resnum']} {ra['atom']}: {d:.2f} A")
    print(f"Total: {len(hbonds)} candidate bonds")

    if args.out:
        result = {
            "ligand_chain": args.ligand_chain,
            "cutoff": args.cutoff,
            "contacts": [{"chain": c, "resname": r, "resnum": n, "min_dist": round(d, 2)}
                         for c, r, n, d in contacts],
            "hbond_candidates": [{"ligand_atom": la["atom"],
                                  "receptor_chain": ra["chain"],
                                  "receptor_res": f"{ra['resname']}{ra['resnum']}",
                                  "receptor_atom": ra["atom"],
                                  "dist": round(d, 2)} for la, ra, d in hbonds],
        }
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"\nSaved: {args.out}")


def cmd_merge(args):
    """Merge ligand PDB (e.g. Vina best pose) into a complex PDB before END."""
    with open(args.complex) as f:
        lines = f.readlines()
    with open(args.ligand) as f:
        lig_lines = f.readlines()
    out = []
    for line in lines:
        if line.startswith("END"):
            out.extend([l for l in lig_lines if not l.startswith("END")])
            out.append("END\n")
        else:
            out.append(line)
    with open(args.out, "w") as f:
        f.writelines(out)
    n = sum(1 for l in lig_lines if l.startswith("ATOM"))
    print(f"Merged {n} ligand atoms -> {args.out}")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("analyze", help="contact + H-bond analysis")
    sp.add_argument("--pdb", required=True, help="complex PDB with ligand chain")
    sp.add_argument("--ligand-chain", required=True, default=None)
    sp.add_argument("--receptor-chains", required=True)
    sp.add_argument("--cutoff", type=float, default=4.5)
    sp.add_argument("--hbond", type=float, default=3.5)
    sp.add_argument("--out", help="save JSON")
    sp.set_defaults(func=cmd_analyze)

    sp = sub.add_parser("merge", help="merge ligand PDB into complex PDB")
    sp.add_argument("--complex", required=True)
    sp.add_argument("--ligand", required=True)
    sp.add_argument("--out", required=True)
    sp.set_defaults(func=cmd_merge)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
