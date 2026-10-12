#!/usr/bin/env python3
"""Small-molecule docking pipeline: pocket center -> PDBQT prep -> AutoDock Vina -> pose PDB.

Encapsulates the full AutoDock Vina 1.2.5 workflow used for receptor-ligand docking:
  1. Compute docking box center from pocket residue CA atoms (centroid)
  2. Prepare receptor/ligand PDBQT via Meeko (mk_prepare_receptor / mk_prepare_ligand)
  3. Run AutoDock Vina (locates vina.exe from --vina / VINA_EXE env / PATH)
  4. Parse best pose, write ligand-only PDB with STRICT PDB column alignment
     (chainID must be column 22 - a classic hand-formatting bug)
  5. Report all-mode affinities + convergence

Requirements: pip install meeko gemmi ; vina.exe (AutoDock-Vina 1.2.5 win binary
from https://github.com/ccsb-scripps/AutoDock-Vina/releases)

Example:
  python run_vina.py --receptor receptor.pdb --ligand ligand.sdf \
      --pocket-chain C --pocket-residues 46,52,53,87,92,94,102,113,119,124,126,131,147 \
      --box-size 30 --exhaustiveness 16 --seed 42 --workdir dock_out
"""
import argparse
import math
import os
import re
import shutil
import subprocess
import sys

PDBQT_ATOM = re.compile(r"^(ATOM|HETATM)")


def find_vina(explicit):
    """Locate vina executable (explicit > VINA_EXE env > skill-bundled
    bin/vina.exe > PATH)."""
    if explicit and os.path.isfile(explicit):
        return explicit
    if os.environ.get("VINA_EXE") and os.path.isfile(os.environ["VINA_EXE"]):
        return os.environ["VINA_EXE"]
    # skill-bundled binary: <skill>/bin/vina.exe (self-contained fallback)
    bundled = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "bin", "vina.exe")
    if os.path.isfile(bundled):
        return bundled
    which = shutil.which("vina") or shutil.which("vina.exe")
    if which:
        return which
    raise FileNotFoundError(
        "vina executable not found. Pass --vina <path>, set VINA_EXE env, or "
        "restore bin/vina.exe inside the skill (from "
        "https://github.com/ccsb-scripps/AutoDock-Vina/releases)")


def find_meeko_tools():
    """Locate mk_prepare_receptor / mk_prepare_ligand CLIs next to current python."""
    exe_suffix = ".exe" if os.name == "nt" else ""
    base = os.path.join(os.path.dirname(sys.executable), "Scripts")
    rec = os.path.join(base, "mk_prepare_receptor" + exe_suffix)
    lig = os.path.join(base, "mk_prepare_ligand" + exe_suffix)
    if os.path.isfile(rec) and os.path.isfile(lig):
        return rec, lig
    for tool in ("mk_prepare_receptor", "mk_prepare_ligand"):
        if shutil.which(tool):
            pass
    rec2 = shutil.which("mk_prepare_receptor")
    lig2 = shutil.which("mk_prepare_ligand")
    if rec2 and lig2:
        return rec2, lig2
    raise FileNotFoundError(
        "Meeko CLIs not found. Install with: "
        f"{sys.executable} -m pip install meeko gemmi")


def pocket_center(pdb_path, chain, residues):
    """Centroid of CA atoms of pocket residues."""
    want = {int(r) for r in residues.split(",") if r.strip()}
    pts = []
    with open(pdb_path) as f:
        for line in f:
            if line.startswith("ATOM") and line[12:16].strip() == "CA":
                if (line[21] or " ") == chain and int(line[22:26]) in want:
                    pts.append((float(line[30:38]), float(line[38:46]), float(line[46:54])))
    missing = want - {int(line[22:26]) for line in open(pdb_path)
                      if line.startswith("ATOM") and (line[21] or " ") == chain}
    if not pts:
        raise ValueError(f"No pocket CA atoms found (chain {chain}, residues {residues})")
    c = tuple(sum(p[i] for p in pts) / len(pts) for i in range(3))
    print(f"Pocket center from {len(pts)} residues: "
          f"x={c[0]:.2f} y={c[1]:.2f} z={c[2]:.2f}")
    if missing:
        print(f"  WARNING: residues not found in structure: {sorted(missing)}")
    return c


def pose_to_pdb(pdbqt_path, model_no, out_pdb, chain_id="L", resname="LIG", resseq=100):
    """Extract one MODEL from a Vina output PDBQT -> strictly formatted ligand PDB.

    PDB fixed columns: 13-16 name, 18-20 resName, 22 chainID, 23-26 resSeq,
    31-54 x/y/z, 77-78 element. Wrong chain column = invisible ligand in PyMOL.
    """
    atoms = []
    in_model = False
    with open(pdbqt_path) as f:
        for line in f:
            if line.startswith("MODEL"):
                in_model = (int(line.split()[1]) == model_no)
                continue
            if line.startswith("ENDMDL"):
                if in_model:
                    break
                in_model = False
                continue
            if in_model and PDBQT_ATOM.match(line):
                name = line[12:16].strip()
                x, y, z = float(line[30:38]), float(line[38:46]), float(line[46:54])
                ad_type = line[77:].split()[0] if line[77:].split() else "C"
                elem = {"A": "C", "OA": "O", "NA": "N", "SA": "S", "HD": "H",
                        "HS": "H", "OS": "O", "NS": "N"}.get(ad_type, ad_type[:1])
                atoms.append((name, x, y, z, elem))
    out_lines = []
    for i, (name, x, y, z, elem) in enumerate(atoms, 1):
        aname = f"{name:<4s}"[:4]
        out_lines.append(
            f"ATOM  {i:5d} {aname} {resname:<3s} {chain_id:1s}{resseq:4d}    "
            f"{x:8.3f}{y:8.3f}{z:8.3f}  1.00  0.00          {elem:>2s}\n")
    out_lines.append("TER\nEND\n")
    with open(out_pdb, "w") as f:
        f.writelines(out_lines)
    # self-verify column alignment
    first = out_lines[0]
    assert first[17:20].strip() == resname and first[21] == chain_id, "column misalignment!"
    print(f"Best pose -> {out_pdb} ({len(atoms)} atoms, chain {chain_id}, "
          f"resname {resname}) [column check OK]")
    return atoms


