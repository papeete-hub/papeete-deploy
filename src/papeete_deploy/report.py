"""The shared report style — ok / note / warn / FAIL, as the wider Papeete ecosystem established it.

DUPLICATED FROM papeete-product ON PURPOSE, not imported. `papeete-deploy` is a standalone
package — a few duplicated lines here is the cost of that, deliberately paid, rather than taking
on a runtime dependency for one small dataclass.
"""
import sys
from dataclasses import dataclass, field


@dataclass
class Report:
    """Findings from one gate run. `errors` fail the run; `notes` and `warns` never do."""

    oks: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    warns: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def merge(self, other: "Report") -> "Report":
        self.oks += other.oks
        self.notes += other.notes
        self.warns += other.warns
        self.errors += other.errors
        return self

    def emit(self, summary: str) -> int:
        for o in self.oks:
            print(f"  ok   {o}")
        for w in self.warns:
            print(f"  warn {w}")
        for n in self.notes:
            print(f"  note {n}")
        for e in self.errors:
            print(f"  FAIL {e}", file=sys.stderr)
        if self.errors:
            print(f"\n{summary}: {len(self.errors)} error(s).", file=sys.stderr)
            return 1
        print(f"\n{summary}: OK ({len(self.oks)} passed, {len(self.warns)} warned, {len(self.notes)} noted).")
        return 0
