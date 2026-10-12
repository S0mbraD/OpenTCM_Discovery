#!/usr/bin/env python3
"""Shared SymMap v2.0 API client (search / related_components / related_enrichment).

Live-tested endpoints (2026-10-10):
  POST /search_th/            {name, table_name}          -> typeahead name list
  POST /search/               {table_name, key}           -> entity records (with ids)
  POST /related_components/   {rrid, table_name, filter}  -> related entities
       (table_name = TARGET table; filter '0'=All,'1'=P<0.05,'2'=FDR<0.05,'3'=Bonferroni)
  POST /related_enrichment/   {genes: json {'gene': [...]}} -> KEGG pathway enrichment
  GET  /static/download/V2.0/...xlsx                      -> full entity tables

Table names: Herb / Mol / TCM_symptom / MM_symptom / Gene / Disease / Syndrome
Relation map (SymMap native): Herb<->[Mol,TCM_symptom,MM_symptom,Syndrome];
  Mol<->[Herb,Gene]; Gene<->[Mol,Disease]; Disease<->[MM_symptom,Gene];
  TCM_symptom<->[Herb,MM_symptom,Syndrome]; MM_symptom<->[TCM_symptom,Disease]

Known quirk: related_components with very large result sets (Gene->Mol for hub
genes, Mol->Herb for poly-pharmacology molecules) returns HTTP 502 (backend
timeout). The client retries with backoff, then raises TimeoutError-like errors
the caller can catch and skip.

Field-name quirk: search results use 'Mol_id'/'MOL_id' inconsistently across
endpoints; use helpers `first_id(row)`.

Usage as a module:
    from symmap_client import SymMapClient
    sm = SymMapClient()
    hits = sm.search("Disease", "ulcerative colitis")
    genes = sm.related(hits[0]["Disease_id"], "Gene")
    enr = sm.enrichment(["NFKB1", "RELA", "JUN"])
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://symmap.org"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

TABLES = ("Herb", "Mol", "TCM_symptom", "MM_symptom", "Gene", "Disease", "Syndrome")


class SymMapError(RuntimeError):
    pass


class SymMapTimeout(SymMapError):
    """502-style backend timeout (large result set)."""


class SymMapClient:
    def __init__(self, base=BASE, timeout=120, retries=3, backoff=6.0, delay=0.4):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.delay = delay  # polite inter-request delay
        self._last = 0.0

    # ------------------------------------------------------------------ core
    def _post(self, path, params):
        self._throttle()
        data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(self.base + path, data=data, headers=UA)
        last = None
        for attempt in range(self.retries):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return r.read().decode("utf-8", errors="replace")
            except urllib.error.HTTPError as e:
                last = e
                if e.code == 502:
                    time.sleep(self.backoff * (attempt + 1))
                    continue
                raise SymMapError(f"HTTP {e.code} on {path} {params}")
            except (urllib.error.URLError, TimeoutError) as e:
                last = e
                time.sleep(self.backoff * (attempt + 1))
        raise SymMapTimeout(f"SymMap timeout after {self.retries} retries: "
                            f"{path} {params} ({last})")

    def _throttle(self):
        wait = self.delay - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()

    # ---------------------------------------------------------------- public
    def typeahead(self, name, table):
        """Name suggestions (autocomplete). Returns list of strings."""
        out = json.loads(self._post("/search_th/", {"name": name, "table_name": table}))
        return out or []

    def search(self, table, key):
        """Full search. Returns list of dict records."""
        out = json.loads(self._post("/search/", {"table_name": table, "key": key}))
        return out.get("data", []) or []

    def related(self, rrid, table, filter_="0", allow_timeout=False):
        """Related components: entities of `table` linked to entity `rrid`."""
        try:
            out = json.loads(self._post("/related_components/",
                                        {"rrid": rrid, "table_name": table,
                                         "filter": filter_}))
            return out.get("data", []) or []
        except SymMapTimeout:
            if allow_timeout:
                return []
            raise

    def enrichment(self, gene_symbols):
        """KEGG pathway enrichment for a gene-symbol list (SymMap server-side).

        Returns dict with keys: enrichment, GO_mf, GO_cc, GO_bp (each has
        data/columns). Gene symbols must be plain (no trailing ';').
        """
        genes = [g.rstrip(";").strip() for g in gene_symbols if g and g.strip()]
        if not genes:
            return {}
        body = self._post("/related_enrichment/",
                          {"genes": json.dumps({"gene": genes})})
        return json.loads(body)

    def enrichment_pathways(self, gene_symbols):
        """Convenience: KEGG pathway enrichment rows sorted by p-value."""
        res = self.enrichment(gene_symbols)
        rows = (res.get("enrichment") or {}).get("data") or []
        rows = [r for r in rows if r]
        rows.sort(key=lambda r: (r[3] if isinstance(r[3], (int, float)) else 1))
        return rows


def first_id(row):
    """Extract the primary SymMap id from a record dict (field name varies)."""
    for k, v in row.items():
        if k.endswith("_id") and v:
            return v
    return None


def symmap_id_prefix(table):
    return {"Herb": "SMHB", "Mol": "SMIT", "TCM_symptom": "SMTS",
            "MM_symptom": "SMMS", "Gene": "SMTT", "Disease": "SMDE",
            "Syndrome": "SMSY"}[table]


if __name__ == "__main__":
    import sys

    sm = SymMapClient()
    table = sys.argv[1] if len(sys.argv) > 1 else "Disease"
    key = sys.argv[2] if len(sys.argv) > 2 else "ulcerative colitis"
    hits = sm.search(table, key)
    print(f"{len(hits)} hits for {key!r} in {table}")
    for h in hits[:5]:
        print(json.dumps(h, ensure_ascii=False)[:200])
