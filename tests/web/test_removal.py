import socket
from datetime import UTC, datetime
from pathlib import Path

import pytest

from typantic.web import _removal
from typantic.web import launcher as launcher_mod
from typantic.web._paths import expand
from typantic.web.backends.base import Launched, PollResult
from typantic.web.launcher import Launcher
from typantic.web.models import CommandMeta, JobStatus, LaunchRequest
from typantic.web.store import (
    FolderNotRemovedError,
    JobStore,
    remove_output_folder,
)

META = CommandMeta(app="app", command="run", argv=("run",), title="Run")


class FakeBackend:
    output_paths_are_local = True

    def __init__(self):
        self.cancelled = []
        self.cancel_error = None

    def launch(self, argv, *, job_dir, log_path, backend_options):
        log_path.write_text("hello\n")
        return Launched(pid=4321, status=JobStatus.RUNNING)

    def poll(self, record):
        return PollResult(status=JobStatus.RUNNING)

    def cancel(self, record):
        if self.cancel_error is not None:
            raise self.cancel_error
        self.cancelled.append(record.id)

    def preview(self, argv, *, job_dir, log_path, backend_options):
        return "PREVIEW"


@pytest.fixture
def wired(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher_mod, "discover_commands", lambda: [META])
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    store = JobStore(tmp_path / "jobs")
    backend = FakeBackend()
    return Launcher(store, backends={"local": backend}), backend, store


def _launch(launcher, output=None, **over):
    values = {} if output is None else {"output_folder": str(output)}
    request = LaunchRequest(
        command_key="app/run",
        backend="local",
        values=values,
        **over,
    )
    return launcher.launch(request)


def _finish(store, record, **over):
    finished = record.model_copy(
        update={"status": JobStatus.DONE, "finished_at": datetime.now(UTC), **over},
    )
    store.save(finished)
    return finished


def _output(tmp_path, name="out"):
    folder = tmp_path / "results" / name
    folder.mkdir(parents=True)
    (folder / "model.pt").write_text("weights")
    return folder


def _confirm(launcher, job_id, **kwargs):
    """Delete a job, confirming the folder its removal names, as the dashboard does."""
    removal = launcher.removal_for(job_id)
    folders = [removal.output_folder] if removal.removes_output else []
    return launcher.delete(job_id, output_folders=folders, **kwargs)


# --- a folder goes only when it was confirmed ---


def test_a_confirmed_folder_goes(wired, tmp_path):
    launcher, _, store = wired
    output = _output(tmp_path)
    record = _finish(store, _launch(launcher, output))
    assert _confirm(launcher, record.id)
    assert not output.exists()
    assert not store.job_dir(record.id).exists()


def test_without_a_confirmation_the_folder_stays(wired, tmp_path):
    launcher, _, store = wired
    output = _output(tmp_path)
    record = _finish(store, _launch(launcher, output))
    assert launcher.removal_for(record.id).removes_output
    assert launcher.delete(record.id)
    assert (output / "model.pt").exists()
    assert store.load(record.id) is None


def test_a_different_path_is_never_removed(wired, tmp_path):
    launcher, _, store = wired
    output = _output(tmp_path, "mine")
    bystander = _output(tmp_path, "theirs")
    record = _finish(store, _launch(launcher, output))
    with pytest.raises(FolderNotRemovedError, match="was kept"):
        launcher.delete(record.id, output_folders=[str(bystander)])
    assert (bystander / "model.pt").exists()
    assert (output / "model.pt").exists()
    assert store.load(record.id) is None


def test_a_spelling_other_than_the_one_shown_is_not_a_confirmation(wired, tmp_path):
    launcher, _, store = wired
    output = _output(tmp_path)
    record = _finish(store, _launch(launcher, output))
    shown = launcher.removal_for(record.id).output_folder
    with pytest.raises(FolderNotRemovedError, match="was kept"):
        launcher.delete(record.id, output_folders=[f"{shown}/."])
    assert output.exists()


def test_confirming_a_folder_already_gone_is_fine(wired, tmp_path):
    launcher, _, store = wired
    record = _finish(store, _launch(launcher))
    assert launcher.delete(record.id, output_folders=[str(tmp_path / "gone")])


def test_a_nul_in_a_confirmed_path_is_only_a_mismatch(wired):
    launcher, _, store = wired
    record = _finish(store, _launch(launcher))
    assert launcher.delete(record.id, output_folders=["/tmp/a\0b"])


