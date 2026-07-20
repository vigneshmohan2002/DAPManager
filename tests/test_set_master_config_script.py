"""Static security contract for the Windows SSH config updater.

These checks deliberately inspect source rather than execute PowerShell.  The
normal CI job runs on Linux, while the helper is intended for Windows
PowerShell 5.1.  Runtime behaviour still needs a Windows smoke test, but the
properties most likely to leak credentials or corrupt ``config.json`` should
not regress silently on any platform.
"""

from __future__ import annotations

import re
from pathlib import Path

from src.config_keys import SECRET_KEYS


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "set-master-config.ps1"


def _source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _balanced_parenthesized(text: str, open_at: int) -> str:
    """Return one balanced parenthesized region, including its delimiters."""
    assert text[open_at] == "("
    depth = 0
    quote: str | None = None
    index = open_at

    while index < len(text):
        char = text[index]
        if quote is not None:
            if char == "`":
                index += 2
                continue
            if char == quote:
                # PowerShell escapes a quote inside the same quoted string by
                # doubling it (in addition to supporting the backtick).
                if index + 1 < len(text) and text[index + 1] == quote:
                    index += 2
                    continue
                quote = None
        elif char in {"'", '"'}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[open_at : index + 1]
        index += 1

    raise AssertionError("unbalanced PowerShell parentheses")


def _parameter_block(text: str) -> str:
    match = re.search(r"(?im)^\s*param\s*\(", text)
    assert match, "script must declare a top-level param block"
    return _balanced_parenthesized(text, text.find("(", match.start()))


def test_script_accepts_secret_payload_only_from_standard_input():
    source = _source()
    params = _parameter_block(source)

    assert re.search(
        r"\[\s*(?:system\.)?console\s*\]\s*::\s*openstandardinput\s*\(",
        source,
        re.IGNORECASE,
    ), "JSON payload must be read as raw stdin bytes"
    assert "MaximumEnvelopeBytes" in source
    assert re.search(r"\.read\s*\(\s*\$buffer", source, re.IGNORECASE)
    assert re.search(r"utf8encoding\s*\(\s*\$false\s*,\s*\$true", source, re.IGNORECASE)
    assert not re.search(r"\bread-host\b", source, re.IGNORECASE)

    # Parameter names are normalised before comparison so spelling variants
    # such as JellyfinApiKey and jellyfin_api_key are both caught.  Generic
    # names are forbidden too: a future ``-Password`` parameter would be just
    # as visible in process listings as one named after a config key.
    parameter_names = re.findall(r"\$([a-z][a-z0-9_-]*)", params, re.IGNORECASE)
    normalised_params = {
        re.sub(r"[^a-z0-9]", "", name.lower()) for name in parameter_names
    }
    for key in SECRET_KEYS:
        assert re.sub(r"[^a-z0-9]", "", key.lower()) not in normalised_params
    assert not any(
        re.search(r"password|passphrase|secret|token|apikey", name)
        for name in normalised_params
    )
    assert not any(
        re.search(r"^(?:input|json|patch|payload)(?:file|path)?$", name)
        for name in normalised_params
    ), "the secret JSON transport must not have a file/argument alternative"


def test_script_does_not_read_secret_environment_variables():
    source = _source()

    environment_names = {
        re.sub(r"[^a-z0-9]", "", name.lower())
        for name in re.findall(
            r"\$env\s*:\s*([a-z][a-z0-9_-]*)",
            source,
            re.IGNORECASE,
        )
    }
    for key in SECRET_KEYS:
        normalised_key = re.sub(r"[^a-z0-9]", "", key.lower())
        assert not any(
            normalised_key in env_name for env_name in environment_names
        ), f"{key} must arrive through stdin, not the process environment"
    assert not any(
        re.search(r"password|passphrase|secret|token|apikey", name)
        for name in environment_names
    )


def test_script_secret_key_contract_matches_application_schema_exactly():
    source = _source()
    # Collect identifier-like quoted literals that look sensitive.  This is
    # independent of the PowerShell variable name and tolerates either quote
    # style and arbitrary array formatting.
    sensitive_literals = {
        match.group("name").lower()
        for match in re.finditer(
            r"(?P<quote>['\"])(?P<name>[a-z][a-z0-9_]*_"
            r"(?:password|passphrase|secret|token|api_key))"
            r"(?P=quote)",
            source,
            re.IGNORECASE,
        )
    }

    assert sensitive_literals == set(SECRET_KEYS)


def test_script_validates_the_versioned_envelope_before_writing():
    source = _source()

    assert re.search(r"\bconvertfrom-json\b", source, re.IGNORECASE)
    for field in ("version", "set", "clear"):
        assert re.search(
            rf"['\"]{field}['\"]|\.\s*{field}\b",
            source,
            re.IGNORECASE,
        )
    assert re.search(r"unknown|unsupported", source, re.IGNORECASE)
    assert re.search(
        r"\$BooleanKeys\s*=.*?['\"]auto_tag_downloads['\"]",
        source,
        re.IGNORECASE | re.DOTALL,
    )
    assert re.search(
        r"\$BooleanKeys\s*=.*?['\"]lidarr_acquisition_handoff_enabled['\"]",
        source,
        re.IGNORECASE | re.DOTALL,
    )


def test_script_uses_atomic_bom_free_replace_with_a_backup():
    source = _source()

    assert re.search(
        r"\[\s*(?:system\.)?io\.file\s*\]\s*::\s*replace\s*\(",
        source,
        re.IGNORECASE,
    )
    assert re.search(
        r"(?:new-object\s+)?(?:\[\s*)?(?:system\.)?text\.utf8encoding"
        r"(?:\s*\])?(?:::new)?\s*\(\s*\$false\s*\)",
        source,
        re.IGNORECASE,
    ), "PowerShell 5.1's default UTF-8 writer emits a BOM"
    assert re.search(r"\bwritealltext\s*\(", source, re.IGNORECASE)
    assert re.search(r"\bbackup", source, re.IGNORECASE)


def test_script_restricts_config_and_backup_acls_to_expected_principals():
    source = _source()

    assert re.search(r"\bicacls(?:\.exe)?\b", source, re.IGNORECASE)
    assert "S-1-5-18" in source, "SYSTEM must retain access"
    assert "S-1-5-32-544" in source, "built-in Administrators must retain access"
    assert re.search(
        r"windowsidentity|\.user\s*\.\s*value",
        source,
        re.IGNORECASE,
    ), "the invoking user's SID must be granted access"
    assert re.search(r"/inheritance\s*:\s*r\b", source, re.IGNORECASE)
    assert re.search(r"/grant\s*:\s*r\b", source, re.IGNORECASE)


def test_restart_path_has_health_check_and_rollback_contract():
    source = _source()

    assert "/api/healthz" in source.lower()
    assert re.search(r"\bdocker(?:\.exe)?\b", source, re.IGNORECASE)
    assert re.search(r"\brestart\b", source, re.IGNORECASE)
    assert re.search(r"\brollback\b|\brestore", source, re.IGNORECASE)


def test_remote_usage_is_documented_as_an_ssh_stdin_pipeline():
    documentation = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "README.md", ROOT / "docs" / "agent-operations.md")
    )

    assert "set-master-config.ps1" in documentation
    assert re.search(r"\bssh\b", documentation, re.IGNORECASE)
    assert re.search(
        r"\bstdin\b|standard input",
        documentation,
        re.IGNORECASE,
    )
