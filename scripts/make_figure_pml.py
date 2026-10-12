#!/usr/bin/env python3
"""Generate a publication-quality PyMOL figure script (.pml) from a JSON config.

Encodes the rendering pipeline proven on the S100A8-TLR4/MD-2 and lithospermic-acid
figures: gradient cartoons, semi-transparent pocket surface, CPK sticks, single-atom
labels (avoid the 27-duplicate-label trap), depth fog + ray tracing, 3200x2200.

Config example (figure_config.json):
{
  "pdb": "complex.pdb",
  "chains": {"A": "TLR4", "C": "MD2", "S": "S100A8", "L": "LIG"},
  "gradient": {"A": [[27, "0x386BB0"], [310, "0x386BB0"], [311, "0x3A6CAC"], [625, "0x3A6CAC"]],
               "S": [[1, "0xFABF73"], [39, "0xFABF73"], [40, "0xEE8C38"], [59, "0xEE8C38"], [60, "0xD16119"], [90, "0xD16119"]]},
  "ligand": {"chain": "L", "resname": "LIG", "color": "0x00B85A", "label": "Lithospermic acid"},
  "figures": [
    {"type": "overall", "name": "overall",
     "chain_labels": {"S": 45, "A": 400, "C": 140}},
    {"type": "detail", "name": "detail",
     "focus": "S:68-77", "key_residues": {"S": [73, 74, 75, 77]},
     "contacts": {"from": "S:73,74,75,77", "to_chain": "C", "cutoff": 4.5},
     "dash_color": "0xE6A800"},
    {"type": "pocket", "name": "pocket",
     "pocket_residues": {"C": [48, 52, 94, 102, 119, 147, 151]}}
  ],
  "render": {"width": 3200, "height": 2200, "dpi": 300, "transparent": true}
}

Usage:
  python make_figure_pml.py --config figure_config.json --out figure.pml
  # then render (background long task; PyMOL must run in the .pml directory):
  pymol -c -r figure.pml
"""
import argparse
import json
import os
import sys

PALETTE = {
    "TLR4": "0x386BB0", "MD2": "0x24B8A8", "S100A8": "0xD16119",
    "LIG": "0x00B85A", "pocket": "0xF2C432", "contact_dash": "0xE6A800",
}
RENDER_BLOCK = """\
bg_color white
set ray_shadows, 0
set antialias, 2
set field_of_view, 18
set ray_trace_mode, 1
set ray_trace_gain, 0.08
set fog_start, 0.42
set fog_end, 0.90
set depth_cue, 1
set cart_smooth_loops, 1
set cart_fancy_helices, 1
set cart_oval_quality, 6
set surface_quality, 1
set ambient, 0.30
set direct, 0.55
set reflect, 0.15
set spec_reflect, 0.12
set light_count, 3
"""


def chain_color(name):
    return PALETTE.get(name, "0x777777")


def label_positions(res_list):
    """Spread label offsets to avoid overlap (clockwise pattern)."""
    offs = [(-14, 5, 15), (14, 5, 15), (-14, -5, 15), (14, -5, 15),
            (0, 12, 15), (-16, 0, 15), (16, 0, 15), (0, -12, 15)]
    return [offs[i % len(offs)] for i in range(len(res_list))]


def build_overall(fig, cfg):
    lines = ["# --- Figure: overall ---", "disable all", "enable complex"]
    lig = cfg.get("ligand")
    if lig:
        lines.append(f"hide labels, chain {lig['chain']}")
    lines.append("set label_size, 26")
    lines.append("set label_connector, 0")
    for ch, res in fig.get("chain_labels", {}).items():
        name = cfg["chains"].get(ch, ch)
        lines.append(f'label chain {ch} and resi {res} and name CA, "{name}"')
        lines.append(f"set label_position, [0, 5, 30], chain {ch} and resi {res} and name CA")
        lines.append(f"set label_color, {chain_color(name)}, chain {ch} and resi {res} and name CA")
    if lig and lig.get("label"):
        lines.append(f"label chain {lig['chain']} and resn {lig['resname']} and id 2, "
                     f'"{lig["label"]}"')
        lines.append(f"set label_position, [-30, -15, 35], chain {lig['chain']} "
                     f"and resn {lig['resname']} and id 2")
        lines.append(f"set label_size, 22, chain {lig['chain']} and "
                     f"resn {lig['resname']} and id 2")
        lines.append(f"set label_color, {lig.get('color', PALETTE['LIG'])}, "
                     f"chain {lig['chain']} and resn {lig['resname']} and id 2")
    lines += [
        "orient chain A",
        "turn y, -28",
        "turn x, 22",
        "zoom complex, buffer=8",
        "move z, 5",
    ]
    return lines


