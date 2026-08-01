import logging

from src.logger_setup import setup_logging


def test_setup_logging_creates_nested_writable_destination(tmp_path):
    log_path = tmp_path / "application-support" / "logs" / "dap_manager.log"

    logger = setup_logging(str(log_path))
    logger.info("ready")

    assert log_path.is_file()
    assert "ready" in log_path.read_text(encoding="utf-8")


def test_setup_logging_redacts_query_credentials_in_message_and_args(tmp_path):
    log_path = tmp_path / "dap_manager.log"
    logger = setup_logging(str(log_path))

    logger.info(
        'GET %s HTTP/1.1',
        '/api/stream/track?token=top-secret&mode=play',
    )
    logger.info(
        'redirect /auth?bundle_token=bootstrap-secret&next=/satellite'
    )
    logger.info('query /api/status?api_token=other-secret')

    contents = log_path.read_text(encoding="utf-8")
    assert "top-secret" not in contents
    assert "bootstrap-secret" not in contents
    assert "other-secret" not in contents
    assert contents.count("[REDACTED]") == 3


def teardown_module():
    logging.shutdown()
    logging.getLogger().handlers.clear()
