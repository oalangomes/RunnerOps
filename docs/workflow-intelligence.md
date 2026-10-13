# Workflow Intelligence — #149 (P1)

**Boundary:** read-only deterministic signals with optional existing AI reviewer.

    runnerctl workflow intelligence . --since 24h --json
    runnerctl workflow intelligence --evidence report.json --json
    runnerctl workflow intelligence --evidence report.json --jobs-evidence jobs.json --json
    runnerctl workflow intelligence --evidence report.json --provider ollama --model qwen3.5:9b --json
    runnerctl workflow intelligence --evidence report.json --provider litellm --model MODEL --allow-remote --json

Live mode reuses the OperationalEvidence v1 report builder. Replay mode reads
only the explicitly provided bounded JSON (256 KiB max), **without network
collection**. schema_version/kind/essential sections are validated.

Optionally supply WorkflowJobsEvidence v1 containing repository=owner/repo,
a bounded list of up to 500 jobs, and each GitHub job's name, status,
conclusion, created_at, started_at and completed_at ISO timestamps. Queue and
execution medians are computed from successful completed jobs only; p95 is
omitted unless there are at least 20 valid observations per job. Missing or
invalid timestamps remain an explicit gap. The file is never sent to a model;
provider review still only receives sanitized OperationalEvidence v1.
An example job evidence document:

    {"schema_version":1,"kind":"WorkflowJobsEvidence","repository":"example/api","jobs":[{"name":"test","status":"completed","conclusion":"success","created_at":"2026-10-10T10:00:00Z","started_at":"2026-10-10T10:00:10Z","completed_at":"2026-10-10T10:02:10Z"}]}

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
