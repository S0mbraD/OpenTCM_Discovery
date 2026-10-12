#!/usr/bin/env python3
"""Fetch and clean molecular structures from structural databases.

Sources (all live-tested 2026-10):
  - RCSB PDB (files.rcsb.org + data.rcsb.org): protein complex structures, metadata
  - PubChem PUG-REST: small-molecule properties + 3D SDF
  - PDBe API (EBI): entry summaries, ligand monomers per chain
  - SWISS-MODEL Repository: homology models by UniProt accession
  Dead end: BindingDB REST endpoints all 404 (2026-10) - use ChEMBL for affinities

Commands:
  pdb        Download PDB entry, optionally filter chains / drop HETATM
  pdbinfo    Get entry metadata (title, method, resolution, entities)
  pubchem    Get compound properties + 3D SDF by CID or name
  pdbe       PDBe entry summary / ligand monomers
  swissmodel List homology models for a UniProt accession

Examples:
  python fetch_structure.py pdb 2Z64 --chains A,C --drop-hetatm --out receptor.pdb
  python fetch_structure.py pdbinfo 2Z64
  python fetch_structure.py pubchem 6441498 --out ligand.sdf
  python fetch_structure.py pdbe 2Z64 --section ligands
  python fetch_structure.py swissmodel P05109 --out models.json
"""
import argparse
import json
import sys
import time
import urllib.request
import urllib.error

UA = {"User-Agent": "Mozilla/5.0 (compatible; structure-docking-analysis/1.0)"}
RETRIES = 4
SLEEP_S = 3


def http_get(url: str, binary: bool = False, timeout: int = 60):
    """GET with retry/backoff. Returns text or bytes."""
    last_err = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                return data if binary else data.decode("utf-8", errors="replace")
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            last_err = e
            wait = SLEEP_S * (attempt + 1)
            print(f"  retry {attempt + 1}/{RETRIES} after {wait}s: {e}", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"GET failed after {RETRIES} retries: {url} ({last_err})")


def cmd_pdb(args):
    url = f"https://files.rcsb.org/download/{args.pdb_id.upper()}.pdb"
    print(f"Downloading {url} ...")
    text = http_get(url)
    lines = text.splitlines(keepends=True)
    kept, dropped_het, dropped_chain = [], 0, 0
    want_chains = set(args.chains.replace(" ", "").split(",")) if args.chains else None
    # For multi-character chain IDs PDB is deprecated; mmCIF should be used. Here we
    # handle classic single-char chains and terminate filtering at END.
    for line in lines:
        rec = line[:6].strip()
        if rec in ("ATOM", "HETATM"):
            if rec == "HETATM" and args.drop_hetatm:
                dropped_het += 1
                continue
            if want_chains is not None and line[21] not in want_chains:
                dropped_chain += 1
                continue
            kept.append(line)
        elif rec in ("TER", "END", "MASTER", "ENDMDL"):
            kept.append(line)
        elif rec in ("HEADER", "TITLE", "COMPND", "REMARK", "SEQRES", "CRYST1"):
            kept.append(line)
    out_text = "".join(kept)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out_text)
        n_atom = sum(1 for l in kept if l.startswith("ATOM"))
        print(f"Saved {args.out}: {n_atom} ATOM lines, "
              f"dropped {dropped_het} HETATM / {dropped_chain} other-chain lines")
    else:
        sys.stdout.write(out_text)


def cmd_pdbinfo(args):
    pdb_id = args.pdb_id.upper()
    url = f"https://data.rcsb.org/rest/v1/core/entry/{pdb_id}"
    print(f"Fetching metadata for {pdb_id} ...")
    data = json.loads(http_get(url))
    info = {
        "id": pdb_id,
        "title": data.get("struct", {}).get("title"),
        "method": (data.get("exptl") or [{}])[0].get("method"),
        "resolution": data.get("rcsb_entry_info", {}).get("resolution_combined"),
        "polymer_entities": data.get("rcsb_entry_info", {}).get("polymer_entity_count"),
        "ligands": data.get("rcsb_entry_info", {}).get("nonpolymer_entity_count"),
        "deposition_date": data.get("rcsb_accession_info", {}).get("deposit_date"),
        "citation": data.get("rcsb_entry_container_identifiers"),
    }
    print(json.dumps(info, indent=2, ensure_ascii=False, default=str))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(info, f, indent=2, ensure_ascii=False, default=str)