def build_detail(fig, cfg):
    lines = ["# --- Figure: interface detail ---", "hide labels, all"]
    chain, rng = fig["focus"].split(":")
    r0, r1 = rng.split("-") if "-" in rng else (rng, rng)
    lines.append(f"select focus_sel, chain {chain} and resi {r0}-{r1}")
    lines.append("show sticks, focus_sel")
    # key residues
    for ch, residues in fig.get("key_residues", {}).items():
        rs = "+".join(str(r) for r in residues)
        lines.append(f"select key_res, chain {ch} and resi {rs}")
        lines.append("show sticks, key_res")
        colors = ["0x25,0x8C,0xD9", "0x33,0xB2,0x4D", "0x94,0x59,0xB8", "0x66,0x66,0x66"]
        lines.append("set stick_radius, 0.16")
        for i, r in enumerate(residues):
            c = colors[i % 4].replace(",", "")
            lines.append(f"color {colors[i]}, chain {ch} and resi {r} and elem C")
            lines.append(f"label chain {ch} and resi {r} and name CA, \"{r}\"")
        offs = label_positions(residues)
        for (dx, dy, dz), r in zip(offs, residues):
            lines.append(f"set label_position, [{dx}, {dy}, {dz}], chain {ch} "
                         f"and resi {r} and name CA")
        lines.append("set label_size, 16")
        lines.append("set label_color, black")
        lines.append("set label_connector, 1")
        lines.append("set label_connector_color, gray50")
    # contacts
    ct = fig.get("contacts")
    if ct:
        fch, frs = ct["from"].split(":")
        rs = "+".join(frs.split(","))
        lines.append(f"select ct_from, chain {fch} and resi {rs}")
        lines.append(f"distance contacts_obj, ct_from, chain {ct['to_chain']}, {ct['cutoff']}")
        lines.append("hide labels, contacts_obj")
        lines.append(f"set dash_color, {fig.get('dash_color', PALETTE['contact_dash'])}, contacts_obj")
        lines.append(f"color {fig.get('dash_color', PALETTE['contact_dash'])}, contacts_obj")
        lines.append("set dash_radius, 0.08")
        lines.append("set dash_gap, 0.30")
    lines += [
        "origin focus_sel",
        "orient focus_sel",
        "turn y, 48",
        "turn x, -52",
        "zoom focus_sel, buffer=6",
        "move z, 2",
    ]
    return lines


