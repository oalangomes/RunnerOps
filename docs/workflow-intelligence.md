# Workflow Intelligence — #149 (P1)

**Boundary:** read-only deterministic signals with optional existing AI reviewer.

    runnerctl workflow intelligence . --since 24h --json
    runnerctl workflow intelligence --evidence report.json --json
    runnerctl workflow intelligence --evidence report.json --provider ollama --model qwen3.5:9b --json
    runnerctl workflow intelligence --evidence report.json --provider litellm --model MODEL --allow-remote --json

Live mode reuses the OperationalEvidence v1 report builder. Replay mode reads
only the explicitly provided bounded JSON (256 KiB max), **without network
collection**. schema_version/kind/essential sections are validated.

Deterministic signals: queued jobs observed in the current snapshot; failed
autoscale action records from the available bounded history; exact reasons
from incomplete_evidence. Output retains period, CI queue-age summary,
autoscale decision/action summaries and provenance class OBSERVED or UNKNOWN.
No unavailable CI history, p95 or postulated savings may be presented as
measured trends. The existing review subsystem is the only LLM gateway.
It validates prompt/schema/JSON pointers and requires explicit provider/model;
remote Ollama or LiteLLM is subject to its consent checks. It cannot perform
runner actions or rewrite workflow YAML.

The existing OperationalEvidence v1 limitation is still explicit:
CI history is not persisted and historical capacity/utilization may be absent.
Those gaps must appear in inconclusive signals, not be masked as zero.

Exit 0 means complete evidence for this projection; 3 means bounded but
incomplete evidence; 2 means failed validation/model review.

    python3 -B -m unittest tests.workflows.test_intelligence
