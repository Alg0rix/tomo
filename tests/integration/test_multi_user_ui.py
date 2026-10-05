"""Account/picker HTTP seams on a real SQLite store (browser evidence separate)."""
pytest_plugins = ["tests.integration.test_multi_user_http"]


def test_new_coordinator_chat_selects_assigned_profile_not_unassigned_global_default(http):
    app, admin, alice, bob, profile, ac, c, bc = http
    secret = ac.post('/api/llm-profiles', json={'name': 'Unassigned global', 'model': 'private-model',
                                               'base_url': 'http://127.0.0.1:9999/v1', 'api_key': 'private-key'}).json()
    assert ac.post('/api/llm-profiles/' + secret['id'] + '/default', json={'model': 'private-model'}).status_code == 200
    sid = c.post('/api/sessions/home', json={}).json()['session_id']
    model = c.get('/api/sessions/' + sid + '/reasoning-effort').json()
    assert model['profile_id'] == profile['id'] and model['selected_model_profile_id'] == profile['id']
    assert secret['id'] not in {p['id'] for p in model['model_profiles']}
    assert ac.delete('/api/users/' + alice['id'] + '/grants/model/' + profile['id']).status_code == 200
    assert c.post('/api/sessions/' + sid + '/chat/stream', json={'message': 'No model fallback'}).status_code == 503


def test_account_project_sharing_catalog_and_admin_controls(http):
    app, admin, alice, bob, profile, ac, c, bc = http
    page = c.get('/account')
    assert page.status_code == 200
    assert 'personalProfile' in page.text and 'personalKeys' in page.text
    assert 'accountAdministration' not in page.text
    assert 'href="/system"' not in page.text and 'href="/agents"' not in page.text
    assert 'accountAdministration' in ac.get('/account').text
    sid = c.post('/api/sessions/home', json={}).json()['session_id']
    assert 'chatAccessButton' in c.get('/sessions').text
    assert 'newChatBrowseFolder' not in c.get('/sessions').text
    project = c.post('/api/projects', json={'name': 'Shared notes'}).json()['workplace']
    url = '/api/projects/' + project['id'] + '/shares'
    assert c.post(url, json={'username': 'bob', 'permission': 'read'}).status_code == 200
    assert c.get(url).json()['shares'][0]['username'] == 'bob'
    assert bc.get(url).status_code == 403  # recipient cannot enumerate recipients
    assert bc.post(url, json={'username': 'alice', 'permission': 'read_write'}).status_code == 403
    catalog = bc.get('/api/access/resources').json()['workplaces']
    shared = next(w for w in catalog if w['id'] == project['id'])
    assert shared['permission'] == 'read' and 'root_path' not in shared
    assert c.post(url, json={'username': 'bob', 'permission': 'read_write'}).status_code == 200
    assert c.get(url).json()['shares'][0]['permission'] == 'read_write'
    assert c.delete(url + '/' + bob['id']).status_code == 200
    assert c.get(url).json()['shares'] == []
    assert project['id'] not in {w['id'] for w in bc.get('/api/access/resources').json()['workplaces']}
    scope = c.get('/api/sessions/' + sid + '/access').json()
    assert scope['unrestricted_workplace_ids'] == []
    personal = scope['active_workplace_id']
    assert ac.put('/api/users/' + bob['id'] + '/grants/workplace/' + personal,
                  json={'permission': 'read'}).status_code == 403
    assert personal not in {w['id'] for w in bc.get('/api/access/resources').json()['workplaces']}
    destinations = '/api/users/' + alice['id'] + '/unrestricted-destinations'
    assert c.get(destinations).status_code == 403
    target_personal = next(w for w in ac.get(destinations).json()['workplaces'] if w['id'] == personal)
    assert target_personal['name'] == "Selected account's personal space"
    assert 'root_path' not in target_personal
    assert ac.put('/api/users/' + alice['id'] + '/grants/unrestricted/' + personal,
                  json={'permission': 'use'}).status_code == 200
    assert c.get('/api/sessions/' + sid + '/access').json()['execution_mode'] == 'restricted'
    quota_url = '/api/users/' + alice['id'] + '/quota'
    assert ac.put(quota_url, json={'disk_mb': 1234}).status_code == 200
    assert ac.put(quota_url, json={'duration_seconds': 45}).status_code == 200
    quota = ac.get(quota_url).json()
    assert quota['disk_mb'] == 1234 and 'max_concurrent_jobs' not in quota and quota['duration_seconds'] == 45
    assert c.put(quota_url, json={'disk_mb': 9999}).status_code == 403


def test_project_share_pending_teardown_is_visible_and_retry_does_not_waive_failure(http):
    import uuid
    from app.services import store
    from app.runtime.isolation.backend import ContainerBackend
    app, admin, alice, bob, profile, ac, c, bc = http
    project = bc.post('/api/projects', json={'name': 'Pending shared input'}).json()['workplace']
    url = '/api/projects/' + project['id'] + '/shares'
    assert bc.post(url, json={'user_id': alice['id'], 'permission': 'read'}).status_code == 200
    sid = c.post('/api/sessions/home', json={}).json()['session_id']
    active = c.get('/api/sessions/' + sid + '/access').json()['active_workplace_id']
    assert c.put('/api/sessions/' + sid + '/access',
                 json={'active_workplace_id': active, 'additional_workplace_ids': [project['id']]}).status_code == 200
    broken = ContainerBackend(policy=store.access, runtime='/missing-runtime', namespace='pending-ui-' + uuid.uuid4().hex)
    store.access.register_execution_stopper(broken.stop_session)
    try:
        assert bc.delete(url + '/' + alice['id']).status_code == 503
        shares = bc.get(url).json()['shares']
        assert len(shares) == 1 and shares[0]['state'] == 'pending'
        assert c.get('/api/sessions/' + sid + '/access').json()['access_pending']
        assert c.post('/api/sessions/' + sid + '/chat/stream', json={'message': 'Do not execute'}).status_code == 503
        broken.runtime = 'docker'  # repair the registered backend, not a policy bypass
        assert bc.delete(url + '/' + alice['id']).status_code == 200
        assert bc.get(url).json()['shares'] == []
        assert not c.get('/api/sessions/' + sid + '/access').json()['access_pending']
    finally:
        broken.close()


