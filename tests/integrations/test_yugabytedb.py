"""Unit tests for the YugabyteDB integration module."""

from integrations.yugabytedb import (
    YugabyteDBConfig,
    YugabyteDBValidationResult,
    _extract_yugabytedb_version,
    build_yugabytedb_config,
    yugabytedb_config_from_env,
)


class TestYugabyteDBConfig:
    """Tests for YugabyteDBConfig model."""

    def test_defaults(self) -> None:
        config = YugabyteDBConfig(host="localhost", database="testdb")
        assert config.host == "localhost"
        assert config.port == 5433
        assert config.database == "testdb"
        assert config.username == "yugabyte"
        assert config.password == ""
        assert config.ssl_mode == "prefer"
        assert config.timeout_seconds == 10.0
        assert config.max_results == 50

    def test_is_configured_with_host_and_database(self) -> None:
        config = YugabyteDBConfig(host="yb.example.com", database="mydb")
        assert config.is_configured is True

    def test_is_configured_without_host(self) -> None:
        config = YugabyteDBConfig(database="mydb")
        assert config.is_configured is False

    def test_is_configured_without_database(self) -> None:
        config = YugabyteDBConfig(host="localhost")
        assert config.is_configured is False

    def test_is_configured_without_host_and_database(self) -> None:
        config = YugabyteDBConfig()
        assert config.is_configured is False

    def test_normalize_host_strips_whitespace(self) -> None:
        config = YugabyteDBConfig(host="  yb.example.com  ", database="mydb")
        assert config.host == "yb.example.com"

    def test_normalize_empty_host(self) -> None:
        config = YugabyteDBConfig(host="", database="mydb")
        assert config.host == ""
        assert config.is_configured is False

    def test_normalize_database_strips_whitespace(self) -> None:
        config = YugabyteDBConfig(host="localhost", database="  mydb  ")
        assert config.database == "mydb"

    def test_normalize_empty_database(self) -> None:
        config = YugabyteDBConfig(host="localhost", database="")
        assert config.database == ""
        assert config.is_configured is False

    def test_normalize_username_default(self) -> None:
        config = YugabyteDBConfig(host="localhost", database="mydb", username="")
        assert config.username == "yugabyte"

    def test_normalize_ssl_mode_default(self) -> None:
        config = YugabyteDBConfig(host="localhost", database="mydb", ssl_mode="")
        assert config.ssl_mode == "prefer"

    def test_custom_values(self) -> None:
        config = YugabyteDBConfig(
            host="yb.prod.internal",
            port=5433,
            database="analytics",
            username="reader",
            password="secret",
            ssl_mode="require",
            timeout_seconds=30.0,
            max_results=100,
        )
        assert config.host == "yb.prod.internal"
        assert config.port == 5433
        assert config.database == "analytics"
        assert config.username == "reader"
        assert config.password == "secret"
        assert config.ssl_mode == "require"
        assert config.timeout_seconds == 30.0
        assert config.max_results == 100


class TestBuildYugabyteDBConfig:
    """Tests for build_yugabytedb_config helper."""

    def test_from_dict(self) -> None:
        config = build_yugabytedb_config(
            {"host": "yb.example.com", "database": "mydb", "port": 5433}
        )
        assert config.host == "yb.example.com"
        assert config.database == "mydb"
        assert config.port == 5433

    def test_from_none(self) -> None:
        config = build_yugabytedb_config(None)
        assert config.host == ""
        assert config.database == ""
        assert config.is_configured is False

    def test_from_empty_dict(self) -> None:
        config = build_yugabytedb_config({})
        assert config.host == ""
        assert config.database == ""
        assert config.is_configured is False


