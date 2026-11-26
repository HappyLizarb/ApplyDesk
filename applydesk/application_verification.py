"""Explicit email-code recovery keeps the original application, audited facts and attempt ledger."""
import functools
import re

from .application_answers import with_application_source
from .application_client import ApplicationClient
from .application_session import ApplicationSession
from .application_steps import verify_attachments
from .application_submission import NATIVE_VERIFICATION, attempt_key, finish_attempt, submit_ready
from .applications import Applications
from .intelligence import audit_fields
from .mapping import plan_fields


def pending(engine, job_id):
    """Only a human follow-up may continue an unconfirmed employer verification challenge."""
    engine.check_active()
    active = engine.active or {}
    message = active.get('message', {})
    if message.get('role') != 'user' or active.get('auto_restart'):
        raise ValueError('Email verification needs a new user message.')
    job = engine.w.store.get('job', job_id)
    attempt = engine.w.store.get('submission_attempt', attempt_key(job)) if job else None
    if not job or job.get('removed_at') or job.get('manual_status') in {'submitted', 'skipped'} or not attempt or attempt['status'] != 'verification_required':
        raise ValueError('Choose an existing application awaiting email verification.')
    if attempt.get('receipt', {}).get('confirmed') or not engine.w.profile()['confirmed']:
        raise ValueError('The application is already confirmed or the profile needs review.')
    return job, attempt, message


def refill_application(engine, job_id):
    """Recheck eligibility and refill only after explicit user direction and a rejected code."""
    job, attempt, message = pending(engine, job_id)
    if not re.search(r'\brefill\b', message.get('text', ''), re.I) or attempt.get('receipt', {}).get('issue') != 'Incorrect security code':
        raise ValueError('Explicitly request a refill after the employer rejects a code.')
    if handoff := native_handoff(engine.w, job_id, attempt):
        return handoff
    automation = engine.applications
    run = engine.w.store.get('application_run', attempt['run_id'])
    run.update(profile_revision=engine.w.profile()['revision'], account_id=engine.accounts.state()['active'])
    automation.run = run
    client = functools.partial(ApplicationClient, engine, job_id=job_id)
    choice = {'id': run['resume_id'], 'sha256': run['resume_sha256']}
    application = Applications(engine.w, client, automation.check_active, functools.partial(automation._scope, client),
                               functools.partial(ApplicationSession, engine), resume_choice=choice)
    try:
        ready = application.prepare(job_id)
        if ready['status'] != 'ready':
            return {'job_id': job_id, 'status': ready['status'], 'issues': ready.get('exceptions', [])}
        result = submit_ready(engine, ready, run, automation.check_active, retry_attempt=attempt)
        save_result(engine, attempt, result)
        return result
    finally:
        session = engine.w.sessions.get(job_id)
        if session:
            session.park()


def verification_audit(engine, job, page):
    """Audit all current profile answers and attachments, exempting only the observed code cells."""
    profile = with_application_source(engine.w.profile(), job)
    cells = [field for field in page['fields'] if 'A verification code was sent to' in field.get('group', '')]
    if len(cells) != 8 or cells[0]['label'] != 'Security code':
        raise ValueError('The expected email verification fields are unavailable.')
    ignored = [field['field_id'] for field in cells]
    answers, issues = plan_fields(page, profile, job['analysis']['country'], job['analysis'].get('country_name', ''), ignored_ids=ignored)
    if issues:
        raise ValueError('; '.join(issues))
    verify_attachments(page, job['attachments'])
    fields = [{**field, 'id': field['field_id'], 'value': field.get('checked') if field['type'] in {'radio', 'checkbox'} else field.get('value')} for field in page['fields'] if field['field_id'] not in ignored]
    expected = [{'id': answer['field_id'], 'value': answer['value'], 'fact_ids': answer['fact_ids']} for answer in answers]
    audit = audit_fields(profile, {**page, 'fields': fields}, expected)
    if not audit['passed']:
        raise ValueError('; '.join(audit['issues']))
    return ignored


def verify_application(engine, job_id, code):
    """Use a code supplied in this human turn, never a generated or silently repeated value."""
    job, attempt, message = pending(engine, job_id)
    if not isinstance(code, str) or not re.fullmatch(r'[a-zA-Z0-9]{8}', code) or not re.search(r'\b' + re.escape(code) + r'\b', message.get('text', '')):
        raise ValueError('Supply the employer email code in your message.')
    if attempt.get('profile_revision') != engine.w.profile()['revision']:
        raise ValueError('The profile changed after eligibility review. Refill and recheck the application before verification.')
    if handoff := native_handoff(engine.w, job_id, attempt):
        return handoff
    session = engine.w.sessions.get(job_id)
    if not session:
        raise ValueError('The retained application window is unavailable. Refill after confirming the earlier code was rejected.')
    revision = engine.w.profile()['revision']
    page = session.call('snapshot')
    cells = verification_audit(engine, job, page)
    engine.check_active()
    if engine.w.profile()['revision'] != revision:
        raise ValueError('The profile changed during verification.')
    params = {'job_id': job_id, 'attempt_id': attempt['id'], 'snapshot': page,
              'code_ids': cells, 'company': job['company'], 'title': job['title'], 'code': code}
    return send_code(engine, job, attempt, session, params)


def native_handoff(workspace, job_id, attempt):
    """Native Chrome exposes its original form to the applicant; legacy sessions retain the code protocol."""
    session = workspace.sessions.get(job_id)
    manager = getattr(session, 'manager', None) if session is not None else getattr(workspace, 'application_browser', None)
    if getattr(manager, 'native', False) is True:
        return {'job_id': job_id, 'status': 'needs_review', 'receipt': attempt['receipt'], 'issues': [NATIVE_VERIFICATION]}
    return None


def send_code(engine, job, attempt, session, params):
    """Reserve the existing attempt before sending; interrupted results cannot become retryable."""
    attempt.setdefault('verification_history', []).append(attempt.get('receipt', {}))
    attempt.update(status='submitting', receipt={})
    engine.w.store.put('submission_attempt', attempt_key(job), attempt)
    engine.w.store.job(job['id'], status='submitting', progress='Verifying the employer email code…')
    try:
        receipt = session.call('verify_application', params)
        confirmed = receipt.get('confirmed') is True and receipt.get('attempt_id') == attempt['id'] and bool(receipt.get('receipt'))
        status = 'submitted' if confirmed else 'verification_required' if receipt.get('verification_required') else 'submission_unknown'
        result = finish_attempt(engine.w, job, attempt, status, receipt)
    except BaseException:
        result = finish_attempt(engine.w, job, attempt, 'submission_unknown', {})
        save_result(engine, attempt, result)
        raise
    finally:
        session.park()
    save_result(engine, attempt, result)
    return result



def save_result(engine, attempt, result):
    """Replace the old outcome for this one durable attempt without consuming another slot."""
    run = engine.w.store.get('application_run', attempt['run_id'])
    for index, previous in enumerate(run['results']):
        if previous.get('receipt', {}).get('attempt_id') == attempt['id']:
            run['results'][index] = result
    engine.applications.run = run
    engine.applications._save()
