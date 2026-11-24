import hashlib
import uuid
from datetime import date

from fastapi import HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.exc import IntegrityError

from src.config.settings import IS_MYSQL, cache, engine
from src.constants.workflow import AmendmentStatus, Status
from src.models.tables import (
    audit, cases, contract_amendments, contract_locks, evidence, run_amendments, run_inputs,
    run_recomputations, runs,
)
from src.services import domain
from src.services.tape_erasure import Amendment, first_overlapping
from src.utils.encoding import canonical
from src.utils.time import utc_time


def log(conn, actor, action, reference):
    conn.execute(audit.insert().values(actor=actor, action=action, reference=str(reference), created_at=utc_time()))


def recent(table):
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(select(table).order_by(table.c.id.desc()).limit(100)).mappings()]


def found(conn, table, identity):
    row = conn.execute(select(table).where(table.c.id == identity)).mappings().first()
    if row is None:
        raise HTTPException(404, '找不到该记录')
    return dict(row)


def _snapshot(conn, run, persist=False):
    """Return the catalog snapshot captured when the run was created.

    The snapshot is the directory used for that batch; later edits to a case do
    not rewrite it, so both retention lookup and recomputation keep using the
    catalog from that time.
    """
    row = conn.execute(select(run_inputs.c.payload).where(run_inputs.c.run_id == run['id'])).scalar_one_or_none()
    if row is not None:
        return row
    payload = found(conn, cases, run['case_id'])['payload']
    if persist:
        conn.execute(run_inputs.insert().values(run_id=run['id'], payload=payload))
    return payload


def make_case(title, payload, actor):
    title = title.strip()
    if not title:
        raise HTTPException(422, '档案名称不能为空')
    try:
        if len(canonical(payload)) > 512 * 1024:
            raise ValueError('业务输入超过 512 KiB')
        if callable(getattr(domain, 'validate', None)):
            domain.validate(payload)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from None
    with engine.begin() as conn:
        case_id = conn.execute(cases.insert().values(slug=uuid.uuid4().hex, title=title, payload=payload,
                               author=actor, created_at=utc_time())).inserted_primary_key[0]
        log(conn, actor, 'create_case', case_id)
        return found(conn, cases, case_id)


def _wake():
    try:
        cache.lpush('worker:wake', 'queued')
        cache.ltrim('worker:wake', 0, 99)
    except Exception:
        # The durable database queue is polled even if Redis is temporarily unavailable.
        pass


def enqueue(case_id, actor):
    with engine.begin() as conn:
        payload = found(conn, cases, case_id)['payload']
        run_id = conn.execute(runs.insert().values(case_id=case_id, status=Status.QUEUED,
                              owner=actor, created_at=utc_time())).inserted_primary_key[0]
        conn.execute(run_inputs.insert().values(run_id=run_id, payload=payload))
        log(conn, actor, 'enqueue', run_id)
        result = found(conn, runs, run_id)
    _wake()
    return result


def claim():
    ready = or_(runs.c.status == Status.QUEUED,
                and_(runs.c.status == Status.RUNNING, runs.c.started_at < utc_time(-600)))
    with engine.begin() as conn:
        run = conn.execute(select(runs.c.id, runs.c.case_id, runs.c.status).where(ready).order_by(runs.c.id)
                           .limit(1).with_for_update(skip_locked=True)).first()
        if not run:
            return None
        token = str(uuid.uuid4())
        conn.execute(runs.update().where(runs.c.id == run.id).values(
            started_at=utc_time(), status=Status.RUNNING, attempt_token=token))
        payload = _snapshot(conn, {'id': run.id, 'case_id': run.case_id}, persist=True)
        return run.id, token, payload


def finish(run_id, token, result=None, error=None):
    digest = hashlib.sha256(canonical(result)).hexdigest() if result is not None else None
    status = Status.FAILED if error else Status.COMPLETE
    with engine.begin() as conn:
        count = conn.execute(runs.update().where(runs.c.id == run_id,
                        runs.c.attempt_token == token, runs.c.status == Status.RUNNING)
                        .values(status=status, finished_at=utc_time(), error=error)).rowcount
        if not count:
            return
        if result is not None:
            conn.execute(evidence.insert().values(run_id=run_id, report=result, digest=digest))
        log(conn, 'worker', status.value, run_id)


def review(identity, note, actor):
    note = note.strip()
    if not note:
        raise HTTPException(422, '复核意见不能为空')
    with engine.begin() as conn:
        found(conn, evidence, identity)
        count = conn.execute(evidence.update().where(evidence.c.id == identity, evidence.c.reviewed_at.is_(None))
                            .values(review_note=note, reviewer=actor, reviewed_at=utc_time())).rowcount
        if count != 1:
            raise HTTPException(409, '该证据已经复核')
        log(conn, actor, 'review', identity)
        return found(conn, evidence, identity)


