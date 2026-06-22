"""Catalog index — flat list of all metrics with fuzzy lookup.

3,880 metrics is too many to embed in the system prompt verbatim, so the
LLM uses the `list_metrics(query)` tool to discover them at run time. This
module loads the YAML manifest once at startup and indexes it for keyword
search.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import yaml


@dataclass(frozen=True)
class CatalogEntry:
    metric_name: str
    description: str
    semantic_model: str
    table_id: str         # ACS column id like 'B01003e1' (uppercased)


@dataclass
class CatalogIndex:
    entries: tuple[CatalogEntry, ...] = field(default_factory=tuple)

    def search(self, query: str, limit: int = 20) -> list[CatalogEntry]:
        """Token-overlap ranked search.

        Cheap, deterministic, no embeddings. The LLM gets ranked candidates;
        if none look right it can broaden the query.
        """
        if not query.strip():
            return list(self.entries[:limit])
        q_tokens = {t for t in _tokens(query) if t}
        if not q_tokens:
            return list(self.entries[:limit])
        scored: list[tuple[float, CatalogEntry]] = []
        margin_in_query = "margin" in q_tokens or "error" in q_tokens
        for e in self.entries:
            haystack = _tokens(f"{e.metric_name} {e.description}")
            raw = sum(1 for t in q_tokens if t in haystack)
            if not raw:
                continue
            # Deprioritize margin-of-error entries unless the query asks for them.
            is_margin = e.description.startswith("[MARGIN OF ERROR]")
            score = raw * (0.3 if is_margin and not margin_in_query else 1.0)
            scored.append((score, e))
        scored.sort(key=lambda x: (-x[0], x[1].metric_name))
        return [e for _, e in scored[:limit]]

    def get(self, metric_name: str) -> CatalogEntry | None:
        return next((e for e in self.entries if e.metric_name == metric_name), None)

    def __len__(self) -> int:
        return len(self.entries)


def _tokens(s: str) -> set[str]:
    return {t for t in s.lower().replace("_", " ").split() if len(t) > 1}


@lru_cache(maxsize=4)
def load_catalog_index(catalog_dir: str) -> CatalogIndex:
    """Build a CatalogIndex from a YAML catalog directory.

    Walks `<catalog_dir>/semantic_models/*.yaml`; each file's measures
    become CatalogEntry rows. Cheap enough to load on every restart.
    """
    root = Path(catalog_dir)
    models_dir = root / "semantic_models"
    entries: list[CatalogEntry] = []
    for yaml_path in sorted(models_dir.glob("*.yaml")):
        doc = yaml.safe_load(yaml_path.read_text())
        sm = doc.get("semantic_model") if doc else None
        if not sm:
            continue
        model_name = sm.get("name", yaml_path.stem)
        for measure in sm.get("measures", []) or []:
            mname = measure.get("name")
            if not mname or mname == "geography_universe":
                continue
            descr = measure.get("description", mname)
            # Prefix of the measure name is the ACS table id (lowercased).
            table_id = mname.split("_", 1)[0].upper()
            entries.append(CatalogEntry(
                metric_name=mname,
                description=descr,
                semantic_model=model_name,
                table_id=table_id,
            ))
    return CatalogIndex(entries=tuple(entries))
