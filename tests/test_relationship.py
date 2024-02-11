from weakref import proxy


def test_relationship(engine_config, engine, ecosystem_config, ecosystem):
    assert engine_config.engine is proxy(engine)
    assert engine.config is engine_config

    assert ecosystem_config.general is engine_config

    assert ecosystem.engine is proxy(engine)
    assert ecosystem.config is ecosystem_config
