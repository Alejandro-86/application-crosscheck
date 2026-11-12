"""FastAPI backend: check an application against clinical notes and pharmacy rows.

Run:  uvicorn --factory crosscheck.api:create_app --port 8000
The model-based extractor is disabled unless ALLOW_LLM=1 is set, so the page is safe to expose.
"""

import os
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from crosscheck.applications import Application, DeclaredMedication
from crosscheck.compare import Flag
from crosscheck.config import ConfigError
from crosscheck.corpus import LoadedDocument
from crosscheck.costs import BudgetExceededError
from crosscheck.demo_helpers import flag_id, load_demo_data, load_reviews, save_review
from crosscheck.facts import PatientFacts
from crosscheck.pharmacy import PharmacyRow
from crosscheck.pipeline import Extractor, check_application
from crosscheck.rules_extractor import RuleExtractor
from crosscheck.terminology import normalise_allergy, normalise_condition, normalise_drug

WEB_DIR = Path(__file__).parent / "web"
ExtractorFactory = Callable[[str], Extractor]


class MedicationIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    dose: int = Field(ge=1, le=5000)
    unit: Literal["mg"] = "mg"


class ApplicationIn(BaseModel):
    name: str = Field(default="Applicant", max_length=100)
    conditions: list[str] = Field(default_factory=list, max_length=20)
    medications: list[MedicationIn] = Field(default_factory=list, max_length=20)
    allergies: list[str] = Field(default_factory=list, max_length=20)


class NoteIn(BaseModel):
    doc_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,20}$")
    text: str = Field(min_length=1, max_length=20000)


class PharmacyRowIn(BaseModel):
    drug: str = Field(min_length=1, max_length=80)
    dose: int = Field(ge=1, le=5000)
    unit: Literal["mg"] = "mg"
    last_dispensed: str = Field(default="unknown", max_length=20)


class CheckRequest(BaseModel):
    app_id: str = Field(default="adhoc", pattern=r"^[A-Za-z0-9_-]{1,40}$")
    application: ApplicationIn
    notes: list[NoteIn] = Field(default_factory=list, max_length=5)
    pharmacy: list[PharmacyRowIn] = Field(default_factory=list, max_length=30)
    extractor: Literal["rules", "llm"] = "rules"

    @model_validator(mode="after")
    def unique_note_ids(self):
        ids = [n.doc_id for n in self.notes if n.doc_id]
        if len(ids) != len(set(ids)):
            raise ValueError("note ids must be unique")
        return self


class ReviewIn(BaseModel):
    flag_id: str = Field(min_length=1, max_length=200)
    decision: Literal["—", "Confirm", "Dismiss"]


def default_extractor_factory(name: str) -> Extractor:
    if name == "rules":
        return RuleExtractor()
    if os.environ.get("ALLOW_LLM") != "1":
        raise PermissionError("The model extractor is disabled. Start the server with ALLOW_LLM=1.")
    from crosscheck.config import get_settings
    from crosscheck.llm import ChatClient
    from crosscheck.llm_extractor import LLMExtractor

    return LLMExtractor(ChatClient(get_settings()))


def _flag_json(app_id: str, flag: Flag) -> dict:
    return {
        "id": flag_id(app_id, flag),
        "type": flag.type,
        "field": flag.field,
        "severity": flag.severity,
        "message": flag.message,
        "declared": flag.declared,
        "checked_docs": flag.checked_docs,
        "evidence": [asdict(e) for e in flag.evidence],
    }


def _facts_json(facts: PatientFacts) -> dict:
    return {
        "conditions": sorted(facts.conditions),
        "negated_conditions": sorted(facts.negated),
        "medications": {
            drug: sorted({dose for dose, _ in found if dose is not None})
            for drug, found in sorted(facts.medications.items())
        },
        "allergies": sorted(facts.allergies),
    }