def test_pending_model_chat_reports_recovery_not_forbidden(http):
    import uuid
    from app.services import store
    from app.runtime.isolation.backend import ContainerBackend
    app, admin, alice, bob, profile, ac, c, bc = http
    sid = c.post('/api/sessions/home', json={}).json()['session_id']
    broken = ContainerBackend(policy=store.access, runtime='/missing-runtime', namespace='pending-model-' + uuid.uuid4().hex)
    store.access.register_execution_stopper(broken.stop_session)
    try:
        assert ac.delete('/api/users/' + alice['id'] + '/grants/model/' + profile['id']).status_code == 503
        response = c.post('/api/sessions/' + sid + '/chat/stream', json={'message': 'Do not execute'})
        # Existing sessions also have a session-level barrier. It remains 503,
        # not an ownership error; a fresh authorized chat exposes model guidance.
        assert response.status_code == 503
        response = c.post('/api/sessions/home', json={})
        assert response.status_code == 503
        assert 'pending' in response.text.lower() and 'teardown' in response.text.lower()
        assert bc.get('/api/sessions/' + sid + '/access').status_code == 404
    finally:
        broken.close()


def test_admin_pending_model_is_not_a_chat_403(http):
    import uuid
    from app.services import store
    from app.runtime.isolation.backend import ContainerBackend
    app, admin, alice, bob, profile, ac, c, bc = http
    assert ac.post('/api/sessions/home', json={}).status_code == 200
    url = '/api/users/' + admin['id'] + '/grants/model/' + profile['id']
    assert ac.put(url, json={'permission': 'use'}).status_code == 200
    broken = ContainerBackend(policy=store.access, runtime='/missing-runtime', namespace='pending-admin-' + uuid.uuid4().hex)
    store.access.register_execution_stopper(broken.stop_session)
    try:
        assert ac.delete(url).status_code == 503
        fresh = ac.post('/api/sessions/home', json={}).json()['session_id']
        assert ac.get('/api/sessions/' + fresh + '/access').json()['execution_mode'] == 'unrestricted'
        response = ac.post('/api/sessions/' + fresh + '/chat/stream', json={'message': 'Do not execute'})
        assert response.status_code == 503
        assert 'pending' in response.text.lower() and 'teardown' in response.text.lower()
        assert c.get('/api/sessions/' + fresh + '/access').status_code == 404
    finally:
        broken.close()


def test_attachment_import_never_falls_back_and_checks_owner(http):
    app, admin, alice, bob, profile, ac, c, bc = http
    sid = c.post('/api/sessions/home', json={}).json()['session_id']
    att = c.post('/api/sessions/' + sid + '/attachments', files={'file': ('note.txt', b'owned', 'text/plain')}).json()
    url = '/api/attachments/' + att['id'] + '/import'
    assert bc.post(url).status_code == ac.post(url).status_code == 404
    # Ordinary server filesystem exceeds the default hard capacity budget.
    # A visible picker alone never permits host execution or unbounded storage.
    response = c.post(url)
    assert response.status_code == 503
    assert 'unavailable' in response.text.lower()
    assert c.get('/api/attachments/' + att['id']).content == b'owned'
    for caller in (ac, c):
        upload = caller.post('/api/memory/upload', data={'entity': 'project/notes'},
                             files={'file': ('input.pdf', b'untrusted', 'application/pdf')})
        assert upload.status_code == 503 and 'owned session_id' in upload.text
    assert bc.post('/api/memory/upload', data={'entity': 'project/notes', 'session_id': sid},
                   files={'file': ('input.pdf', b'untrusted', 'application/pdf')}).status_code == 404
    from app.services import secret_store
    admin_sid = ac.post('/api/sessions/home', json={}).json()['session_id']
    # This regression checks restricted broker admission, not the Admin's new
    # host default. Explicitly opt the Admin chat into the same tested boundary.
    admin_access = ac.get('/api/sessions/' + admin_sid + '/access').json()
    assert ac.put('/api/sessions/' + admin_sid + '/access', json={
        'active_workplace_id': admin_access['active_workplace_id'], 'execution_mode': 'restricted',
    }).status_code == 200
    for caller, owner, session in ((ac, admin, admin_sid), (c, alice, sid)):
        token = secret_store.issue_capability(session, owner['id'])
        for endpoint in ('/api/secret-broker/apply', '/api/connection-broker/http'):
            response = caller.post(endpoint, headers={'Authorization': 'Bearer ' + token}, json={})
            assert response.status_code == 503  # Admin role is not a sandbox exception
        secret_store.revoke_capability(token)
