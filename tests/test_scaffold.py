import crgraph


def test_package_has_version():
    assert crgraph.__version__ == "0.4.0"


def test_fixture_makes_commits(repo):
    repo.commit({"a.txt": "1"})
    repo.commit({"a.txt": "2"})
    assert repo.git("rev-list", "--count", "HEAD") == "2"
