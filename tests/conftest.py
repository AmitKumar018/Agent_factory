import asyncio
import inspect


_LOOP = None


def _loop():
    global _LOOP
    if _LOOP is None or _LOOP.is_closed():
        _LOOP = asyncio.new_event_loop()
    return _LOOP


def pytest_configure(config):
    config.addinivalue_line("markers", "asyncio: run async tests with the built-in asyncio runner")


def pytest_pyfunc_call(pyfuncitem):
    if "asyncio" not in pyfuncitem.keywords:
        return None

    test_function = pyfuncitem.obj
    if not inspect.iscoroutinefunction(test_function):
        return None

    fixture_names = pyfuncitem._fixtureinfo.argnames
    fixture_values = {name: pyfuncitem.funcargs[name] for name in fixture_names}
    _loop().run_until_complete(test_function(**fixture_values))
    return True


def pytest_fixture_setup(fixturedef, request):
    fixturefunc = fixturedef.func
    if not (inspect.iscoroutinefunction(fixturefunc) or inspect.isasyncgenfunction(fixturefunc)):
        return None

    kwargs = {name: request.getfixturevalue(name) for name in fixturedef.argnames}

    if inspect.isasyncgenfunction(fixturefunc):
        agen = fixturefunc(**kwargs)
        result = _loop().run_until_complete(agen.__anext__())

        def finalizer():
            try:
                _loop().run_until_complete(agen.__anext__())
            except StopAsyncIteration:
                pass

        request.addfinalizer(finalizer)
        fixturedef.cached_result = (result, fixturedef.cache_key(request), None)
        return result

    result = _loop().run_until_complete(fixturefunc(**kwargs))
    fixturedef.cached_result = (result, fixturedef.cache_key(request), None)
    return result


def _close_workflow_singletons():
    async def _close_all():
        from app.storage.database import close_db
        from app.workflows.codegen.runner import close_orchestrator
        from app.workflows.requirements.graph import close_checkpointer

        await close_orchestrator()
        await close_checkpointer()
        await close_db()

    _loop().run_until_complete(_close_all())


def pytest_sessionfinish(session, exitstatus):
    global _LOOP
    if _LOOP is not None and not _LOOP.is_closed():
        _close_workflow_singletons()
        _LOOP.close()
        _LOOP = None