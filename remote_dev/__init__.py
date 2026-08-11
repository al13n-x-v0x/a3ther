"""
A3THER Remote Server Developer Mode.

When the user says *"a3ther, act as a dev on <server>"* the
:class:`remote_dev.dev_mode.DevModeManager` opens a secure paramiko
channel to the server profile (from ``config/servers.json`` or the
``A3THER_SSH_*`` environment variables) and lets A3THER run commands,
read log files and deploy patches over SFTP — like a real developer on
that box.

Security
--------
- Host keys are verified against ``~/.ssh/known_hosts`` when available.
- Private keys are preferred over passwords; passwords are only read from
  environment variables, never stored in plain-text config.
"""
from .dev_mode import DevModeManager, get_dev_mode_manager

__all__ = ["DevModeManager", "get_dev_mode_manager"]