def _build_inputs(
    request: CheckRequest,
) -> tuple[Application, list[LoadedDocument], list[PharmacyRow], list[str]]:
    unmapped: list[str] = []
    conditions: list[str] = []
    for text in request.application.conditions:
        value = normalise_condition(text)
        if value is None:
            unmapped.append(f"condition: {text}")
        elif value not in conditions:
            conditions.append(value)
    medications: dict[str, DeclaredMedication] = {}
    for med in request.application.medications:
        value = normalise_drug(med.name)
        if value is None:
            unmapped.append(f"medication: {med.name}")
        else:
            medications[value] = DeclaredMedication(value, med.dose, med.unit)
    allergies = sorted({normalise_allergy(a) for a in request.application.allergies if a.strip()})
    app = Application(
        app_id=request.app_id,
        patient_id="adhoc",
        name=request.application.name,
        mrn="adhoc",
        declared_conditions=conditions,
        declared_medications=list(medications.values()),
        declared_allergies=allergies,
    )
    docs = [
        LoadedDocument(n.doc_id or f"N{i + 1}", n.text, "adhoc", "note", [], [])
        for i, n in enumerate(request.notes)
    ]
    rows = []
    for row in request.pharmacy:
        value = normalise_drug(row.drug)
        if value is None:
            unmapped.append(f"pharmacy drug: {row.drug}")
        else:
            rows.append(PharmacyRow("adhoc", value, row.dose, row.unit, row.last_dispensed))
    return app, docs, rows, unmapped


def create_app(
    data_dir: Path = Path("data/generated"),
    extractor_factory: ExtractorFactory | None = None,
    allow_llm: bool | None = None,
) -> FastAPI:
    factory = extractor_factory or default_extractor_factory
    llm_enabled = os.environ.get("ALLOW_LLM") == "1" if allow_llm is None else allow_llm
    data = load_demo_data(data_dir)
    by_id = {a.app_id: a for a in data.applications}
    reviews_path = data_dir / "reviews.json"
    app = FastAPI(
        title="Application cross-check", docs_url="/api/docs", openapi_url="/api/openapi.json"
    )

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "llm_enabled": llm_enabled, "applications": len(by_id)}

    @app.get("/api/applications")
    def list_applications() -> list[dict]:
        return [
            {"app_id": a.app_id, "name": a.name, "planted_errors": len(a.planted_errors)}
            for a in data.applications
        ]

    def _find(app_id: str) -> Application:
        if app_id not in by_id:
            raise HTTPException(404, f"Unknown application {app_id}")
        return by_id[app_id]

    @app.get("/api/applications/{app_id}")
    def get_application(app_id: str) -> dict:
        found = _find(app_id)
        return {
            "app_id": found.app_id,
            "application": {
                "name": found.name,
                "conditions": found.declared_conditions,
                "medications": [asdict(m) for m in found.declared_medications],
                "allergies": found.declared_allergies,
            },
            "notes": [
                {"doc_id": d.doc_id, "text": d.text} for d in data.docs_by_mrn.get(found.mrn, [])
            ],
            "pharmacy": [
                {"drug": r.drug, "dose": r.dose, "unit": r.unit, "last_dispensed": r.last_dispensed}
                for r in data.pharmacy_by_mrn.get(found.mrn, [])
            ],
        }

    @app.get("/api/applications/{app_id}/answer-key")
    def answer_key(app_id: str) -> list[dict]:
        return [asdict(e) for e in _find(app_id).planted_errors]

    @app.post("/api/check")
    def check(request: CheckRequest) -> dict:
        application, docs, rows, unmapped_declared = _build_inputs(request)
        if request.extractor == "llm" and not llm_enabled:
            raise HTTPException(403, "The model extractor is disabled on this server.")
        try:
            extractor = factory(request.extractor)
            result = check_application(application, docs, rows, extractor)
        except PermissionError as error:
            raise HTTPException(403, str(error)) from error
        except ConfigError as error:
            raise HTTPException(503, str(error)) from error
        except BudgetExceededError as error:
            raise HTTPException(429, str(error)) from error
        except Exception as error:
            if request.extractor == "rules":
                raise
            raise HTTPException(502, f"Model call failed: {type(error).__name__}") from error
        return {
            "app_id": request.app_id,
            "extractor": request.extractor,
            "flags": [_flag_json(request.app_id, f) for f in result.flags],
            "dropped_for_missing_evidence": len(result.dropped),
            "unmapped_declared": unmapped_declared,
            "unmapped_extracted": result.facts.unmapped,
            "facts": _facts_json(result.facts),
            "declared": {
                "conditions": application.declared_conditions,
                "medications": {m.name: m.dose for m in application.declared_medications},
                "allergies": application.declared_allergies,
            },
            "pharmacy": {row.drug: row.dose for row in rows},
        }

    @app.get("/api/reviews")
    def get_reviews() -> dict:
        return load_reviews(reviews_path)

    @app.put("/api/reviews")
    def put_review(review: ReviewIn) -> dict:
        save_review(reviews_path, review.flag_id, review.decision)
        return load_reviews(reviews_path)

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    return app