def test_delete_cancels_then_removes_an_active_jobs_output(wired, tmp_path):
    launcher, backend, _ = wired
    output = _output(tmp_path)
    record = _launch(launcher, output)
    assert _confirm(launcher, record.id)
    assert backend.cancelled == [record.id]
    assert not output.exists()


def test_delete_of_a_job_that_is_not_there(wired):
    launcher, _, _ = wired
    assert not launcher.delete("nope")


def test_removal_for_unknown_job(wired):
    launcher, _, _ = wired
    assert launcher.removal_for("nope") is None


def test_a_job_gone_before_its_turn_is_skipped(wired):
    launcher, _, _ = wired
    assert launcher._delete_jobs(["nope"], []) == []


# --- what can change after the dialog ---


def test_a_job_that_could_not_be_stopped_keeps_the_confirmed_folder(wired, tmp_path):
    launcher, backend, store = wired
    backend.cancel_error = OSError("no")
    output = _output(tmp_path)
    record = _launch(launcher, output)
    with pytest.raises(FolderNotRemovedError, match="was kept"):
        _confirm(launcher, record.id)
    assert (output / "model.pt").exists()
    assert store.load(record.id) is None


def test_an_unstoppable_job_without_an_output_folder_just_goes(wired):
    launcher, backend, store = wired
    backend.cancel_error = OSError("no")
    record = _launch(launcher)
    assert launcher.delete(record.id)
    assert store.load(record.id) is None


def test_an_uninstalled_backend_counts_as_not_stopped(wired, tmp_path):
    launcher, _, store = wired
    output = _output(tmp_path)
    record = _launch(launcher, output)
    store.save(record.model_copy(update={"backend": "gone"}))
    with pytest.raises(FolderNotRemovedError, match="was kept"):
        launcher.delete(record.id, output_folders=[str(output.resolve())])
    assert (output / "model.pt").exists()


def test_a_job_launched_into_the_folder_after_the_dialog_keeps_it(wired, tmp_path):
    launcher, _, store = wired
    output = _output(tmp_path)
    record = _finish(store, _launch(launcher, output))
    shown = launcher.removal_for(record.id).output_folder
    newcomer = _launch(launcher, output)
    with pytest.raises(FolderNotRemovedError, match="was kept"):
        launcher.delete(record.id, output_folders=[shown])
    assert (output / "model.pt").exists()
    assert store.load(newcomer.id) is not None


def test_a_job_launched_while_jobs_are_being_deleted_keeps_the_folder(
    wired,
    tmp_path,
    monkeypatch,
):
    launcher, _, store = wired
    output = _output(tmp_path)
    record = _finish(store, _launch(launcher, output))
    shown = launcher.removal_for(record.id).output_folder
    real = store.delete

    def delete_then_launch(job_id):
        deleted = real(job_id)
        _launch(launcher, output)
        return deleted

    monkeypatch.setattr(store, "delete", delete_then_launch)
    with pytest.raises(FolderNotRemovedError, match="was kept"):
        launcher.delete(record.id, output_folders=[shown])
    assert (output / "model.pt").exists()


# --- when the output folder stays, whatever is confirmed ---


def _stays(launcher, record, target, reason):
    removal = launcher.removal_for(record.id)
    assert not removal.removes_output
    assert reason in removal.kept_reason
    with pytest.raises(FolderNotRemovedError, match="was kept"):
        launcher.delete(record.id, output_folders=[str(target), removal.output_folder])
    assert target.exists()


@pytest.mark.parametrize(
    ("output", "reason"),
    [
        (lambda t: t, "contains the job store"),
        (lambda t: t / "jobs", "inside the job store"),
        (lambda t: t / "jobs" / "another", "inside the job store"),
    ],
)
def test_the_job_store_stays(wired, tmp_path, output, reason):
    launcher, _, store = wired
    target = output(tmp_path)
    target.mkdir(exist_ok=True, parents=True)
    record = _finish(store, _launch(launcher, target))
    _stays(launcher, record, target, reason)
    assert store.root.exists()


def test_home_and_what_contains_it_stay(tmp_path, monkeypatch):
    home = tmp_path / "base" / "home"
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(launcher_mod, "discover_commands", lambda: [META])
    store = JobStore(tmp_path / "elsewhere" / "jobs")
    launcher = Launcher(store, backends={"local": FakeBackend()})
    for target in (home, home.parent):
        record = _finish(store, _launch(launcher, target))
        _stays(launcher, record, target, "contains your home folder")


