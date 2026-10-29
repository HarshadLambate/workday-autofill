from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
from pathlib import Path

import yaml

from . import __version__
from .answer_bank import AnswerBank
from .jobs import parse_jobs, read_job_lines
from .llm import make_llm
from .resolver import Resolver
from .templates import ANSWERS, COMPANIES, INSTRUCTIONS, JOBS, PROFILE


# ---------------------------------------------------------------- helpers
def _load_profile(folder: Path) -> tuple[dict, Path] | None:
    path = folder / "profile.yaml"
    if not path.exists():
        print(f"{path} not found. Run `wdautofill init --dir {folder}` first.", file=sys.stderr)
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}, path
    except yaml.YAMLError as exc:
        _yaml_help(path, exc)
        return None


def _yaml_help(path: Path, exc: Exception) -> None:
    """A short, readable message instead of a YAML traceback."""
    mark = getattr(exc, "problem_mark", None)
    where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
    print(f"\n{path} has a formatting mistake{where}:", file=sys.stderr)
    if mark:
        lines = path.read_text(encoding="utf-8").splitlines()
        for n in range(max(0, mark.line - 2), min(len(lines), mark.line + 2)):
            print(f"  {'>' if n == mark.line else ' '} {n + 1:4} | {lines[n]}", file=sys.stderr)
    print(f"  ({getattr(exc, 'problem', exc)})\n", file=sys.stderr)
    print("Common fixes:\n"
          "  - Text over several lines needs '|' and indented lines:\n"
          "        description: |\n"
          "          - Built a Playwright framework\n"
          "          - Led CI/CD for 5 teams\n"
          "  - Text containing ': ' or starting with - [ { * & ! # needs quotes: \"Notice: 90 days\"\n"
          "  - Use spaces, not tabs, and keep items of a list at the same indentation.", file=sys.stderr)


def _file(folder: Path, value: str | None) -> Path | None:
    if not value:
        return None
    p = Path(value).expanduser()
    return (p if p.is_absolute() else folder / p).resolve()


def _already_filled(log: Path) -> dict[str, str]:
    done: dict[str, str] = {}
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("status") == "ready_for_review" and rec.get("url"):
                done[rec["url"]] = rec.get("time", "")[:10]
    return done


def _paste_links(jobs_file: Path) -> None:
    print("Paste Workday job links (one per line, as many as you like).")
    print("Press Enter on an empty line to start:")
    lines = []
    while True:
        try:
            line = input().strip()
        except EOFError:
            break
        if not line:
            break
        lines.append(line)
    if lines:
        with jobs_file.open("a", encoding="utf-8") as fh:
            fh.write("\n" + "\n".join(lines) + "\n")
        print(f"Saved {len(lines)} link(s) to {jobs_file.name}.")


# ---------------------------------------------------------------- commands
def _init(args: argparse.Namespace) -> int:
    target = Path(args.dir)
    target.mkdir(parents=True, exist_ok=True)
    for name, content in (("profile.yaml", PROFILE), ("answers.yaml", ANSWERS),
                          ("instructions.md", INSTRUCTIONS), ("jobs.txt", JOBS),
                          ("companies.txt", COMPANIES)):
        path = target / name
        if path.exists():
            print(f"skip   {path} (already exists)")
        else:
            path.write_text(content, encoding="utf-8")
            print(f"wrote  {path}")
    print(f"\nNext: edit {target / 'profile.yaml'} and instructions.md, put your resume")
    print("(and cover letter template) in that folder, then:")
    print(f"  wdautofill search --dir {args.dir}     # find and shortlist jobs")
    print(f"  wdautofill apply  --dir {args.dir}     # fill everything in jobs.txt")
    return 0


def _answers(args: argparse.Namespace) -> int:
    bank = AnswerBank(Path(args.dir) / "answers.yaml")
    if not bank.entries:
        print("No saved answers yet.")
    for e in bank.entries:
        flag = " [sensitive]" if e.sensitive else ""
        print(f"- ({e.scope}){flag} {e.question}\n    -> {e.answer}")
    return 0