def parse_vina_table(log_path):
    """Parse affinity table from Vina stdout log."""
    rows = []
    in_table = False
    with open(log_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.strip().startswith("mode |"):
                in_table = True
                continue
            if in_table:
                m = re.match(r"\s*(\d+)\s+(-?[\d.]+)\s+", line)
                if m:
                    rows.append((int(m.group(1)), float(m.group(2))))
                elif line.strip().startswith("---"):
                    continue
                elif rows:
                    break
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--receptor", required=True, help="cleaned receptor PDB (no waters)")
    p.add_argument("--ligand", required=True, help="ligand 3D SDF")
    p.add_argument("--pocket-chain", required=True,
                   help="chain ID of pocket residues, e.g. C")
    p.add_argument("--pocket-residues", required=True,
                   help="comma-separated residue numbers defining the pocket")
    p.add_argument("--box-size", type=float, default=30.0,
                   help="cubic box edge length in Angstrom (default 30)")
    p.add_argument("--box-center", default=None,
                   help="explicit center 'x,y,z' (overrides pocket centroid)")
    p.add_argument("--exhaustiveness", type=int, default=16)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--num-modes", type=int, default=9)
    p.add_argument("--workdir", required=True, help="output directory")
    p.add_argument("--vina", default=None, help="path to vina executable")
    p.add_argument("--ligand-chain", default="L", help="chain ID for pose PDB")
    p.add_argument("--ligand-resname", default="LIG", help="residue name for pose PDB")
    args = p.parse_args()

    os.makedirs(args.workdir, exist_ok=True)

    # 1. box center
    if args.box_center:
        cx, cy, cz = (float(v) for v in args.box_center.split(","))
        print(f"Using explicit box center: ({cx}, {cy}, {cz})")
    else:
        cx, cy, cz = pocket_center(args.receptor, args.pocket_chain, args.pocket_residues)

    # 2. locate tools
    vina = find_vina(args.vina)
    mk_rec, mk_lig = find_meeko_tools()
    print(f"vina: {vina}")
    print(f"meeko: {mk_lig}")

    # 3. prepare receptor / ligand PDBQT
    rec_base = os.path.join(args.workdir, "receptor")
    if not os.path.exists(rec_base + ".pdbqt"):
        subprocess.run([mk_rec, "--read_pdb", args.receptor, "-o", rec_base, "-p"],
                       check=True, cwd=args.workdir)
    lig_pdbqt = os.path.join(args.workdir, "ligand.pdbqt")
    subprocess.run([mk_lig, "-i", args.ligand, "-o", lig_pdbqt],
                   check=True, cwd=args.workdir)

    # 4. run vina
    out_pdbqt = os.path.join(args.workdir, "docked.pdbqt")
    log_path = os.path.join(args.workdir, "vina_log.txt")
    cmd = [vina,
           "--receptor", rec_base + ".pdbqt",
           "--ligand", lig_pdbqt,
           "--center_x", f"{cx:.3f}", "--center_y", f"{cy:.3f}", "--center_z", f"{cz:.3f}",
           "--size_x", str(args.box_size), "--size_y", str(args.box_size),
           "--size_z", str(args.box_size),
           "--exhaustiveness", str(args.exhaustiveness),
           "--num_modes", str(args.num_modes),
           "--seed", str(args.seed),
           "--out", out_pdbqt]
    print("Running Vina:", " ".join(cmd))
    with open(log_path, "w", encoding="utf-8") as log:
        ret = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=args.workdir)
    print(open(log_path, encoding="utf-8", errors="replace").read()[-800:])

    # 5. parse affinities
    table = parse_vina_table(log_path)
    if table:
        best = table[0][1]
        spread = abs(table[0][1] - table[-1][1])
        print(f"\nBest affinity: {best} kcal/mol | "
              f"mode spread: {spread:.2f} kcal/mol over {len(table)} modes")
        for mode, aff in table:
            print(f"  mode {mode}: {aff}")

    # 6. best pose -> PDB
    pose_pdb = os.path.join(args.workdir, "best_pose.pdb")
    pose_to_pdb(out_pdbqt, 1, pose_pdb,
                chain_id=args.ligand_chain, resname=args.ligand_resname)

    print(f"\nDone. Outputs in {args.workdir}: docked.pdbqt, best_pose.pdb, vina_log.txt")


if __name__ == "__main__":
    main()
