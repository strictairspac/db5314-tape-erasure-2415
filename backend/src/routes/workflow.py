from fastapi import APIRouter, Depends, Request
from sqlalchemy import text

from src.config.settings import cache, engine
from src.controllers.workflow import download
from src.models.tables import audit, cases, evidence, runs
from src.schemas.workflow import AmendmentForm, CaseForm, LoginForm, RecomputeForm, ReviewForm
from src.services import domain, workflow
from src.services.security import current_user, login, operator, reviewer

router = APIRouter()


@router.get('/health')
def health():
    with engine.connect() as conn:
        conn.execute(text('SELECT 1')).scalar_one()
    cache.ping()
    return {'ok': True}


@router.post('/login')
def sign_in(form: LoginForm):
    return login(form.username, form.password)


@router.get('/spec')
def spec(_=Depends(current_user)):
    return domain.SPEC


@router.get('/cases')
def list_cases(_=Depends(current_user)):
    return workflow.recent(cases)


@router.post('/cases', status_code=201)
def create_case(form: CaseForm, actor=Depends(operator)):
    return workflow.make_case(form.title, form.payload, actor)


@router.get('/runs')
def list_runs(_=Depends(current_user)):
    return workflow.recent(runs)


@router.post('/cases/{case_id}/runs', status_code=202)
def start_run(case_id: int, actor=Depends(operator)):
    return workflow.enqueue(case_id, actor)


@router.get('/evidence')
def list_evidence(_=Depends(current_user)):
    return workflow.recent(evidence)


@router.get('/evidence/{identity}/download')
def export_evidence(identity: int, _=Depends(current_user)):
    return download(identity)


@router.post('/evidence/{identity}/review')
def review_evidence(identity: int, form: ReviewForm, actor=Depends(reviewer)):
    return workflow.review(identity, form.note, actor)


@router.get('/runs/{run_id}/retention')
def run_retention(run_id: int, _=Depends(current_user)):
    return workflow.retention(run_id)


@router.get('/evidence/{identity}/retention')
def evidence_retention(identity: int, _=Depends(current_user)):
    return workflow.evidence_retention(identity)


@router.get('/amendments')
def list_amendments(_=Depends(current_user)):
    return workflow.list_amendments()


@router.post('/amendments', status_code=201)
def create_amendment(form: AmendmentForm, request: Request, actor=Depends(operator)):
    return workflow.record_amendment(form.model_dump(), actor, request.headers.get('Idempotency-Key'))


@router.get('/recomputations')
def list_recomputations(_=Depends(current_user)):
    return workflow.list_recomputations()


@router.post('/runs/{run_id}/recompute', status_code=202)
def recompute_run(run_id: int, request: Request, form: RecomputeForm | None = None, actor=Depends(operator)):
    key = (form.request_key if form else None) or request.headers.get('Idempotency-Key')
    return workflow.recompute(run_id, key, actor)


@router.get('/audit')
def list_audit(_=Depends(current_user)):
    return workflow.recent(audit)
