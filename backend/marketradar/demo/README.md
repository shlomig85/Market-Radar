# Development corpus (DEMO)

Everything in `corpus.json` is **synthetic**. It exists so the pipeline can be exercised in
an environment where no live data provider is reachable.

Guardrails, enforced in code and in the database schema — not by convention:

* Every record loads with `data_mode = DEMO`.
* Every company is **fictional** (`is_fictional = true`). A database check constraint makes
  "synthetic evidence about a real ticker" unrepresentable: a `DEMO` company must be
  fictional, and a non-`DEMO` company must not be.
* Every document URL is on a `.invalid` domain, which by RFC 2606 can never resolve. A check
  constraint on `source_documents` rejects a `DEMO` row with any other URL.
* Every publisher is named as synthetic and carries `is_synthetic = true`.

The *phenomenon* described (AI infrastructure driving memory demand, DRAM/HBM/NAND,
memory manufacturing, semiconductor equipment) is a real-world concept. The *issuers,
publishers, statements and numbers* are invented. Nothing here should ever be read as a
claim about a real company or security.

See `docs/decision-log.md` ADR-006 for why the demo universe is fictional rather than a set
of synthetic articles about real tickers.