class TestYugabyteDBConfigFromEnv:
    """Tests for yugabytedb_config_from_env helper."""

    def test_returns_none_without_host(self) -> None:
        import os

        old_host = os.environ.get("YUGABYTEDB_HOST")
        old_database = os.environ.get("YUGABYTEDB_DATABASE")
        os.environ.pop("YUGABYTEDB_HOST", None)
        os.environ.pop("YUGABYTEDB_DATABASE", None)
        try:
            result = yugabytedb_config_from_env()
            assert result is None
        finally:
            if old_host is not None:
                os.environ["YUGABYTEDB_HOST"] = old_host
            if old_database is not None:
                os.environ["YUGABYTEDB_DATABASE"] = old_database

    def test_returns_none_without_database(self) -> None:
        import os

        old_host = os.environ.get("YUGABYTEDB_HOST")
        old_database = os.environ.get("YUGABYTEDB_DATABASE")
        os.environ["YUGABYTEDB_HOST"] = "localhost"
        os.environ.pop("YUGABYTEDB_DATABASE", None)
        try:
            result = yugabytedb_config_from_env()
            assert result is None
        finally:
            if old_host is not None:
                os.environ["YUGABYTEDB_HOST"] = old_host
            else:
                os.environ.pop("YUGABYTEDB_HOST", None)
            if old_database is not None:
                os.environ["YUGABYTEDB_DATABASE"] = old_database

    def test_returns_config_with_host_and_database(self) -> None:
        import os

        os.environ["YUGABYTEDB_HOST"] = "yb.test.local"
        os.environ["YUGABYTEDB_PORT"] = "5433"
        os.environ["YUGABYTEDB_DATABASE"] = "testdb"
        os.environ["YUGABYTEDB_USERNAME"] = "testuser"
        os.environ["YUGABYTEDB_PASSWORD"] = "testpass"
        os.environ["YUGABYTEDB_SSL_MODE"] = "require"
        try:
            config = yugabytedb_config_from_env()
            assert config is not None
            assert config.host == "yb.test.local"
            assert config.port == 5433
            assert config.database == "testdb"
            assert config.username == "testuser"
            assert config.password == "testpass"
            assert config.ssl_mode == "require"
        finally:
            for key in [
                "YUGABYTEDB_HOST",
                "YUGABYTEDB_PORT",
                "YUGABYTEDB_DATABASE",
                "YUGABYTEDB_USERNAME",
                "YUGABYTEDB_PASSWORD",
                "YUGABYTEDB_SSL_MODE",
            ]:
                os.environ.pop(key, None)


class TestYugabyteDBValidationResult:
    """Tests for YugabyteDBValidationResult dataclass."""

    def test_ok_result(self) -> None:
        result = YugabyteDBValidationResult(
            ok=True, detail="Connected to YugabyteDB 2.20.0.0; target database: mydb."
        )
        assert result.ok is True
        assert result.detail == "Connected to YugabyteDB 2.20.0.0; target database: mydb."

    def test_error_result(self) -> None:
        result = YugabyteDBValidationResult(
            ok=False, detail="YugabyteDB connection failed: connection refused"
        )
        assert result.ok is False
        assert result.detail == "YugabyteDB connection failed: connection refused"


class TestExtractYugabyteDBVersion:
    """Tests for _extract_yugabytedb_version, YSQL's compound version() parser.

    YSQL's ``SELECT version()`` returns a compound string where the
    PostgreSQL-compat version and the YB build version are concatenated with
    ``-YB-`` inside a single whitespace-delimited token, so naively reusing
    PostgreSQL's ``version_info.split()[1]`` would yield the mangled token
    instead of a clean version.
    """

    def test_parses_compound_version_string(self) -> None:
        version_info = "PostgreSQL 11.2-YB-2.20.0.0-b0 on x86_64-pc-linux-gnu, compiled by gcc"
        assert _extract_yugabytedb_version(version_info) == "2.20.0.0 (PG 11.2 compat, build 0)"

    def test_returns_unknown_for_none(self) -> None:
        assert _extract_yugabytedb_version(None) == "unknown"

    def test_returns_unknown_for_empty_string(self) -> None:
        assert _extract_yugabytedb_version("") == "unknown"

    def test_returns_unknown_for_non_yugabytedb_version_string(self) -> None:
        # A vanilla PostgreSQL server's version() string has no -YB- segment.
        version_info = "PostgreSQL 16.1 on x86_64-pc-linux-gnu, compiled by gcc"
        assert _extract_yugabytedb_version(version_info) == "unknown"
