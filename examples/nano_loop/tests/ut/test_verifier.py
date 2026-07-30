from app.schema import VerifySpec
from app.verifier import CommandVerifier


def test_command_verifier_collects_passing_evidence(tmp_path):
    verifier = CommandVerifier(VerifySpec(commands=["test -d .", "printf ok"]))
    result = verifier.verify(str(tmp_path))
    assert result.passed is True
    assert len(result.evidence) == 2
    assert result.evidence[1].output == "ok"


def test_command_verifier_returns_failed_output(tmp_path):
    verifier = CommandVerifier(
        VerifySpec(commands=["printf broken >&2; exit 7"])
    )
    result = verifier.verify(str(tmp_path))
    assert result.passed is False
    assert result.evidence[0].exit_code == 7
    assert "broken" in result.feedback


def test_empty_verifier_fails_closed(tmp_path):
    result = CommandVerifier(VerifySpec()).verify(str(tmp_path))
    assert result.passed is False
    assert result.evidence[0].kind == "configuration"