def test_folders_are_told_apart_by_identity_not_spelling(wired, tmp_path, monkeypatch):
    # A case-insensitive filesystem or a firmlink gives one folder a second name
    # that resolve() does not normalise; an unresolved symlinked path has one too.
    launcher, _, store = wired
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path)
    monkeypatch.setattr(launcher_mod, "resolved", lambda path: path)
    monkeypatch.setattr(_removal, "resolved", lambda path: path)
    for target, reason in (
        (alias / "home", "contains your home folder"),
        (alias / "jobs", "inside the job store"),
    ):
        record = _finish(store, _launch(launcher, target))
        removal = launcher.removal_for(record.id)
        assert reason in removal.kept_reason
        assert not removal.removes_output
    other = _output(tmp_path, "shared")
    _launch(launcher, other)
    record = _finish(store, _launch(launcher, alias / "results" / "shared"))
    assert "overlaps" in launcher.removal_for(record.id).kept_reason


def test_what_really_holds_a_symlinked_home_stays(tmp_path, monkeypatch):
    # A cluster's /home is often a symlink onto deeper storage.
    (tmp_path / "vol" / "nfs" / "users" / "me").mkdir(parents=True)
    (tmp_path / "home_link").symlink_to(tmp_path / "vol" / "nfs" / "users")
    monkeypatch.setenv("HOME", str(tmp_path / "home_link" / "me"))
    monkeypatch.setattr(launcher_mod, "discover_commands", lambda: [META])
    store = JobStore(tmp_path / "elsewhere" / "jobs")
    launcher = Launcher(store, backends={"local": FakeBackend()})
    target = tmp_path / "vol" / "nfs"
    record = _finish(store, _launch(launcher, target))
    _stays(launcher, record, target, "contains your home folder")


def test_what_really_holds_a_symlinked_store_stays(tmp_path, monkeypatch):
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(launcher_mod, "discover_commands", lambda: [META])
    (tmp_path / "vol" / "deep").mkdir(parents=True)
    (tmp_path / "link").symlink_to(tmp_path / "vol" / "deep")
    store = JobStore(tmp_path / "link" / "jobs")
    launcher = Launcher(store, backends={"local": FakeBackend()})
    target = tmp_path / "vol"
    record = _finish(store, _launch(launcher, target))
    _stays(launcher, record, target, "contains the job store")


def test_a_macos_system_folder_stays(wired, tmp_path, monkeypatch):
    launcher, _, store = wired
    output = _output(tmp_path)
    record = _finish(store, _launch(launcher, output))
    monkeypatch.setattr(_removal, "_MACOS_SYSTEM", output.parent)
    _stays(launcher, record, output, "macOS system folder")


def test_a_mount_point_stays(wired, tmp_path, monkeypatch):
    launcher, _, store = wired
    output = _output(tmp_path)
    record = _finish(store, _launch(launcher, output))
    real = _removal.os.path.ismount
    monkeypatch.setattr(
        _removal.os.path,
        "ismount",
        lambda path: path == output.resolve() or real(path),
    )
    _stays(launcher, record, output, "mount point")


def test_a_config_nested_too_deep_breaks_nothing_else(wired, tmp_path):
    launcher, _, store = wired
    other = _launch(launcher)
    store.config_path(other.id).write_text("[" * 300000)
    output = _output(tmp_path)
    record = _finish(store, _launch(launcher, output))
    assert _confirm(launcher, record.id)
    assert not output.exists()


def test_a_missing_home_keeps_the_folder(wired, tmp_path, monkeypatch):
    launcher, _, store = wired
    output = _output(tmp_path)
    record = _finish(store, _launch(launcher, output))
    monkeypatch.setenv("HOME", str(tmp_path / "no-such-home"))
    assert "home folder" in launcher.removal_for(record.id).kept_reason


def test_an_undeterminable_home_keeps_the_folder(wired, tmp_path, monkeypatch):
    launcher, _, store = wired
    output = _output(tmp_path)
    record = _finish(store, _launch(launcher, output))
    real = _removal.expand
    monkeypatch.setattr(
        _removal,
        "expand",
        lambda path: None if path == "~" else real(path),
    )
    assert "home folder" in launcher.removal_for(record.id).kept_reason


def test_a_top_level_folder_stays(wired):
    launcher, _, store = wired
    record = _finish(store, _launch(launcher, "/usr"))
    assert "top-level" in launcher.removal_for(record.id).kept_reason


