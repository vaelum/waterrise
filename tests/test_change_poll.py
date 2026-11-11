"""The change streams: cheaper, and still live for what each page shows (p-pw7ft8 s3-4)."""

import http.client
import time

import pytest

from tests.test_planning_service import put, repo, server, state  # noqa: F401 (fixtures)
from tests.test_planning import plan_text
from waterrise import answers, service


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(service.Handler, "EVENT_TICK", 0.05)


def stream(port, query):
    """Open a stream; events are read on a thread, so a test can wait for one
    with a timeout, or see that none came."""
    import queue
    import threading

    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", f"/api/events?{query}", headers={"Host": f"127.0.0.1:{port}"})
    resp = conn.getresponse()
    assert resp.status == 200
    events: queue.Queue = queue.Queue()

    def read():
        name = None
        try:
            while True:
                line = resp.fp.readline().decode()
                if not line:
                    return
                if line.startswith("event: "):
                    name = line[7:].strip()
                elif line == "\n" and name:
                    events.put(name)
                    name = None
        except (OSError, ValueError):
            return
    threading.Thread(target=read, daemon=True).start()

    def next_event(wait: float = 2.0):
        try:
            return events.get(timeout=wait)
        except queue.Empty:
            return None
    assert next_event() == "hello"
    time.sleep(0.15)
    return conn, next_event


def protocol(repo):
    d = repo / "planning" / "protocols" / "p-4k7x2m"
    d.mkdir(parents=True, exist_ok=True)
    return d / "s1-1.md"


def grow(path):
    with path.open("a", encoding="utf-8") as fh:
        fh.write("2026-11-10 12:00:00 note\none more line\n")


def answer(repo):
    answers.update(repo / "planning" / "answers" / "p-4k7x2m.json", "p-4k7x2m",
                   lambda a: a.setdefault("comments", []).append(
                       {"id": "c1", "on": "", "text": "hi", "at": answers.now(), "by": "u"}))


@pytest.mark.parametrize("query", ["scope=repo&slug=demo", "scope=all"])
def test_a_protocol_line_and_an_answer_each_make_a_change(server, repo, query):
    p = protocol(repo)
    p.write_text("2026-11-10 note\nstart\n", encoding="utf-8")
    conn, next_event = stream(server, query)
    grow(p)
    assert next_event() == "change", "a plan page shows its protocols live"
    answer(repo)
    assert next_event() == "change"
    conn.close()


def test_an_index_page_hears_answers_and_new_protocols_but_not_their_growth(server, repo):
    p = protocol(repo)
    p.write_text("2026-11-10 note\nstart\n", encoding="utf-8")
    conn, next_event = stream(server, "scope=index")
    grow(p)
    assert next_event(wait=0.6) is None
    (p.parent / "s1-2.md").write_text("2026-11-10 note\nnew\n", encoding="utf-8")
    assert next_event() == "change"
    answer(repo)
    assert next_event() == "change"
    conn.close()


def test_a_tick_is_shared_but_a_page_always_sees_its_own_write(server, repo):
    e = {"slug": "demo", "path": str(repo), "planning": str(repo / "planning")}
    first = service._tick_signature(e)
    put(repo, "draft/a.toml", plan_text().replace('title = "Monitoring"', 'title = "Renamed"'))
    assert service._signature(e) != first, "a request reads the signature itself"
    assert service.item_view(e, "p-4k7x2m")["data"]["title"] == "Renamed"
