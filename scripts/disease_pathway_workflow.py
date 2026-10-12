#!/usr/bin/env python3
"""Disease -> Pathway -> Herb -> Ingredients -> Cross-validation workflow.

Four-step retrieval logic (per requirement):
  Step 1  disease-pathways   : disease -> related KEGG pathways
         (a) KEGG DISEASE id matched by name, direct disease<->pathway links
         (b) SymMap disease-related genes, server-side KEGG enrichment (OR/P/FDR)
  Step 2  pathway-herbs      : pathway gene set -> candidate herbs
         (a) SymMap symptom chain: Disease->MM_symptom->TCM_symptom->Herb
         (b) reverse ingredient chain: pathway genes -> Gene->Mol -> mol pool ->
             herbs containing those mols (needs local herb-mol index, see
             build-index)
  Step 3  herb-ingredients   : herb -> active ingredients (SymMap Herb->Mol with
         OB_score = TCMSP oral bioavailability; filter OB >= cutoff)
  Step 4  cross-validate     : ingredients -> targets (Mol->Gene) vs disease
         pathway gene set; per-ingredient and per-herb hypergeometric test
         (Fisher exact, one-tailed over-representation), EnrichmentRatio, FDR(BH)

Auxiliary:
  build-index : crawl all herbs (SymMap browse list) -> Herb->Mol relations,
         cached atomically to a local JSON (source+queried_at stamped).
         NOTE: this is a relation-pair cache, not curated answers; every record
         keeps SymMap provenance.

Examples:
  # full pipeline for ulcerative colitis (uses cached outputs per step)
  python scripts/disease_pathway_workflow.py run-all --disease "ulcerative colitis" \
      --out workdir_uc

  # step-by-step
  python scripts/disease_pathway_workflow.py disease-pathways --disease "ulcerative colitis" --out workdir_uc
  python scripts/disease_pathway_workflow.py pathway-herbs --pathway hsa05321 --disease "ulcerative colitis" --out workdir_uc
  python scripts/disease_pathway_workflow.py herb-ingredients --herbs "Huang Qin,Huang Lian" --out workdir_uc --ob-cutoff 30
  python scripts/disease_pathway_workflow.py cross-validate --pathway hsa05321 --out workdir_uc

  # one-time herb->mol index build (~700 requests, ~30-60 min, resumable)
  python scripts/disease_pathway_workflow.py build-index --out workdir_uc
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from tcm_core.stats import bh_fdr, hypergeometric_p, log_hypergeom_sf  # noqa: E402
from symmap_client import SymMapClient, SymMapTimeout, first_id  # noqa: E402

logp_hypergeometric = log_hypergeom_sf

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
KEGG = "https://rest.kegg.jp"


# ------------------------------------------------------------------ helpers
def http_get(url, timeout=60, retries=3):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="replace")
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            last = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"GET failed: {url} ({last})")


def atomic_write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_json(path, default=None):
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def stamp():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# ------------------------------------------------------------------ KEGG
def kegg_find_disease(disease_name):
    """Match disease name in KEGG DISEASE (H#####) entries."""
    text = http_get(f"{KEGG}/list/disease")
    hits = []
    low = disease_name.lower()
    for line in text.splitlines():
        if not line.strip():
            continue
        hid, names = line.split("\t", 1) if "\t" in line else (line, "")
        for nm in names.split(";"):
            nm = nm.strip().lower()
            if nm == low or (len(low) > 5 and low in nm) or (len(nm) > 5 and nm in low):
                hits.append({"kegg_disease_id": hid, "name": names.strip()})
                break
    return hits


def kegg_disease_pathways(hid):
    """Direct disease <-> pathway links from KEGG link/pathway/disease."""
    text = http_get(f"{KEGG}/link/pathway/{hid}")
    rows = []
    for line in text.splitlines():
        if "\t" not in line:
            continue
        _, path = line.split("\t")
        rows.append(path.replace("path:", ""))
    return rows


_PATHWAY_NAMES_CACHE = None


def kegg_pathway_names():
    """All human pathway ids -> titles (one request, cached in-process)."""
    global _PATHWAY_NAMES_CACHE
    if _PATHWAY_NAMES_CACHE is None:
        text = http_get(f"{KEGG}/list/pathway/hsa")
        names = {}
        for line in text.splitlines():
            if "\t" not in line:
                continue
            pid, title = line.split("\t", 1)
            names[pid.replace("path:", "")] = title.strip()
        _PATHWAY_NAMES_CACHE = names
    return _PATHWAY_NAMES_CACHE


def kegg_pathway_name(pathway_id):
    """hsa##### -> pathway title (from cached full list)."""
    return kegg_pathway_names().get(pathway_id, pathway_id)


def kegg_pathway_genes(pathway_id):
    """Gene symbols of a KEGG pathway (from GENE section of get/entry)."""
    text = http_get(f"{KEGG}/get/{pathway_id}")
    symbols = []
    in_gene = False
    for line in text.splitlines():
        if line.startswith("GENE"):
            in_gene = True
            line = line[8:]
        elif in_gene and line.startswith((" ", "\t")):
            pass
        else:
            in_gene = False
            continue
        parts = line.strip().split(None, 2)
        if len(parts) >= 2:
            symbols.append(parts[1].rstrip(";"))
    return symbols


# ------------------------------------------------------------------ steps
def step_disease_pathways(args, sm):
    out = {"disease": args.disease, "queried_at": stamp(), "sources": []}
    # (a) KEGG direct
    kd = kegg_find_disease(args.disease)
    out["kegg_disease"] = kd
    pathways = {}
    for d in kd:
        for pid in kegg_disease_pathways(d["kegg_disease_id"]):
            pathways.setdefault(pid, {"pathway_id": pid, "sources": ["KEGG-direct"]})
    # (b) SymMap disease genes -> enrichment (cap gene count to keep server fast)
    sym_genes = []
    sm_disease = []
    try:
        sm_disease = sm.search("Disease", args.disease)
        if sm_disease:
            did = sm_disease[0]["Disease_id"]
            genes = sm.related(did, "Gene")
            sym_genes = [g.get("Gene_symbol") for g in genes if g.get("Gene_symbol")]
            out["symmap_disease_id"] = did
            out["symmap_disease_genes"] = len(sym_genes)
            if sym_genes:
                # enrichment server gets slow with huge lists; cap at 300
                rows = sm.enrichment_pathways(sym_genes[:300])
                for r in rows:
                    pid = str(r[0])
                    e = pathways.setdefault(pid, {"pathway_id": pid, "sources": []})
                    e["sources"].append("SymMap-enrichment")
                    e["enrich_p"] = r[3]
                    e["enrich_fdr"] = r[4]
                    e["odd_ratio"] = r[2]
                    e["related_genes"] = r[6]
    except Exception as e:
        out["symmap_error"] = str(e)
    # names
    for pid, e in pathways.items():
        e["pathway_name"] = kegg_pathway_name(pid)
        e["source"] = "+".join(sorted(set(e["sources"])))
    # order: direct first, then enrichment p
    ordered = sorted(pathways.values(),
                     key=lambda e: (0 if "KEGG-direct" in e["sources"] else 1,
                                    e.get("enrich_p", 1)))
    out["pathways"] = ordered
    out["n_pathways"] = len(ordered)
    atomic_write_json(os.path.join(args.out, "step1_disease_pathways.json"), out)
    print(f"Step1 done: {len(ordered)} pathways "
          f"({sum(1 for e in ordered if 'KEGG-direct' in e['sources'])} direct, "
          f"{sum(1 for e in ordered if 'SymMap-enrichment' in e['sources'])} enriched)")
    for e in ordered[:10]:
        print(f"  {e['pathway_id']}  {e.get('pathway_name','')[:45]:45s}  "
              f"[{e['source']}]"
              + (f"  p={e['enrich_p']:.2e}" if e.get("enrich_p") is not None else ""))
    return out


def step_pathway_herbs(args, sm):
    genes = kegg_pathway_genes(args.pathway)
    out = {"pathway": args.pathway, "queried_at": stamp(),
           "pathway_name": kegg_pathway_name(args.pathway),
           "pathway_genes": genes, "n_genes": len(genes)}
    herbs = {}
    # (a) symptom chain
    if args.disease:
        try:
            sm_disease = sm.search("Disease", args.disease)
            if sm_disease:
                did = sm_disease[0]["Disease_id"]
                mm_syms = sm.related(did, "MM_symptom")
                out["mm_symptoms"] = [s.get("MM_symptom_name") for s in mm_syms]
                tcm_ids = set()
                for s in mm_syms:
                    sid = s.get("MM_symptom_id")
                    if not sid:
                        continue
                    try:
                        for t in sm.related(sid, "TCM_symptom"):
                            if t.get("TCM_symptom_id"):
                                tcm_ids.add(t["TCM_symptom_id"])
                    except SymMapTimeout:
                        continue
                out["tcm_symptoms"] = len(tcm_ids)
                for tid in tcm_ids:
                    try:
                        rows = sm.related(tid, "Herb")
                    except SymMapTimeout:
                        continue
                    for h in rows:
                        hid = h.get("Herb_id")
                        if not hid:
                            continue
                        e = herbs.setdefault(hid, {
                            "herb_id": hid,
                            "chinese_name": h.get("Chinese_name"),
                            "pinyin": h.get("Pinyin_name"),
                            "latin_name": h.get("Latin_name"),
                            "class": h.get("Class_Chinese"),
                            "via": set(), "symptom_hits": 0})
                        e["via"].add("symptom-chain")
                        e["symptom_hits"] += 1
        except Exception as e:
            out["symptom_chain_error"] = str(e)
    # (b) reverse ingredient chain via local index
    idx = load_json(os.path.join(args.out, "herb_mol_index.json"))
    if idx:
        mol_hits = {}
        timeout_genes = 0
        for g in genes:
            gid_rows = sm.search("Gene", g)
            if not gid_rows:
                continue
            gid = gid_rows[0].get("Gene_id")
            if not gid:
                continue
            try:
                mols = sm.related(gid, "Mol")
            except SymMapTimeout:
                timeout_genes += 1
                continue
            for m in mols:
                mid = m.get("MOL_id") or m.get("Mol_id")
                if mid:
                    mol_hits.setdefault(mid, []).append(g)
        out["gene_mol_timeouts"] = timeout_genes
        out["pathway_mol_pool"] = len(mol_hits)
        # reverse lookup through index
        for herb_id, mols in idx["herb_to_mols"].items():
            hit = [m for m in mols if m in mol_hits]
            if hit:
                meta = idx["herbs"].get(herb_id, {})
                e = herbs.setdefault(herb_id, {
                    "herb_id": herb_id,
                    "chinese_name": meta.get("chinese_name"),
                    "pinyin": meta.get("pinyin"),
                    "latin_name": meta.get("latin_name"),
                    "class": meta.get("class"), "via": set(), "symptom_hits": 0})
                e["via"].add("ingredient-chain")
                e["mol_hits"] = hit[:20]
                e["n_mol_hits"] = len(hit)
    for e in herbs.values():
        e["via"] = "+".join(sorted(e["via"]))
    ordered = sorted(herbs.values(),
                     key=lambda e: (-e.get("n_mol_hits", 0),
                                    -e.get("symptom_hits", 0)))
    out["herbs"] = ordered
    out["n_herbs"] = len(ordered)
    atomic_write_json(os.path.join(args.out, "step2_pathway_herbs.json"), out)
    print(f"Step2 done: {len(ordered)} candidate herbs "
          f"(symptom-chain {sum(1 for e in ordered if 'symptom-chain' in e['via'])}, "
          f"ingredient-chain {sum(1 for e in ordered if 'ingredient-chain' in e['via'])})")
    for e in ordered[:15]:
        print(f"  {e.get('chinese_name') or e['herb_id']}: via={e['via']} "
              f"mol_hits={e.get('n_mol_hits', 0)} symptom_hits={e.get('symptom_hits', 0)}")
    return out


def step_herb_ingredients(args, sm):
    names = [h.strip() for h in args.herbs.split(",") if h.strip()]
    out = {"herbs": names, "ob_cutoff": args.ob_cutoff,
           "queried_at": stamp(), "source": "SymMap v2.0 (OB_score=TCMSP OB)"}
    result = {}
    for name in names:
        rows = sm.search("Herb", name)
        if not rows and " " in name:
            rows = sm.search("Herb", name.replace(" ", ""))  # "Ku Shen" -> "Kushen"
        if not rows:
            # typeahead resolves canonical display names (incl. latin names)
            for sug in sm.typeahead(name, "Herb")[:1]:
                rows = sm.search("Herb", sug)
                if rows:
                    print(f"  NOTE: {name!r} matched via typeahead -> {sug!r}")
                    break
        if not rows:
            print(f"  WARNING: herb {name!r} not found in SymMap "
                  f"(try pinyin without spaces or the latin name, e.g. 'Kushen', "
                  f"'Radix Sophorae Flavescentis')")
            continue
        herb = rows[0]
        hid = herb.get("Herb_id")
        try:
            mols = sm.related(hid, "Mol")
        except SymMapTimeout as e:
            print(f"  WARNING: {name} ingredients timeout: {e}")
            continue
        entries = []
        for m in mols:
            ob = m.get("OB_score")
            try:
                ob = float(ob) if ob not in (None, "", "None") else None
            except (TypeError, ValueError):
                ob = None
            entry = {
                "mol_id": m.get("MOL_id") or m.get("Mol_id"),
                "name": m.get("Molecule_name"),
                "pubchem_cid": m.get("PubChem_CID"),
                "cas": m.get("CAS_id"),
                "tcmsp_id": m.get("TCMSP_id"),
                "ob": ob,
                "dl_note": "OB only (DL from TCMSP unavailable in SymMap)",
            }
            entries.append(entry)
        active = [e for e in entries if e["ob"] is not None and e["ob"] >= args.ob_cutoff]
        result[name] = {
            "herb_id": hid, "chinese_name": herb.get("Chinese_name"),
            "latin_name": herb.get("Latin_name"),
            "n_ingredients": len(entries), "n_active_ob": len(active),
            "active_ingredients": active,
        }
        print(f"  {name} ({herb.get('Chinese_name')}): {len(entries)} ingredients, "
              f"{len(active)} with OB >= {args.ob_cutoff}")
    out["result"] = result
    atomic_write_json(os.path.join(args.out, "step3_herb_ingredients.json"), out)
    return out


def step_cross_validate(args, sm):
    s2 = load_json(os.path.join(args.out, "step2_pathway_herbs.json"))
    s3 = load_json(os.path.join(args.out, "step3_herb_ingredients.json"))
    pathway = args.pathway or (s2 or {}).get("pathway")
    if not pathway:
        raise SystemExit("pathway required (from step2 or --pathway)")
    genes = (s2 or {}).get("pathway_genes") or kegg_pathway_genes(pathway)
    gene_set = set(genes)
    N = 20000  # approx human protein-coding genes queried in SymMap/KEGG space
    K = len(gene_set)
    out = {"pathway": pathway, "queried_at": stamp(),
           "pathway_genes": len(gene_set),
           "population_size": N, "method": "hypergeometric one-tailed (over)"}

    rows = []
    for herb_name, hdata in (s3 or {}).get("result", {}).items():
        herb_targets = set()
        herb_rows = []
        for ing in hdata.get("active_ingredients", []):
            mid = ing.get("mol_id")
            try:
                tg = sm.related(mid, "Gene")
            except SymMapTimeout:
                tg = []
            symbols = {g.get("Gene_symbol") for g in tg if g.get("Gene_symbol")}
            hit = sorted(symbols & gene_set)
            n = len(symbols)
            k = len(hit)
            p = hypergeometric_p(k, K, n, N) if n else 1.0
            er = (k / n) / (K / N) if n and k else 0.0
            herb_targets |= symbols
            herb_rows.append({
                "herb": herb_name, "ingredient": ing.get("name"),
                "mol_id": mid, "ob": ing.get("ob"),
                "n_targets": n, "n_pathway_hits": k,
                "hit_genes": hit, "p_value": p,
                "enrichment_ratio": round(er, 2)})
        rows.extend(herb_rows)
        # herb-level
        hn = len(herb_targets)
        hk = len(herb_targets & gene_set)
        hp = hypergeometric_p(hk, K, hn, N) if hn else 1.0
        out.setdefault("herb_summary", []).append({
            "herb": herb_name, "chinese_name": hdata.get("chinese_name"),
            "n_active_ingredients": hdata.get("n_active_ob"),
            "n_targets_union": hn, "n_pathway_hits": hk,
            "hit_genes": sorted(herb_targets & gene_set),
            "p_value": hp,
            "enrichment_ratio": round((hk / hn) / (K / N), 2) if hk else 0.0})
        print(f"  {herb_name}: {hn} targets, {hk} pathway hits, "
              f"P={hp:.2e}")
    # BH-FDR over ingredient rows
    pvals = [r["p_value"] for r in rows]
    adj = bh_fdr(pvals)
    for r, a in zip(rows, adj):
        r["fdr_bh"] = a
    rows.sort(key=lambda r: r["p_value"])
    out["ingredient_tests"] = rows
    atomic_write_json(os.path.join(args.out, "step4_cross_validation.json"), out)
    print(f"Step4 done: {len(rows)} ingredient tests, "
          f"{sum(1 for r in rows if r['fdr_bh'] < 0.05)} significant (FDR<0.05)")
    for r in rows[:10]:
        print(f"  {r['ingredient'][:28]:28s} OB={r['ob']} hits={r['n_pathway_hits']:2d} "
              f"p={r['p_value']:.2e} fdr={r['fdr_bh']:.2e}")
    return out


def step_build_index(args, sm):
    """Crawl all SymMap herbs -> Herb->Mol relations; cache locally."""
    idx_path = os.path.join(args.out, "herb_mol_index.json")
    idx = load_json(idx_path, {"herbs": {}, "herb_to_mols": {},
                               "mol_to_herbs": {}, "built_at": None,
                               "source": "SymMap v2.0 related_components"})
    # enumerate all herbs by paginating /browse/ (server-side pagination)
    page = 1
    all_herbs = []
    while True:
        data = urllib.parse.urlencode({"page_size": 50, "page_num": page,
                                       "table_name": "Herb", "mol_type": "ALL"}).encode()
        req = urllib.request.Request(sm.base + "/browse/", data=data, headers=UA)
        with urllib.request.urlopen(req, timeout=120) as r:
            res = json.loads(r.read().decode("utf-8", errors="replace"))
        rows = res.get("Data") or []
        all_herbs.extend(rows)
        total = res.get("Total", len(all_herbs))
        if len(all_herbs) >= total or not rows:
            break
        page += 1
        time.sleep(0.3)
    print(f"Enumerated {len(all_herbs)} herbs from SymMap browse")
    for i, h in enumerate(all_herbs, 1):
        hid = h.get("Herb_id")
        if not hid or hid in idx["herb_to_mols"]:
            continue
        try:
            mols = sm.related(hid, "Mol")
        except SymMapTimeout:
            print(f"  [{i}/{len(all_herbs)}] {hid} TIMEOUT (skip)")
            continue
        mids = []
        for m in mols:
            mid = m.get("MOL_id") or m.get("Mol_id")
            if not mid:
                continue
            mids.append(mid)
            idx["mol_to_herbs"].setdefault(mid, []).append(hid)
            if mid not in idx["herbs"]:
                pass
        idx["herb_to_mols"][hid] = mids
        idx["herbs"][hid] = {"chinese_name": h.get("Chinese_name"),
                             "pinyin": h.get("Pinyin_name"),
                             "latin_name": h.get("Latin_name"),
                             "class": h.get("Class_Chinese")}
        if i % 25 == 0:
            idx["built_at"] = stamp()
            atomic_write_json(idx_path, idx)  # resumable checkpoint
            print(f"  [{i}/{len(all_herbs)}] checkpoint saved "
                  f"({len(idx['herb_to_mols'])} herbs)")
    idx["built_at"] = stamp()
    atomic_write_json(idx_path, idx)
    print(f"Index built: {len(idx['herb_to_mols'])} herbs, "
          f"{len(idx['mol_to_herbs'])} mols -> {idx_path}")
    return idx


def cmd_run_all(args, sm):
    if not os.path.isdir(args.out):
        os.makedirs(args.out, exist_ok=True)
    s1 = step_disease_pathways(args, sm)
    # choose pathway: --pathway or first direct/enriched
    pathway = args.pathway
    if not pathway and s1["pathways"]:
        pathway = s1["pathways"][0]["pathway_id"]
    args.pathway = pathway
    print(f"\nUsing pathway: {pathway}")
    step_pathway_herbs(args, sm)
    # herbs: user-specified or top from step2
    if not args.herbs:
        s2 = load_json(os.path.join(args.out, "step2_pathway_herbs.json"))
        top = [e.get("chinese_name") or e.get("pinyin") for e in (s2 or {}).get("herbs", [])[:args.top_herbs]]
        args.herbs = ",".join(h for h in top if h)
        print(f"\nTop {args.top_herbs} herbs selected: {args.herbs}")
    step_herb_ingredients(args, sm)
    step_cross_validate(args, sm)
    print("\n=== run-all complete ===")
    for f in ("step1_disease_pathways.json", "step2_pathway_herbs.json",
              "step3_herb_ingredients.json", "step4_cross_validation.json"):
        p = os.path.join(args.out, f)
        if os.path.isfile(p):
            print(f"  {f}: {os.path.getsize(p):,} B")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="step", required=True)

    def common(sp):
        sp.add_argument("--out", required=True, help="output/work directory")

    sp = sub.add_parser("run-all", help="four-step pipeline")
    sp.add_argument("--disease", required=True)
    sp.add_argument("--pathway", default=None, help="KEGG pathway id (default: top of step1)")
    sp.add_argument("--herbs", default=None, help="comma herb names (default: top of step2)")
    sp.add_argument("--top-herbs", type=int, default=5)
    sp.add_argument("--ob-cutoff", type=float, default=30.0)
    common(sp)

    sp = sub.add_parser("disease-pathways", help="step 1: disease -> pathways")
    sp.add_argument("--disease", required=True)
    common(sp)

    sp = sub.add_parser("pathway-herbs", help="step 2: pathway -> candidate herbs")
    sp.add_argument("--pathway", required=True)
    sp.add_argument("--disease", default=None,
                    help="optional disease name for symptom-chain channel")
    common(sp)

    sp = sub.add_parser("herb-ingredients", help="step 3: herb -> active ingredients")
    sp.add_argument("--herbs", required=True, help="comma-separated herb names")
    sp.add_argument("--ob-cutoff", type=float, default=30.0)
    common(sp)

    sp = sub.add_parser("cross-validate", help="step 4: pathway cross-validation")
    sp.add_argument("--pathway", default=None, help="default: from step2 output")
    common(sp)

    sp = sub.add_parser("build-index", help="build local herb->mol relation index")
    common(sp)

    args = p.parse_args()
    os.makedirs(args.out, exist_ok=True)
    sm = SymMapClient()
    fn = {
        "run-all": cmd_run_all,
        "disease-pathways": lambda a, s: step_disease_pathways(a, s),
        "pathway-herbs": lambda a, s: step_pathway_herbs(a, s),
        "herb-ingredients": lambda a, s: step_herb_ingredients(a, s),
        "cross-validate": lambda a, s: step_cross_validate(a, s),
        "build-index": lambda a, s: step_build_index(a, s),
    }[args.step]
    fn(args, sm)


if __name__ == "__main__":
    main()
