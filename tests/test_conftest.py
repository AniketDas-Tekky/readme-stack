from pathlib import Path


def test_git_repo_is_empty_repo(git_repo):
    assert isinstance(git_repo, Path)
    assert (git_repo / ".git").is_dir()
    assert git_repo.git("rev-parse", "--is-inside-work-tree").strip() == "true"
    assert git_repo.git("config", "user.email").strip() == "test@example.com"
    assert git_repo.git("branch", "--show-current").strip() == "main"


def test_derived_paths_are_plain(git_repo):
    assert type(git_repo / "x") is type(Path())
    assert type(git_repo.parent) is type(Path())


def test_commit_and_diff_range(git_repo):
    first = git_repo.commit({"a.txt": "one\n"}, "first")
    second = git_repo.commit({"a.txt": "two\n", "src/pkg/mod.py": "x = 1\n"}, "second")
    assert first != second
    assert git_repo.git("rev-parse", "HEAD").strip() == second
    stat = git_repo.git("diff", "--stat", "HEAD~1..HEAD")
    assert "a.txt" in stat
    assert "src/pkg/mod.py" in stat


def test_empty_commit(git_repo):
    git_repo.commit(message="initial")
    assert len(git_repo.git("log", "--oneline").splitlines()) == 1


def test_gitignore_and_untracked(git_repo):
    git_repo.commit({".gitignore": "ignored.txt\n", "tracked.txt": "t\n"}, "init")
    git_repo.write({"ignored.txt": "i\n", "untracked.txt": "u\n", "bin.dat": b"\x00\x01"})
    listed = git_repo.git("ls-files", "-co", "--exclude-standard").split()
    assert "tracked.txt" in listed
    assert "untracked.txt" in listed
    assert "bin.dat" in listed
    assert "ignored.txt" not in listed
    assert (git_repo / "bin.dat").read_bytes() == b"\x00\x01"