def build_pocket(fig, cfg):
    lines = ["# --- Figure: ligand pocket ---", "hide labels, all"]
    lig = cfg["ligand"]
    lines.append(f"select lig_sel, chain {lig['chain']} and resn {lig['resname']}")
    lines.append(f"show sticks, lig_sel")
    lines.append(f"color {lig.get('color', PALETTE['LIG'])}, lig_sel and elem C")
    lines.append("color red, lig_sel and elem O")
    lines.append("color blue, lig_sel and elem N")
    lines.append("color white, lig_sel and elem H")
    lines.append("set stick_radius, 0.18, lig_sel")
    for ch, residues in fig.get("pocket_residues", {}).items():
        rs = "+".join(str(r) for r in residues)
        lines.append(f"select pocket_res, chain {ch} and resi {rs}")
        lines.append("show sticks, pocket_res")
        lines.append(f"color {PALETTE['pocket']}, pocket_res and elem C")
        lines.append("color red, pocket_res and elem O")
        lines.append("color blue, pocket_res and elem N")
        lines.append(f"distance lig_contacts, lig_sel, pocket_res, "
                     f"{fig.get('cutoff', 4.5)}")
        lines.append("hide labels, lig_contacts")
        lines.append(f"set dash_color, {fig.get('dash_color', PALETTE['contact_dash'])}, lig_contacts")
        lines.append(f"color {fig.get('dash_color', PALETTE['contact_dash'])}, lig_contacts")
        lines.append("set dash_radius, 0.08")
        lines.append("set dash_gap, 0.30")
        # residue labels with spread offsets
        offs = label_positions(residues)
        for (dx, dy, dz), r in zip(offs, residues):
            lines.append(f"label chain {ch} and resi {r} and name CA, \"{r}\"")
            lines.append(f"set label_position, [{dx}, {dy}, {dz}], chain {ch} "
                         f"and resi {r} and name CA")
        lines.append("set label_size, 16")
        lines.append("set label_color, black")
        lines.append("set label_connector, 1")
        lines.append("set label_connector_color, black")
        lines.append("set label_connector_width, 1.0")
    # single ligand label
    lines.append(f"label chain {lig['chain']} and resn {lig['resname']} and id 2, "
                 f"'{lig.get('label', 'Ligand')}'")
    lines.append(f"set label_position, [0, 0, 25], chain {lig['chain']} and "
                 f"resn {lig['resname']} and id 2")
    lines.append("set label_size, 20")
    lines.append(f"set label_color, {lig.get('color', PALETTE['LIG'])}, chain "
                 f"{lig['chain']} and resn {lig['resname']} and id 2")
    lines += [
        f"set transparency, 0.65, chain C",
        "orient lig_sel",
        "turn y, -30",
        "turn x, 15",
        "zoom lig_sel, buffer=14",
        "move z, 2",
    ]
    return lines


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", required=True, help="JSON figure config")
    p.add_argument("--out", required=True, help="output .pml path")
    args = p.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)

    # Resolve pdb path relative to the config file, then absolutize so the .pml
    # renders correctly regardless of PyMOL's working directory.
    cfg_dir = os.path.dirname(os.path.abspath(args.config))
    cfg["pdb"] = os.path.normpath(os.path.join(cfg_dir, cfg["pdb"]))
    # PNG/session outputs land next to the config too.
    out_dir = cfg_dir

    r = cfg.get("render", {})
    W, H = r.get("width", 3200), r.get("height", 2200)
    dpi = r.get("dpi", 300)
    transparent = r.get("transparent", True)

    out = []
    out.append(f"# Generated by make_figure_pml.py - PyMOL publication figures")
    out.append(f"# Render with:  pymol -c -r {os.path.abspath(args.out)}")
    out.append(f"load {cfg['pdb'].replace(os.sep, '/')}, complex")
    out.append("")

    # chains: cartoons + colors / gradients
    for ch, name in cfg["chains"].items():
        c = chain_color(name)
        if name == "LIG":
            continue
        out.append(f"select ch_{ch}, chain {ch}")
        out.append(f"show cartoon, ch_{ch}")
        out.append(f"hide lines, ch_{ch}")
        out.append(f"color {c}, ch_{ch}")
    # gradients
    for ch, stops in (cfg.get("gradient") or {}).items():
        for resi, color in stops:
            out.append(f"color {color}, chain {ch} and resi {resi}")
    # MD2-style surface for the pocket-bearing chain (default C if present)
    surf_chain = cfg.get("surface_chain", "C")
    if surf_chain in cfg["chains"]:
        out.append(f"show surface, chain {surf_chain}")
        out.append(f"set transparency, 0.50, chain {surf_chain}")
        out.append(f"set surface_quality, 1")
    out.append("")
    out.append(RENDER_BLOCK)
    out.append("")

    builders = {"overall": build_overall, "detail": build_detail, "pocket": build_pocket}
    for fig in cfg.get("figures", []):
        b = builders.get(fig["type"])
        if not b:
            print(f"WARNING: unknown figure type {fig['type']}, skipped", file=sys.stderr)
            continue
        out.extend(b(fig, cfg))
        out.append(f"ray {W}, {H}")
        out.append(f"png {os.path.join(out_dir, fig['name'] + '_white.png').replace(os.sep, '/')}, dpi={dpi}")
        if transparent:
            out.append("set ray_opaque_background, 0")
            out.append(f"ray {W}, {H}")
            out.append(f"png {os.path.join(out_dir, fig['name'] + '_transparent.png').replace(os.sep, '/')}, dpi={dpi}")
            out.append("set ray_opaque_background, 1")
        out.append("")

    out.append(f"save {os.path.join(out_dir, 'figures_session.pse').replace(os.sep, '/')}")
    text = "\n".join(out) + "\n"
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"Wrote {args.out} ({len(text.splitlines())} lines, "
          f"{len(cfg.get('figures', []))} figures)")


if __name__ == "__main__":
    main()
