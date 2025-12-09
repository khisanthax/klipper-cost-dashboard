"""
Installer entry points (master and client installs) imported by install.py.

This module exposes master_setup, install_client_local, and install_client_remote by
delegating to the implementations available via installer.utils (which provides all
expected helpers and state handling).
"""

from installer import utils as _u

# Re-export the expected functions so install.py can import them.
master_setup = _u.master_setup
install_client_local = _u.install_client_local
install_client_remote = _u.install_client_remote

__all__ = [
    "master_setup",
    "install_client_local",
    "install_client_remote",
]
