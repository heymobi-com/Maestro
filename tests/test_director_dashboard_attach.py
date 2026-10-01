"""A dashboard that cannot attach to a project shows no clips at all.

Why this file exists. The Director Dashboard renders a project's clips only from
``dashboardSelectedPipeline``, and the only action that fills it is
``loadSavedPipeline``. That action raises ``dashboardLoading`` for the duration of
the fetch and lowers it on the path that wins the token race -- a superseded load
returns early and leaves the flag raised. So anything that invalidates
``_dashboardPipelineLoadToken`` *without* starting a replacement load pins
``dashboardLoading`` to true, and the Dashboard then renders "Loading pipeline..."
forever: no clips, no videos, and every action button disabled, until the page is
reloaded.

``deletePipeline`` is that site, and its token bump is deliberate: it stops a load
already in flight from resurrecting the project being deleted (checked below too).
For exactly that reason it owes the flag -- it must clear ``dashboardLoading`` in
the same update that clears the selection.

The live failure this encodes: a session with eleven ``GET /api/v1/director/pipelines``
and not a single ``GET`` of any pipeline detail. The list kept refreshing while no
project could attach.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "ui" / "src" / "stores" / "useStore.ts"
DASHBOARD = ROOT / "ui" / "src" / "components" / "DirectorDashboard" / "DirectorDashboard.tsx"

TOKEN = "_dashboardPipelineLoadToken"
LOAD = "loadSavedPipeline: async (pid) =>"
DELETE = "deletePipeline: async (pid) =>"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _body(source: str, marker: str) -> str:
    """The brace-delimited block that follows ``marker``.

    A slice of the real source, so the assertions below read the code that runs
    rather than a description of it.
    """
    start = source.index(marker)
    opening = source.index("{", start)
    depth = 0
    for index in range(opening, len(source)):
        character = source[index]
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return source[opening : index + 1]
    raise AssertionError(f"unbalanced braces after {marker!r}")


class DashboardAttachTest(unittest.TestCase):
    """The dashboard must never be left unable to attach to a project."""

    def setUp(self) -> None:
        self.store = _read(STORE)
        self.dashboard = _read(DASHBOARD)

    def test_deleting_a_project_clears_the_loading_flag(self):
        """The update that abandons a load and drops the selection clears the flag."""
        delete = _body(self.store, DELETE)
        self.assertIn(
            f"{TOKEN} += 1",
            delete,
            "deletePipeline is expected to invalidate an in-flight load",
        )
        update = _body(delete, "set(s => ({")
        self.assertIn("dashboardSelectedPipeline: null", update)
        self.assertIn(
            "dashboardLoading: false",
            update,
            "this update abandons the load that would have lowered the flag, so it "
            "has to lower it: otherwise the dashboard never attaches again",
        )

    def test_the_abandoned_load_keeps_its_token_guard(self):
        """The protection that stops a stale load resurrecting a deleted project."""
        load = _body(self.store, LOAD)
        self.assertIn("set({ dashboardLoading: true })", load)
        self.assertEqual(
            load.count(f"if (loadToken !== {TOKEN}) return"),
            2,
            "both the success and the failure path must ignore a superseded load",
        )

    def test_only_the_load_and_the_delete_touch_the_token(self):
        """One site may abandon a load, so one site owes the flag.

        A third increment would mean a new way to leave the dashboard loading
        forever, and it has to clear ``dashboardLoading`` as well.
        """
        self.assertEqual(self.store.count(f"{TOKEN} += 1"), 1)
        self.assertEqual(self.store.count(f"++{TOKEN}"), 1)
        self.assertIn(f"{TOKEN} += 1", _body(self.store, DELETE))
        self.assertIn(f"++{TOKEN}", _body(self.store, LOAD))

    def test_the_flag_is_what_hides_every_shot(self):
        """Why a raised flag is fatal, so the invariant is not relaxed later.

        The auto-load effect is gated on it, the loading branch is what replaces
        the clip grid, and every action button is disabled while it is set.
        """
        self.assertIn(
            "pipelineList.length > 0 && !selectedPipeline && !loading",
            self.dashboard,
            "the dashboard only auto-attaches a project while nothing is loading",
        )
        self.assertIn("Loading pipeline", self.dashboard)
        self.assertIn("disabled={loading", self.dashboard)


if __name__ == "__main__":
    unittest.main()
