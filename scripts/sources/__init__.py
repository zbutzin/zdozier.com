"""One module per data source. Each exposes NAME and fetch(ctx) -> (payload, count).

ctx is a dict with keys: site (content/site.yml), ident (identity config),
http (common.Http), prev (previous raw payload or None), raw (payloads
fetched or cached earlier in this run, keyed by source name), pubs
(content/publications.yml), now (ISO timestamp), options (CLI flags).
"""

ORDER = ["orcid", "openalex", "europepmc", "crossref", "reporter", "github"]
