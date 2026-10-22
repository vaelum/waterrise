"""Reports: the kind, its sidecar and document, and what check says about them."""

import json
import tomllib
from pathlib import Path

import pytest

from tests.test_planning import plan_text, put, run
from waterrise import check, folder


@pytest.fixture
def proj(tmp_path):
    from tests.test_planning import make_project

    root = make_project(tmp_path)
    assert run(root, "init") == 0
    put(root, "draft/a.toml", plan_text())
    return root


def new_report(root: Path, name: str = "nas-throughput") -> tuple[Path, Path, str]:
    assert run(root, "new", "report", name) == 0
    toml = root / "planning" / "reports" / f"{name}.toml"
    doc = toml.with_suffix(".html")
    return toml, doc, tomllib.loads(toml.read_text(encoding="utf-8"))["id"]


def findings(root: Path) -> list[tuple[str, str, str]]:
    rep = check.run(folder.read(root / "planning", root), git=False)
    return [(f.file, f.level, f.message) for f in rep.findings if f.level != "info"]


def test_new_report_makes_the_pair_and_the_template_passes(proj):
    toml, doc, rid = new_report(proj)
    assert rid.startswith("h-") and len(rid) == 8
    assert doc.read_text(encoding="utf-8").lstrip().lower().startswith("<!doctype html")
    assert "<title>Nas throughput</title>" in doc.read_text(encoding="utf-8")
    assert findings(proj) == []


def test_a_report_about_a_plan(proj):
    toml, doc, rid = new_report(proj)
    toml.write_text(toml.read_text(encoding="utf-8").replace('# about = "p-xxxxxx"', 'about = "p-4k7x2m"'), encoding="utf-8")
    assert findings(proj) == []
    toml.write_text(toml.read_text(encoding="utf-8").replace('about = "p-4k7x2m"', 'about = "p-zzzzzz"'), encoding="utf-8")
    assert any("no item has that id" in m for _, _, m in findings(proj))


def test_a_report_is_not_about_a_note(proj):
    assert run(proj, "new", "note", "n") == 0
    nid = tomllib.loads((proj / "planning" / "notes" / "n.toml").read_text(encoding="utf-8"))["id"]
    toml, doc, rid = new_report(proj)
    toml.write_text(toml.read_text(encoding="utf-8").replace('# about = "p-xxxxxx"', f'about = "{nid}"'), encoding="utf-8")
    assert any("is a note" in m for _, _, m in findings(proj))


def test_a_missing_document_is_an_error(proj):
    toml, doc, rid = new_report(proj)
    doc.unlink()
    assert ("reports/nas-throughput.toml", "error", "its document reports/nas-throughput.html does not exist") \
        in findings(proj)


@pytest.mark.parametrize("snippet", [
    '<script src="https://cdn.example.org/chart.js"></script>',
    '<script src="//cdn.example.org/chart.js"></script>',
    '<link rel="stylesheet" href="https://fonts.example.org/x.css">',
    '<img src="http://example.org/a.png">',
    '<style>body { background: url("https://example.org/bg.png") }</style>',
    '<style>@import url(https://example.org/x.css);</style>',
])
def test_a_remote_resource_is_an_error(proj, snippet):
    toml, doc, rid = new_report(proj)
    doc.write_text(doc.read_text(encoding="utf-8").replace("</main>", snippet + "\n</main>"), encoding="utf-8")
    errs = [m for f, lvl, m in findings(proj) if lvl == "error"]
    assert any("loads something from another origin" in m for m in errs), errs


def test_a_link_and_inline_script_are_fine(proj):
    toml, doc, rid = new_report(proj)
    doc.write_text(doc.read_text(encoding="utf-8").replace(
        "</main>", '<a href="https://example.org/source">the source</a>\n'
                   '<script>document.title += " ✓";</script>\n<img src="data:image/png;base64,AAAA">\n</main>'), encoding="utf-8")
    assert findings(proj) == []


