"""SSH backend: run a job on a remote host over ``ssh``, streaming output back.

The command runs on the remote host; ``ssh``'s local stdout/stderr *is* the
remote command's output, so the launcher captures it into the job log exactly as
for a local job, and the local ``ssh`` client's exit status propagates the remote
exit code. Assumes a shared filesystem: the ``--config`` path the launcher writes
must be valid on the remote host (an NFS home directory is the common case).
Cancelling SIGTERMs the local ``ssh`` client; without a remote pty the remote
process may linger — a known streamed-mode limitation.
"""

import shlex
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from typantic.web.backends.process import ProcessBackend


class SshOptions(BaseModel):
    """Where and how to ssh for a job."""

    model_config = ConfigDict(extra="forbid")

    host: str = Field(description="Remote host to ssh into.")
    user: str | None = Field(default=None, description="SSH user (else ssh config).")
    port: int | None = Field(default=None, ge=1, le=65535, description="SSH port.")
    identity: str | None = Field(default=None, description="Path to an identity key.")
    directory: str | None = Field(default=None, description="Remote working directory.")

    @property
    def target(self) -> str:
        """The ``[user@]host`` ssh destination."""
        return f"{self.user}@{self.host}" if self.user else self.host


class SshBackend(ProcessBackend):
    """Run a job on a remote host over ssh."""

    options_model: ClassVar[type[BaseModel]] = SshOptions

    def _wrap(
        self,
        argv: list[str],
        *,
        job_dir: Path,  # noqa: ARG002 - the command runs remotely
        backend_options: dict[str, Any],
    ) -> list[str]:
        """Wrap ``argv`` as an ssh invocation of the remote command."""
        opts = SshOptions.model_validate(backend_options)
        remote = shlex.join(argv)
        if opts.directory:
            remote = f"cd {shlex.quote(opts.directory)} && {remote}"
        ssh_argv = ["ssh"]
        if opts.port is not None:
            ssh_argv += ["-p", str(opts.port)]
        if opts.identity:
            ssh_argv += ["-i", opts.identity]
        ssh_argv += [opts.target, remote]
        return ssh_argv
