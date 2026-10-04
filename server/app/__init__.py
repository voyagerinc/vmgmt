"""Management Server application package."""
__version__ = "4.1.0"


def get_build() -> str | None:
    """A build id that changes on every release. Read from the baked app/BUILD file (shipped in
    the bundle/exe) so it works on non-git clients; falls back to the git short commit in dev."""
    import subprocess
    from pathlib import Path
    here = Path(__file__).resolve().parent
    bf = here / "BUILD"
    if bf.exists():
        try:
            v = bf.read_text(encoding="utf-8").strip()
            if v:
                return v
        except Exception:
            pass
    try:
        r = subprocess.run(["git", "-C", str(here.parent.parent), "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    return None
