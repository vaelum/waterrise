"""A plan's timeline: its [[log]] and every step's notes, newest first (p-pw7ft8 s7-2)."""

from pathlib import Path

from waterrise import protocol, service
from waterrise.folder import Item

PROTO = """
## 2026-11-10 09:00:00 note
starting s1

## 2026-11-10 09:01:00 run
~~~~~~
$ true
~~~~~~
exit 0 after 0.1 s

## 2026-11-10 09:05:00 note
s1 done
"""


def write(planning: Path, step: str, text: str) -> Path:
    p = planning / "protocols" / "p-aaaaaa" / f"{step}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_notes_are_every_note_oldest_first_and_runs_are_left_out(tmp_path):
    write(tmp_path, "s1", PROTO)
    write(tmp_path, "s2", "\n## 2026-11-10 08:00:00 note\nearlier, on s2\n")
    got = protocol.notes(tmp_path, "p-aaaaaa")
    assert [(n["step"], n["text"]) for n in got] == [("s2", "earlier, on s2"), ("s1", "starting s1"), ("s1", "s1 done")]


def test_notes_follow_a_protocol_that_grows(tmp_path):
    p = write(tmp_path, "s1", PROTO)
    assert len(protocol.notes(tmp_path, "p-aaaaaa")) == 2
    with p.open("a", encoding="utf-8") as fh:
        fh.write("\n## 2026-11-10 09:10:00 note\none more\n")
    assert protocol.notes(tmp_path, "p-aaaaaa")[-1]["text"] == "one more"


def test_a_long_note_is_cut_in_the_timeline(tmp_path):
    write(tmp_path, "s1", "\n## 2026-11-10 09:00:00 note\n" + "x" * 5000 + "\n")
    text = protocol.notes(tmp_path, "p-aaaaaa")[0]["text"]
    assert len(text) <= protocol.NOTE_CHARS + 2 and text.endswith("…")


def plan(log):
    return Item(path=Path("x.toml"), rel="x.toml", folder="in-progress", data={"log": log}, parsed=True)


def test_the_timeline_puts_a_log_entry_at_its_steps_last_note_and_newest_first(tmp_path):
    write(tmp_path, "s1", PROTO)
    notes = protocol.notes(tmp_path, "p-aaaaaa")
    rows, older = service.timeline(plan([
        {"on": "2026-11-09", "text": "the day before"},
        {"on": "2026-11-10", "step": "s1", "text": "s1 summary", "commit": "abc1234"},
        {"on": "2026-11-10", "text": "after s1, no step"},
    ]), notes)
    assert older == 0
    assert [(r["kind"], r["text"]) for r in rows] == [
        ("log", "after s1, no step"),        # just after the entry above it in the file
        ("log", "s1 summary"),               # at s1's last note, and above it
        ("note", "s1 done"),
        ("note", "starting s1"),
        ("log", "the day before"),
    ]
    assert rows[1]["at"] == "2026-11-10 09:05:00" and rows[1]["commit"] == "abc1234"


def test_the_timeline_keeps_the_newest_and_counts_the_rest(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "TIMELINE", 3)
    write(tmp_path, "s1", PROTO)
    rows, older = service.timeline(plan([{"on": "2026-11-10", "step": "s1", "text": "done"}]),
                                   protocol.notes(tmp_path, "p-aaaaaa"))
    assert len(rows) == 3 and older == 0       # two notes and the log: all of them
    rows, older = service.timeline(plan([{"on": "2026-11-09", "text": "a"}, {"on": "2026-11-09", "text": "b"}]),
                                   protocol.notes(tmp_path, "p-aaaaaa"))
    assert len(rows) == 3 and older == 1