def _apply(args: argparse.Namespace) -> int:
    folder = Path(args.dir).resolve()
    loaded = _load_profile(folder)
    if loaded is None:
        return 1
    profile, _ = loaded

    jobs_file = _file(folder, args.file) or folder / "jobs.txt"
    if not args.urls and not read_job_lines(jobs_file.read_text(encoding="utf-8") if jobs_file.exists() else ""):
        _paste_links(jobs_file)
    jobs = parse_jobs(args.urls, str(jobs_file), args.country)
    log = folder / "applications.jsonl"
    done = {} if args.again else _already_filled(log)
    skipped = [j for j in jobs if j.url in done]
    for j in skipped:
        print(f"skip   {j.url} (already filled on {done[j.url]}; use --again to redo)")
    jobs = [j for j in jobs if j.url not in done]
    if not jobs:
        print(f"Nothing new to fill. Add Workday job links to {jobs_file} (or run `wdautofill search`).")
        return 0

    # ---- documents
    resume_path = _file(folder, args.resume or profile.get("resume_path"))
    if resume_path and not resume_path.exists():
        print(f"!! Resume NOT found at {resume_path} - it will not be uploaded.")
        resume_path = None
    print(f"Resume:       {resume_path or '(none - set resume_path in profile.yaml)'}")
    cover = _file(folder, args.cover_letter or profile.get("cover_letter_path"))
    if cover and not cover.exists():
        print(f"!! Cover letter template NOT found at {cover}.")
        cover = None
    print(f"Cover letter: {cover or '(none - set cover_letter_path in profile.yaml)'}"
          + ("  -> a copy per job in cover_letters/" if cover and cover.suffix.lower() in {'.docx', '.txt', '.md'} else ""))

    instructions = ""
    ipath = _file(folder, args.instructions or profile.get("instructions_path", "instructions.md"))
    if ipath and ipath.exists():
        instructions = ipath.read_text(encoding="utf-8")
        print(f"Instructions: {ipath}")

    # ---- sign-in
    password = None
    if not args.no_auto_account:
        password = os.environ.get("WDA_PASSWORD")
        if not password:
            password = getpass.getpass(
                "Workday password to use on every company site (typing is hidden; Enter = sign in yourself): "
            ) or None

    cfg = profile.get("llm") or {}
    llm = make_llm(cfg.get("provider"), cfg.get("model", ""), cfg.get("base_url"), cfg.get("api_key_env"))
    if instructions and llm is None:
        print("Note: instructions.md is only read by the AI; with llm.provider: none it is ignored.")
    resolver = Resolver(
        profile,
        AnswerBank(folder / "answers.yaml"),
        llm=llm,
        min_confidence=float(cfg.get("min_confidence", 0.8)),
        instructions=instructions,
        suggest_sensitive=bool(cfg.get("suggest_sensitive", True)),
    )

    from .workday import run_jobs  # imported lazily so tests don't need a browser

    print(f"\n{len(jobs)} job(s) queued; up to {args.parallel} windows open at a time.")
    print(f"Add more links to {jobs_file.name} at any time - they join the queue.\n")
    asyncio.run(
        run_jobs(
            jobs,
            resolver,
            browser_dir=Path(args.browser_dir).expanduser(),
            channel=args.channel,
            parallel=args.parallel,
            watch_file=jobs_file,
            skip_urls=set(done),
            resume_path=resume_path,
            cover_letter_template=cover,
            cover_letter_dir=folder / "cover_letters",
            password=password,
            overwrite=args.overwrite,
            resume_autofill=not args.no_resume_autofill,
            auto_account=password is not None,
            log_path=log,
            debug_dir=folder / "debug",
        )
    )
    return 0