def test_a_two_level_folder_is_not_top_level(tmp_path):
    # A real tmp_path resolves deeper than this, so the folder is named directly.
    target = Path("/scratch/results")
    target_id = _removal.FolderId((-1, -1), frozenset({(-1, -1)}))
    assert _removal._location_reason(target, target, target_id, tmp_path, {}) is None


def test_a_symlink_is_not_followed(wired, tmp_path):
    launcher, _, store = wired
    real = _output(tmp_path)
    link = tmp_path / "results" / "link"
    link.symlink_to(real)
    record = _finish(store, _launch(launcher, link))
    assert "symbolic link" in launcher.removal_for(record.id).kept_reason
    with pytest.raises(FolderNotRemovedError, match="was kept"):
        launcher.delete(record.id, output_folders=[str(link), str(real.resolve())])
    assert (real / "model.pt").exists()


def test_tilde_is_kept_as_the_job_may_not_have_expanded_it(wired, tmp_path):
    launcher, _, store = wired
    thesis = tmp_path / "home" / "thesis"
    thesis.mkdir()
    record = _finish(store, _launch(launcher, "~/thesis"))
    assert "starts with ~" in launcher.removal_for(record.id).kept_reason
    launcher.delete(record.id)
    assert thesis.exists()


def test_dotdot_cannot_step_around_the_symlink_check(wired, tmp_path):
    launcher, _, store = wired
    real = _output(tmp_path)
    (real / "sub").mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    record = _finish(store, _launch(launcher, f"{link}/sub/.."))
    assert ".." in launcher.removal_for(record.id).kept_reason


@pytest.mark.parametrize("nested", [False, True])
def test_a_folder_another_job_uses_stays(wired, tmp_path, nested):
    launcher, _, store = wired
    output = _output(tmp_path)
    other = output / "sub" if nested else output
    keeper = _launch(launcher, other)
    record = _finish(store, _launch(launcher, output))
    removal = launcher.removal_for(record.id)
    assert keeper.id in removal.kept_reason
    launcher.delete(record.id)
    assert (output / "model.pt").exists()


def test_another_jobs_unrelated_folder_does_not_stop_the_removal(wired, tmp_path):
    launcher, _, store = wired
    _launch(launcher, _output(tmp_path, "theirs"))
    _launch(launcher)  # no output folder at all
    output = _output(tmp_path, "mine")
    record = _finish(store, _launch(launcher, output))
    assert _confirm(launcher, record.id)
    assert not output.exists()


def test_a_backend_that_does_not_vouch_for_its_paths_keeps_them(wired, tmp_path):
    launcher, backend, store = wired
    backend.output_paths_are_local = False
    output = _output(tmp_path)
    record = _finish(store, _launch(launcher, output))
    assert "local backend" in launcher.removal_for(record.id).kept_reason


def test_a_job_from_another_host_keeps_its_path_here(wired, tmp_path):
    launcher, _, store = wired
    output = _output(tmp_path)
    record = _finish(store, _launch(launcher, output), host="another-node")
    assert "another-node" in launcher.removal_for(record.id).kept_reason


def test_a_job_from_this_host_does_not(wired, tmp_path):
    launcher, _, store = wired
    output = _output(tmp_path)
    record = _finish(store, _launch(launcher, output), host=socket.gethostname())
    assert launcher.removal_for(record.id).removes_output


def test_an_uninstalled_backend_keeps_the_folder(wired, tmp_path):
    launcher, _, store = wired
    output = _output(tmp_path)
    record = _launch(launcher, output)
    store.save(record.model_copy(update={"backend": "gone"}))
    assert launcher.removal_for(record.id).kept_reason is not None


@pytest.mark.parametrize("backend", ["ssh", "docker", "podman", "apptainer"])
def test_the_builtin_remote_and_container_backends_keep_paths(backend):
    backends = launcher_mod.load_backends()
    assert not getattr(backends[backend], "output_paths_are_local", False)


@pytest.mark.parametrize("backend", ["local", "slurm", "pbs"])
def test_the_builtin_host_backends_vouch_for_paths(backend):
    assert launcher_mod.load_backends()[backend].output_paths_are_local


def test_a_third_party_scheduler_does_not_inherit_the_promise():
    from typantic.web.backends.scheduler import SchedulerBackend  # noqa: PLC0415

    assert not hasattr(SchedulerBackend, "output_paths_are_local")


def test_an_unresolvable_path_stays(wired, tmp_path, monkeypatch):
    launcher, _, store = wired
    output = _output(tmp_path)
    record = _finish(store, _launch(launcher, output))
    monkeypatch.setattr(_removal, "resolved", lambda _path: None)
    assert "resolved" in launcher.removal_for(record.id).kept_reason


