"""python -m papertrade cycle|premarket|intraday|context|site|weekly|status [--only day,swing]"""
import sys

from . import core


def main(argv):
    cmd = argv[0] if argv else "status"
    only = None
    if "--only" in argv:
        only = argv[argv.index("--only") + 1].split(",")
    if cmd == "cycle" and "--auto" in argv:  # every 5 min: Dax always, everyone else when ~15 min have passed
        from .run import due
        only = due()
    if cmd == "cycle":
        from .run import cycle
        for tid, cash, pv, eq in cycle(only):
            print(f"{tid:13} cash {cash:10,.2f}  positions {pv:10,.2f}  equity {eq:10,.2f}")
    elif cmd == "deep":
        from .run import scan
        out = scan("deep")
        print("deep done:", {k: len(v) for k, v in out.get("lists", {}).items()})
    elif cmd in ("premarket", "intraday"):
        from .run import scan
        out = scan(cmd)
        print(cmd, "done:", {k: len(v) for k, v in out.get("lists", {}).items()} if cmd == "premarket" else len(out.get("rows", [])))
    elif cmd == "context":
        from .meeting import context
        print(context())
    elif cmd == "settings":  # python -m papertrade settings <file.json> [by]
        import json
        from .settings import apply_doc
        print(apply_doc(json.loads(open(argv[1]).read()), argv[2] if len(argv) > 2 else "you"))
    elif cmd == "site":
        from .site import build
        print(build())
    elif cmd == "scoreboard":
        from .run import scoreboard
        scoreboard()
    elif cmd == "review":  # Saturday review: apply review + bonus + PDF
        from .weekly import review
        print(review(argv[1] if len(argv) > 1 else None))
    elif cmd == "weekly":
        from .weekly import build
        print(build())
    else:
        acc = core.load_accounts()
        for tid, a in [(k, v) for k, v in acc.items() if not k.startswith("_")]:
            print(tid, round(a["cash"], 2), len(a["positions"]), "positions", len(a["pending"]), "pending")


if __name__ == "__main__":
    main(sys.argv[1:])