def cmd_pubchem(args):
    query = args.query.strip()
    # Resolve to CID (accepts numeric CID or name)
    if query.isdigit():
        cid = query
    else:
        url = ("https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
               + urllib.request.quote(query) + "/cids/JSON")
        data = json.loads(http_get(url))
        cid = str(data["IdentifierList"]["CID"][0])
    print(f"CID: {cid}")
    # Properties
    prop_url = (f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/"
                "CanonicalSMILES,IsomericSMILES,MolecularFormula,MolecularWeight,IUPACName/JSON")
    props = json.loads(http_get(prop_url))["PropertyTable"]["Properties"][0]
    for k, v in props.items():
        print(f"{k}: {v}")
    # 3D SDF
    if args.out:
        sdf_url = (f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/"
                   "SDF?record_type=3d")
        sdf = http_get(sdf_url)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(sdf)
        n_atoms = 0
        for line in sdf.splitlines()[3:4]:
            pass
        # atom count is in counts line (line index 3, field 0)
        counts = sdf.splitlines()
        if len(counts) > 3:
            try:
                n_atoms = int(counts[3].split()[0])
            except (ValueError, IndexError):
                pass
        print(f"Saved 3D SDF: {args.out} ({n_atoms} atoms)")
    if args.props:
        with open(args.props, "w", encoding="utf-8") as f:
            json.dump({"cid": int(cid), **props}, f, indent=2, ensure_ascii=False)
        print(f"Saved properties: {args.props}")


def cmd_pdbe(args):
    """PDBe (EBI) API: entry summary or ligand monomers."""
    pdb_id = args.pdb_id.lower()
    if args.section == "ligands":
        url = f"https://www.ebi.ac.uk/pdbe/api/pdb/entry/ligand_monomers/{pdb_id}"
    else:
        url = f"https://www.ebi.ac.uk/pdbe/api/pdb/entry/summary/{pdb_id}"
    print(f"Querying PDBe: {url}")
    data = json.loads(http_get(url))
    print(json.dumps(data, indent=2, ensure_ascii=False)[:4000])
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"Saved: {args.out}")


def cmd_swissmodel(args):
    """SWISS-MODEL Repository: homology models for a UniProt accession.
    Useful when an experimental human template is unavailable (e.g. replace a
    mouse receptor with a human homology model)."""
    url = f"https://swissmodel.expasy.org/repository/uniprot/{args.accession}.json"
    print(f"Querying SWISS-MODEL Repository: {url}")
    data = json.loads(http_get(url, timeout=90))
    results = data.get("result", {})
    models = results.get("structures", [])
    print(f"{len(models)} homology models found for {args.accession}")
    for m in models[: args.limit]:
        print(f"  {m.get('model_id')}: {m.get('template')}  "
              f"gmqe={m.get('gmqe')}  oligo={m.get('oligo-state')}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"Saved full JSON: {args.out}")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("pdb", help="download + clean PDB entry")
    sp.add_argument("pdb_id")
    sp.add_argument("--chains", help="comma-separated chain IDs to keep, e.g. A,C")
    sp.add_argument("--drop-hetatm", action="store_true",
                    help="remove HETATM (waters, glycans, ligands)")
    sp.add_argument("--out", help="output PDB file (stdout if omitted)")
    sp.set_defaults(func=cmd_pdb)

    sp = sub.add_parser("pdbinfo", help="entry metadata from RCSB data API")
    sp.add_argument("pdb_id")
    sp.add_argument("--out", help="save JSON metadata")
    sp.set_defaults(func=cmd_pdbinfo)

    sp = sub.add_parser("pubchem", help="compound props + 3D SDF")
    sp.add_argument("query", help="CID or compound name")
    sp.add_argument("--out", help="output 3D SDF path")
    sp.add_argument("--props", help="save properties JSON")
    sp.set_defaults(func=cmd_pubchem)

    sp = sub.add_parser("pdbe", help="PDBe (EBI) entry info")
    sp.add_argument("pdb_id")
    sp.add_argument("--section", choices=["summary", "ligands"], default="summary")
    sp.add_argument("--out", help="save JSON")
    sp.set_defaults(func=cmd_pdbe)

    sp = sub.add_parser("swissmodel", help="homology models for a UniProt accession")
    sp.add_argument("accession", help="UniProt AC, e.g. P05109")
    sp.add_argument("--limit", type=int, default=10)
    sp.add_argument("--out", help="save full JSON")
    sp.set_defaults(func=cmd_swissmodel)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