@pytest.mark.parametrize(
    "config",
    [
        "not json",
        "[1]",
        '{"output_folder": 3}',
        '{"output_folder": "relative/dir"}',
        '{"output_folder": "~nosuchuserhere/x"}',
        '{"output_folder": "~\\u0000x"}',
    ],
)
def test_nothing_usable_means_only_the_job_folder_goes(wired, config):
    launcher, _, store = wired
    record = _launch(launcher)
    store.config_path(record.id).write_text(config)
    removal = launcher.removal_for(record.id)
    assert removal.output_folder is None
    assert not removal.removes_output


def test_expanding_a_nul_answers_instead_of_raising():
    assert expand("~\0x") is None


def test_a_missing_output_folder_is_nothing_to_remove(wired, tmp_path):
    launcher, _, _ = wired
    record = _launch(launcher, tmp_path / "results" / "never")
    assert launcher.removal_for(record.id).output_folder is None


def test_an_output_inside_the_job_folder_just_goes_with_it(wired):
    launcher, _, store = wired
    record = _launch(launcher)
    nested = store.job_dir(record.id) / "out"
    nested.mkdir()
    store.config_path(record.id).write_text(f'{{"output_folder": "{nested}"}}')
    assert launcher.removal_for(record.id).output_folder is None


# --- projects ---


def _confirm_project(launcher, project_id):
    folders = [
        removal.output_folder
        for removal in launcher.project_removal(project_id)
        if removal.removes_output
    ]
    return launcher.delete_project(project_id, output_folders=folders)


def test_project_removal_unknown(wired):
    launcher, _, _ = wired
    assert launcher.project_removal("nope") is None


def test_a_project_removes_a_folder_only_its_jobs_share(wired, tmp_path):
    launcher, _, store = wired
    project = store.create_project("P")
    output = _output(tmp_path)
    _finish(store, _launch(launcher, output, project_id=project.id))
    _finish(store, _launch(launcher, output, project_id=project.id))
    assert {r.removes_output for r in launcher.project_removal(project.id)} == {True}
    assert _confirm_project(launcher, project.id)
    assert not output.exists()


def test_a_project_removes_nested_folders_as_planned(wired, tmp_path):
    launcher, _, store = wired
    project = store.create_project("P")
    outer = _output(tmp_path)
    inner = outer / "sub"
    inner.mkdir()
    _finish(store, _launch(launcher, outer, project_id=project.id))
    _finish(store, _launch(launcher, inner, project_id=project.id))
    assert all(r.removes_output for r in launcher.project_removal(project.id))
    assert _confirm_project(launcher, project.id)
    assert not outer.exists()


@pytest.mark.parametrize("same_path", [False, True])
def test_a_project_does_not_remove_what_holds_a_siblings_kept_folder(
    wired,
    tmp_path,
    same_path,
):
    launcher, _, store = wired
    project = store.create_project("P")
    outer = _output(tmp_path)
    inner = outer if same_path else outer / "keepme"
    inner.mkdir(exist_ok=True)
    launcher._backends["docker"] = type(
        "Docker",
        (FakeBackend,),
        {"output_paths_are_local": False},
    )()
    _finish(store, _launch(launcher, outer, project_id=project.id))
    kept = _launch(launcher, inner, project_id=project.id)
    store.save(kept.model_copy(update={"backend": "docker"}))
    _finish(store, store.load(kept.id))
    removals = launcher.project_removal(project.id)
    assert not any(r.removes_output for r in removals)
    assert all(r.kept_reason for r in removals)
    assert _confirm_project(launcher, project.id)
    assert inner.exists()
    assert (outer / "model.pt").exists()


def test_a_kept_folder_passes_on_through_what_overlaps_it(wired, tmp_path):
    # keepme stays, so outer does, so what is inside outer does too.
    launcher, _, store = wired
    project = store.create_project("P")
    outer = _output(tmp_path)
    (outer / "keepme").mkdir()
    (outer / "c").mkdir()
    launcher._backends["docker"] = type(
        "Docker",
        (FakeBackend,),
        {"output_paths_are_local": False},
    )()
    _finish(store, _launch(launcher, outer, project_id=project.id))
    _finish(store, _launch(launcher, outer / "c", project_id=project.id))
    kept = _launch(launcher, outer / "keepme", project_id=project.id)
    _finish(store, kept, backend="docker")
    assert not any(r.removes_output for r in launcher.project_removal(project.id))