@pytest.mark.parametrize("secret", [
    "ghp_" + "a" * 36, "AKIA" + "ABCDEFGHIJKLMNOP", 'api_key = "s3cr3t-value-123"',
    "-----BEGIN OPENSSH PRIVATE KEY-----",
])
def test_a_credential_looking_string_is_an_error(proj, secret):
    toml, doc, rid = new_report(proj)
    doc.write_text(doc.read_text(encoding="utf-8").replace("</main>", f"<p>{secret}</p>\n</main>"), encoding="utf-8")
    assert any("looks like a credential" in m for _, _, m in findings(proj))


def test_not_html_is_an_error(proj):
    toml, doc, rid = new_report(proj)
    doc.write_text("just text\n", encoding="utf-8")
    assert any("is not an HTML document" in m for _, _, m in findings(proj))


def test_a_document_without_a_sidecar_is_a_warning(proj):
    (proj / "planning" / "reports").mkdir()
    (proj / "planning" / "reports" / "loose.html").write_text("<!doctype html><title>x</title>", encoding="utf-8")
    assert ("reports/loose.html", "warn",
            "a report's document with no sidecar (`waterrise new report` makes the pair)") in findings(proj)


def test_a_report_lives_in_reports(proj):
    toml, doc, rid = new_report(proj)
    put(proj, "draft/r.toml", toml.read_text(encoding="utf-8").replace(rid, "h-aaaaaa"))
    assert any("a report belongs in reports" in m for _, _, m in findings(proj))


def test_check_names_reports_only_when_there_are_some(proj, capsys):
    assert run(proj, "check") == 0
    assert "reports" not in capsys.readouterr().out
    new_report(proj)
    capsys.readouterr()
    assert run(proj, "check") == 0
    assert "notes 0 · reports 1" in capsys.readouterr().out


def test_list_shows_reports(proj, capsys):
    toml, doc, rid = new_report(proj)
    capsys.readouterr()
    assert run(proj, "list", "--json") == 0
    rows = json.loads(capsys.readouterr().out)
    assert {"item": rid, "kind": "report", "folder": "reports"}.items() <= next(r for r in rows if r["item"] == rid).items()


@pytest.fixture
def served(proj, tmp_path, monkeypatch):
    import threading

    from waterrise import client, service

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    srv = service.make_server(0, client.state_dir())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    status, entry = client.call(port, "POST", "/api/repos",
                                {"path": str(proj), "planning": str(proj / "planning"), "name": "demo"}, auth=True)
    assert status == 200
    yield port
    srv.shutdown()
    srv.server_close()


def test_the_document_is_served_sandboxed_with_nothing_remote(proj, served):
    from tests.test_planning_service import req

    toml, doc, rid = new_report(proj)
    status, body, headers = req(served, "GET", f"/r/demo/{rid}/report.html")
    assert status == 200 and body.lstrip().lower().startswith(b"<!doctype html")
    csp = headers["Content-Security-Policy"]
    assert csp.startswith("sandbox allow-scripts;") and "allow-same-origin" not in csp
    assert "default-src 'none'" in csp and "frame-ancestors 'self'" in csp and "connect-src" not in csp
    assert headers["Referrer-Policy"] == "no-referrer"
    # Not an item that is a report, or a report without its document: 404.
    assert req(served, "GET", "/r/demo/p-4k7x2m/report.html")[0] == 404
    doc.unlink()
    assert req(served, "GET", f"/r/demo/{rid}/report.html")[0] == 404


def test_a_plan_lists_its_reports_and_a_report_names_its_plan(proj, served):
    from tests.test_planning_service import req

    toml, doc, rid = new_report(proj)
    toml.write_text(toml.read_text(encoding="utf-8").replace('# about = "p-xxxxxx"', 'about = "p-4k7x2m"'), encoding="utf-8")
    status, plan, _ = req(served, "GET", "/api/r/demo/item/p-4k7x2m")
    assert [r["id"] for r in plan["reports"]] == [rid]
    status, rep, _ = req(served, "GET", f"/api/r/demo/item/{rid}")
    assert rep["about"] == {"id": "p-4k7x2m", "title": "Monitoring"}
    assert rep["document"].startswith(f"/r/demo/{rid}/report.html?v=")
    status, repo_view, _ = req(served, "GET", "/api/r/demo")
    assert next(i for i in repo_view["items"] if i["id"] == rid)["about"] == "p-4k7x2m"
