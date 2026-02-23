def test_relationship(engine_config, engine, ecosystem_config, ecosystem):
    assert engine.config is engine_config

    assert ecosystem_config.general is engine_config

    assert ecosystem.engine is engine
    assert ecosystem.config is ecosystem_config