def test_a_confirmed_folder_holding_a_siblings_kept_one_still_stays(wired, tmp_path):
    # The dialog can be older than a sibling's folder starting to have to stay.
    launcher, _, store = wired
    project = store.create_project("P")
    outer = _output(tmp_path)
    inner = outer / "keepme"
    inner.mkdir()
    _finish(store, _launch(launcher, outer, project_id=project.id))
    kept = _launch(launcher, inner, project_id=project.id)
    _finish(store, kept, backend="gone")
    with pytest.raises(FolderNotRemovedError, match="was kept"):
        launcher.delete_project(project.id, output_folders=[str(outer.resolve())])
    assert inner.exists()


def test_a_sibling_with_nothing_there_yet_does_not_keep_the_folder(wired, tmp_path):
    launcher, _, store = wired
    project = store.create_project("P")
    outer = _output(tmp_path)
    _finish(store, _launch(launcher, outer, project_id=project.id))
    _finish(store, _launch(launcher, outer / "never", project_id=project.id))
    assert _confirm_project(launcher, project.id)
    assert not outer.exists()


def test_a_project_folder_shared_with_a_job_outside_stays(wired, tmp_path):
    launcher, _, store = wired
    project = store.create_project("P")
    output = _output(tmp_path)
    _finish(store, _launch(launcher, output, project_id=project.id))
    outsider = _launch(launcher, output)
    removals = launcher.project_removal(project.id)
    assert outsider.id in removals[0].kept_reason
    assert _confirm_project(launcher, project.id)
    assert (output / "model.pt").exists()


def test_a_sibling_cannot_take_the_folder_of_a_job_that_would_not_stop(wired, tmp_path):
    launcher, backend, store = wired
    project = store.create_project("P")
    output = _output(tmp_path)
    _launch(launcher, output, project_id=project.id)
    _finish(store, _launch(launcher, output, project_id=project.id))
    folders = [r.output_folder for r in launcher.project_removal(project.id)]
    backend.cancel_error = OSError("no")
    with pytest.raises(FolderNotRemovedError, match="was kept"):
        launcher.delete_project(project.id, output_folders=folders)
    assert (output / "model.pt").exists()
    assert store.get_project(project.id) is None


def test_a_project_error_names_the_folder(wired, tmp_path):
    launcher, _, store = wired
    project = store.create_project("P")
    output = _output(tmp_path)
    _finish(store, _launch(launcher, output, project_id=project.id))
    output.chmod(0o500)
    try:
        with pytest.raises(FolderNotRemovedError, match=str(output.resolve())):
            _confirm_project(launcher, project.id)
    finally:
        output.chmod(0o700)
    assert store.get_project(project.id) is None


# --- folders that will not go ---


def test_an_output_that_will_not_go_is_reported(wired, tmp_path):
    launcher, _, store = wired
    output = _output(tmp_path)
    record = _finish(store, _launch(launcher, output))
    output.chmod(0o500)
    try:
        with pytest.raises(FolderNotRemovedError, match="output folder"):
            _confirm(launcher, record.id)
        assert store.load(record.id) is None
    finally:
        output.chmod(0o700)


def test_both_folders_failing_reports_both(wired, tmp_path):
    launcher, _, store = wired
    output = _output(tmp_path)
    record = _finish(store, _launch(launcher, output))
    locked = store.job_dir(record.id) / "out"
    locked.mkdir()
    (locked / "x").write_text("x")
    locked.chmod(0o500)
    output.chmod(0o500)
    try:
        with pytest.raises(FolderNotRemovedError) as caught:
            _confirm(launcher, record.id)
        assert "output folder" in str(caught.value)
        assert "its folder" in str(caught.value)
    finally:
        locked.chmod(0o700)
        output.chmod(0o700)


def test_a_job_folder_that_will_not_go_still_removes_the_output(wired, tmp_path):
    launcher, _, store = wired
    output = _output(tmp_path)
    record = _finish(store, _launch(launcher, output))
    locked = store.job_dir(record.id) / "out"
    locked.mkdir()
    (locked / "x").write_text("x")
    locked.chmod(0o500)
    try:
        with pytest.raises(FolderNotRemovedError, match="its folder"):
            _confirm(launcher, record.id)
        assert not output.exists()
    finally:
        locked.chmod(0o700)


def test_remove_output_folder_missing_is_fine(tmp_path):
    remove_output_folder(tmp_path / "gone")
