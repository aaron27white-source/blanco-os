"""Personal → Job Search: applications CRUD and the resumes read from the vault."""

from __future__ import annotations

from datetime import date


def test_starts_empty_with_zero_counts(client):
    body = client.get("/api/jobs").json()
    assert body["applications"] == []
    assert set(body["counts"]) == {"saved", "applied", "interviewing", "offer", "rejected", "withdrawn"}
    assert all(n == 0 for n in body["counts"].values())


def test_saved_has_no_applied_date_until_it_is_applied(client):
    row = client.post("/api/jobs", json={"company": "Encore", "role": "AV Tech"}).json()
    assert row["status"] == "saved" and row["applied_on"] is None
    moved = client.patch(f"/api/jobs/{row['id']}", json={"status": "applied"}).json()
    assert moved["applied_on"] == date.today().isoformat()
    # Moving on doesn't reset the date it went out.
    later = client.patch(f"/api/jobs/{row['id']}", json={"status": "interviewing"}).json()
    assert later["applied_on"] == moved["applied_on"]
    assert client.get("/api/jobs").json()["counts"]["interviewing"] == 1


def test_validation_and_missing_rows(client):
    assert client.post("/api/jobs", json={"company": "   "}).status_code == 422
    assert client.post("/api/jobs", json={"company": "X", "status": "ghosted"}).status_code == 422
    assert client.patch("/api/jobs/999", json={"role": "x"}).status_code == 404
    assert client.delete("/api/jobs/999").status_code == 404


def test_delete(client):
    row = client.post("/api/jobs", json={"company": "Acme", "status": "applied"}).json()
    assert client.delete(f"/api/jobs/{row['id']}").status_code == 204
    assert client.get(f"/api/jobs/{row['id']}").status_code == 404


def test_resumes_listed_from_career_folder(client, tmp_path):
    from app.config import get_settings
    career = get_settings().vault_path / "Personal" / "areas" / "career"
    career.mkdir(parents=True, exist_ok=True)
    (career / "2026-08-11_jane-doe-resume.md").write_text("# resume")
    (career / "cover-letter.md").write_text("not a resume")
    names = [r["name"] for r in client.get("/api/jobs").json()["resumes"]]
    assert names == ["2026-08-11_jane-doe-resume.md"]


def test_model_directive_picks_claude_model_per_turn():
    from app.services.commanders import model_directive
    assert model_directive("@opus write the proposal") == ("opus", "write the proposal")
    assert model_directive("  @Haiku: log it") == ("haiku", "log it")
    assert model_directive("email me at a@sonnet.com") == ("", "email me at a@sonnet.com")
    assert model_directive("@opusx hi") == ("", "@opusx hi")
    assert model_directive("@sonnet") == ("", "@sonnet")
