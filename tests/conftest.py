"""GUI tests must never add projects to the user's real engineering library."""
import pytest


@pytest.fixture(autouse=True)
def isolated_project_library(tmp_path, monkeypatch):
    monkeypatch.setattr('app.gui.PROJECTS', tmp_path / 'projects')
