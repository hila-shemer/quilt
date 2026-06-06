"""P3 Task 3: propose-push HARD SAFETY GATE (spec §6.7, principle §2.6)."""
import pytest

from quilt import gates, gitio
from quilt.loom import pushgate

VALID = "git@github.com:hila-shemer/e2.git"
MAJESTIC = "git@github.com:Majestic/e2.git"


def cfg():
    return gates.Config(base="main", branches=[], gates=[], targets={})


@pytest.fixture
def staged(repo):
    """next_staging two commits ahead of main."""
    repo.branch("work")
    repo.commit_file("f1.txt", "1\n")
    tip = repo.commit_file("f2.txt", "2\n")
    repo.git("checkout", "-q", "main")
    gitio.update_ref(repo.path, pushgate.NEXT_STAGING_REF, tip)
    return tip


def test_propose_returns_proposal_and_pushes_nothing(repo, staged):
    p = pushgate.propose(repo.path, cfg(), url=VALID, ref=pushgate.NEXT_STAGING_REF)
    assert p.blocked is False
    assert p.url == VALID
    assert p.commit == staged
    assert p.branch and p.diffstat
    assert p.artifacts == []
    # no push happened: no remotes were configured on the repo
    assert gitio.git(repo.path, "remote") == ""


def test_rejects_majestic_url(repo, staged):
    with pytest.raises(pushgate.PushBlocked):
        pushgate.propose(repo.path, cfg(), url=MAJESTIC, ref=pushgate.NEXT_STAGING_REF)


def test_rejects_non_hila_url(repo, staged):
    with pytest.raises(pushgate.PushBlocked):
        pushgate.propose(repo.path, cfg(), url="git@github.com:someone/e2.git",
                         ref=pushgate.NEXT_STAGING_REF)


def test_emits_patches_on_block(repo, staged, tmp_path):
    p = pushgate.propose(repo.path, cfg(), url=MAJESTIC,
                         ref=pushgate.NEXT_STAGING_REF, outdir=tmp_path)
    assert p.blocked is True and p.reason
    assert p.artifacts and all(a.suffix == ".patch" for a in p.artifacts)
    assert all(a.exists() for a in p.artifacts)
    assert len(p.artifacts) == 2          # two commits in main..next_staging


def test_never_pushes_staging(repo, staged):
    with pytest.raises(pushgate.NotPushable):
        pushgate.propose(repo.path, cfg(), url=VALID, ref=pushgate.STAGING_REF)


def test_staging_refused_even_with_outdir(repo, staged, tmp_path):
    # the staging ban is unconditional — it precedes the URL/patch path.
    with pytest.raises(pushgate.NotPushable):
        pushgate.propose(repo.path, cfg(), url=MAJESTIC,
                         ref=pushgate.STAGING_REF, outdir=tmp_path)