def _search(args: argparse.Namespace) -> int:
    from .search import parse_picks, read_sites, search

    folder = Path(args.dir).resolve()
    loaded = _load_profile(folder)
    if loaded is None:
        return 1
    profile, _ = loaded
    cfg = profile.get("search") or {}
    keywords = [k.strip() for k in (args.keywords.split(",") if args.keywords else cfg.get("keywords") or []) if k.strip()]
    location = args.location if args.location is not None else cfg.get("location", "")
    days = args.days if args.days is not None else cfg.get("max_days", 30)
    companies = _file(folder, args.companies or cfg.get("companies_file", "companies.txt"))
    if not keywords:
        print("Give keywords: --keywords \"QA, SDET\" or search.keywords in profile.yaml", file=sys.stderr)
        return 1
    if not companies or not companies.exists():
        print(f"Companies list not found: {companies}", file=sys.stderr)
        return 1
    sites = read_sites(companies)
    print(f"Searching {len(sites)} Workday career sites for {keywords} in '{location or 'any location'}', "
          f"posted in the last {days} days...")
    failed = []

    def log(msg: str) -> None:
        print(msg)
        if msg.lstrip().startswith("!"):
            failed.append(msg)

    posts = search(sites, keywords, location, cfg.get("skills") or [], cfg.get("years_experience"),
                   days, exclude=cfg.get("exclude") or [], log=log)
    if sites and len(failed) == len(sites):
        print("Could not reach any career site. Check your internet connection (or proxy) and try again.")
        return 1

    jobs_file = folder / "jobs.txt"
    have = {u for u, _ in read_job_lines(jobs_file.read_text(encoding="utf-8"))} if jobs_file.exists() else set()
    have |= set(_already_filled(folder / "applications.jsonl"))
    new = [p for p in posts if p.url not in have][: args.top]
    if not new:
        print("No new matching jobs. Try more companies, other keywords or --days 60.")
        return 0

    print(f"\n{len(new)} new match(es), best first:\n")
    for i, p in enumerate(new, 1):
        age = "" if p.posted_days is None else f"{p.posted_days}d ago"
        notes = f"  [{', '.join(p.notes)}]" if p.notes else ""
        print(f"{i:>3}. {p.score:>3}%  {p.company[:22]:<22} {p.title[:60]}")
        print(f"         {p.locations[:70]}  {age}{notes}")
        if p.matched:
            print(f"         skills: {', '.join(p.matched[:8])}")
        print(f"         {p.url}")
    raw = input("\nWhich ones to apply for? (e.g. 1,3,5-8 / all / Enter = none): ")
    picks = parse_picks(raw, len(new))
    if not picks:
        print("Nothing added.")
        return 0
    with jobs_file.open("a", encoding="utf-8") as fh:
        for i in picks:
            p = new[i]
            fh.write(f"{p.url}  # {p.score}% {p.company} - {p.title}\n")
    print(f"Added {len(picks)} job(s) to {jobs_file}.")
    if input("Start filling them now? [Y/n] ").strip().lower() not in {"n", "no"}:
        return _apply(argparse.Namespace(**{**vars(_apply_defaults()), "dir": args.dir}))
    return 0


# ---------------------------------------------------------------- parser
def _add_apply_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("urls", nargs="*", help="Workday job URLs (optional - default: jobs.txt)")
    p.add_argument("--dir", default=".", help="folder with profile.yaml, answers, jobs.txt (default: here)")
    p.add_argument("--file", help="job list file (default: jobs.txt in --dir)")
    p.add_argument("--parallel", type=int, default=5, help="job windows open at once (default 5)")
    p.add_argument("--country", help="country of ALL these jobs (US, UK, IN...). Default: read from each posting")
    p.add_argument("--again", action="store_true", help="also redo jobs filled before")
    p.add_argument("--resume", help="resume file (default: resume_path in profile)")
    p.add_argument("--cover-letter", help="cover letter template (default: cover_letter_path in profile)")
    p.add_argument("--instructions", help="instructions file (default: instructions.md)")
    p.add_argument("--no-auto-account", action="store_true", help="never type email/password; sign in yourself")
    p.add_argument("--overwrite", action="store_true", help="also overwrite fields Workday pre-filled")
    p.add_argument("--no-resume-autofill", action="store_true", help="don't use 'Autofill with Resume'")
    p.add_argument("--browser-dir", default="~/.wdautofill/browser", help="persistent browser profile")
    p.add_argument("--channel", help="use an installed browser, e.g. chrome or msedge")


def _apply_defaults() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    _add_apply_args(p)
    return p.parse_args([])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="wdautofill",
        description="Find Workday jobs and fill the applications. You review and click Submit.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="create profile.yaml, answers, instructions, jobs and companies files")
    p_init.add_argument("--dir", default=".")
    p_init.set_defaults(func=_init)

    p_ans = sub.add_parser("answers", help="list saved answers")
    p_ans.add_argument("--dir", default=".")
    p_ans.set_defaults(func=_answers)

    p_search = sub.add_parser("search", help="find matching jobs on Workday career sites and shortlist them")
    p_search.add_argument("--dir", default=".")
    p_search.add_argument("--keywords", help='comma-separated, e.g. "QA, SDET, Test Automation"')
    p_search.add_argument("--location", help="e.g. Pune ('' = anywhere)")
    p_search.add_argument("--days", type=int, help="only jobs posted in the last N days")
    p_search.add_argument("--companies", help="file of Workday career-site URLs (default companies.txt)")
    p_search.add_argument("--top", type=int, default=40, help="show at most N matches")
    p_search.set_defaults(func=_search)

    p_apply = sub.add_parser("apply", help="fill Workday applications (queue: jobs.txt, links, or paste)")
    _add_apply_args(p_apply)
    p_apply.set_defaults(func=_apply)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