def _clean_text(value, label, limit=64, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > limit:
        raise HTTPException(422, f'{label}须为不超过 {limit} 字的非空文本')
    return value


def _clean_day(value, label, optional=False):
    if value is None and optional:
        return None
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise HTTPException(422, f'{label}须为有效的 YYYY-MM-DD 日期') from None
    if parsed.isoformat() != value:
        raise HTTPException(422, f'{label}须为有效的 YYYY-MM-DD 日期')
    return value


def _lock_contract(conn, contract_id):
    """Serialize corrections for one contract so first-to-record wins."""
    if IS_MYSQL:
        statement = mysql_insert(contract_locks).values(contract_id=contract_id).on_duplicate_key_update(
            contract_id=contract_locks.c.contract_id)
    else:
        statement = postgres_insert(contract_locks).values(contract_id=contract_id).on_conflict_do_nothing(
            index_elements=['contract_id'])
    conn.execute(statement)
    conn.execute(select(contract_locks.c.contract_id).where(
        contract_locks.c.contract_id == contract_id).with_for_update())


def _amendment_row(row):
    row = dict(row)
    return {
        'id': row['id'], 'contract_id': row['contract_id'], 'asset_id': row['asset_id'],
        'hold_until': row['hold_until'], 'effective_from': row['effective_from'],
        'effective_to': row['effective_to'], 'status': row['status'],
        'conflict_with': row['conflict_with'], 'request_key': row['request_key'],
        'actor': row['actor'], 'created_at': row['created_at'], 'note': row['note'],
    }


def _find_amendment(conn, request_key):
    row = conn.execute(select(contract_amendments).where(
        contract_amendments.c.request_key == request_key)).mappings().first()
    return _amendment_row(row) if row else None


def _conflict_id(new, accepted):
    """First accepted correction for the same contract whose dates overlap."""
    ordered = [Amendment(row['contract_id'], row['asset_id'], row['hold_until'],
                         row['effective_from'], row['effective_to']) for row in accepted]
    index = first_overlapping(ordered, new)
    return accepted[index]['id'] if index is not None else None


def _replay(accepted, new):
    """An already-accepted correction with identical fields, if any.

    Replaying this exact correction (for example after a dropped connection)
    must not record anything new; the first recording stands, timestamp intact.
    """
    for row in accepted:
        if (row['asset_id'], row['hold_until'], row['effective_from'], row['effective_to']) == (
                new.asset_id, new.hold_until, new.effective_from, new.effective_to):
            return row
    return None


def record_amendment(form, actor, idempotency_key=None):
    """Record a date-bounded contract correction.

    A correction whose date range overlaps an already-accepted correction for
    the same contract is stored as ``conflict`` and returned to review; the
    first-recorded correction wins. Corrections with disjoint ranges all take
    effect. A stable request key, or an exact replay of an accepted correction,
    makes a retry after a dropped connection a no-op that still reports the
    original recording time.
    """
    contract_id = _clean_text(form.get('contract_id'), '合同编号')
    asset_id = _clean_text(form.get('asset_id'), '资产编号')
    effective_from = _clean_day(form.get('effective_from'), '生效起始日')
    effective_to = _clean_day(form.get('effective_to'), '生效截止日', optional=True)
    if effective_to is not None and effective_to < effective_from:
        raise HTTPException(422, '生效截止日不得早于生效起始日')
    hold_until = _clean_day(form.get('hold_until'), '保护截止日', optional=True)
    note = form.get('note')
    if note is not None:
        if not isinstance(note, str) or len(note) > 500:
            raise HTTPException(422, '更正说明不得超过 500 字')
        note = note.strip() or None
    request_key = form.get('request_key') or idempotency_key
    if request_key is not None:
        request_key = _clean_text(request_key, '请求标识', limit=128)
    else:
        # Without an explicit key each submission is a distinct correction. A
        # client that may retry should send the same key on the retry so a
        # dropped connection does not double-record; a second identical-looking
        # correction without a key is treated as a separate attempt (and so may
        # conflict, which is the intended "return to review" outcome).
        request_key = uuid.uuid4().hex

    try:
        with engine.begin() as conn:
            _lock_contract(conn, contract_id)
            existing = _find_amendment(conn, request_key)
            if existing:
                return existing
            accepted = conn.execute(select(contract_amendments).where(
                contract_amendments.c.contract_id == contract_id,
                contract_amendments.c.status == AmendmentStatus.ACCEPTED).order_by(
                contract_amendments.c.id).with_for_update()).mappings().all()
            new = Amendment(contract_id, asset_id, hold_until, effective_from, effective_to)
            replay = _replay(accepted, new)
            if replay is not None:
                return _amendment_row(replay)
            conflict_with = _conflict_id(new, accepted)
            status = AmendmentStatus.CONFLICT if conflict_with is not None else AmendmentStatus.ACCEPTED
            amendment_id = conn.execute(contract_amendments.insert().values(
                contract_id=contract_id, asset_id=asset_id, hold_until=hold_until,
                effective_from=effective_from, effective_to=effective_to, status=status.value,
                request_key=request_key, actor=actor, created_at=utc_time(),
                conflict_with=conflict_with, note=note)).inserted_primary_key[0]
            log(conn, actor, 'amend', amendment_id)
            return _amendment_row(conn.execute(select(contract_amendments).where(
                contract_amendments.c.id == amendment_id)).mappings().first())
    except IntegrityError:
        # A concurrent submission with the same idempotency key already landed.
        with engine.connect() as conn:
            existing = _find_amendment(conn, request_key)
            if existing:
                return existing
        raise


def evidence_retention(identity):
    with engine.connect() as conn:
        ev = found(conn, evidence, identity)
    return retention(ev['run_id'])


def list_amendments():
    with engine.connect() as conn:
        rows = conn.execute(select(contract_amendments).order_by(contract_amendments.c.id.desc())
                            .limit(100)).mappings().all()
    return [_amendment_row(row) for row in rows]


def retention(run_id):
    with engine.connect() as conn:
        run = found(conn, runs, run_id)
        payload = _snapshot(conn, run)
        ev = conn.execute(select(evidence.c.id).where(evidence.c.run_id == run_id)).scalar_one_or_none()
    view = domain.retention_view(payload)
    return {
        'run': {'id': run['id'], 'case_id': run['case_id'], 'status': run['status'],
                'owner': run['owner'], 'created_at': run['created_at'],
                'started_at': run['started_at'], 'finished_at': run['finished_at']},
        'evidence_id': ev, **view,
    }


def _accepted_amendments(conn, lock=False):
    statement = select(contract_amendments).where(
        contract_amendments.c.status == AmendmentStatus.ACCEPTED).order_by(contract_amendments.c.id)
    if lock:
        # Serialize against concurrent corrections while a recompute reads them.
        statement = statement.with_for_update()
    rows = conn.execute(statement).mappings().all()
    return [_amendment_row(row) for row in rows]


def _recompute_view(conn, record):
    result = found(conn, runs, record['result_run_id'])
    return {
        'id': record['id'], 'source_run_id': record['source_run_id'],
        'result_run_id': record['result_run_id'], 'cutoff': record['cutoff'],
        'created_at': record['created_at'], 'actor': record['actor'],
        'request_key': record['request_key'], 'result': result,
        'amendments': [dict(row) for row in conn.execute(
            select(run_amendments.c.amendment_id).where(
                run_amendments.c.run_id == record['result_run_id'])).mappings()],
    }


def _existing_recompute(conn, request_key):
    if not request_key:
        return None
    row = conn.execute(select(run_recomputations).where(
        run_recomputations.c.request_key == request_key)).mappings().first()
    return _recompute_view(conn, dict(row)) if row else None


def recompute(source_run_id, request_key, actor):
    """Re-run one stored batch's check date with amended contract associations.

    The directory and reference edges come from the batch snapshot captured at
    enqueue time, so recomputation stays on the catalog from that moment. Only
    contract associations are overlaid, and the original run and its evidence
    are left untouched. A matching request key returns the first recorded
    recomputation with its original timestamp.
    """
    if request_key:
        request_key = _clean_text(request_key, '请求标识', limit=128)
    else:
        # No explicit key: each call is a distinct recomputation. A client that
        # may retry after a dropped connection should send a stable key.
        request_key = uuid.uuid4().hex
    try:
        with engine.begin() as conn:
            existing = _existing_recompute(conn, request_key)
            if existing:
                return existing
            source = found(conn, runs, source_run_id)
            if source['status'] != Status.COMPLETE:
                raise HTTPException(409, '仅已完成的批次可以重算')
            snapshot = _snapshot(conn, source, persist=True)
            cutoff = snapshot['cutoff']
            accepted = _accepted_amendments(conn, lock=True)
            overlay = [Amendment(row['contract_id'], row['asset_id'], row['hold_until'],
                                 row['effective_from'], row['effective_to']) for row in accepted]
            amended, applied, _skipped = domain.apply_amendments(snapshot, overlay, cutoff)
            try:
                domain.validate(amended)
            except (ValueError, TypeError) as exc:
                raise HTTPException(422, str(exc)) from None
            result_id = conn.execute(runs.insert().values(
                case_id=source['case_id'], status=Status.QUEUED, owner=actor,
                created_at=utc_time())).inserted_primary_key[0]
            conn.execute(run_inputs.insert().values(run_id=result_id, payload=amended))
            record_id = conn.execute(run_recomputations.insert().values(
                source_run_id=source_run_id, result_run_id=result_id, cutoff=cutoff,
                request_key=request_key, actor=actor, created_at=utc_time())).inserted_primary_key[0]
            if applied:
                conn.execute(run_amendments.insert(), [
                    {'run_id': result_id, 'amendment_id': row['id']} for row in applied])
            log(conn, actor, 'recompute', record_id)
            record = dict(conn.execute(select(run_recomputations).where(
                run_recomputations.c.id == record_id)).mappings().first())
            view = _recompute_view(conn, record)
    except IntegrityError:
        with engine.connect() as conn:
            existing = _existing_recompute(conn, request_key)
            if existing:
                return existing
        raise
    _wake()
    return view


def list_recomputations():
    with engine.connect() as conn:
        rows = conn.execute(select(run_recomputations).order_by(
            run_recomputations.c.id.desc()).limit(100)).mappings().all()
        return [_recompute_view(conn, dict(row)) for row in rows]
