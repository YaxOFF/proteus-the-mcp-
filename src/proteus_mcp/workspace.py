"""State holder for the MCP server: one open Circuit and at most one live Proteus Session."""
import atexit
from pathlib import Path


class WorkspaceError(Exception):
    """A rule violation with an actionable hint for the AI client."""

    def __init__(self, message, hint=""):
        super().__init__(message)
        self.hint = hint


class Workspace:
    def __init__(self):
        self.circuit = None
        self.path = None          # last saved/opened path (Path) or None
        self.dirty = False
        self.session = None
        self.simulation = None
        self.bridge = None
        self.live_path = None     # file the live Session was started on
        self.live_dirty = False   # circuit edited after the session was opened

    # ---- circuit ----------------------------------------------------
    def set_circuit(self, circuit, path=None, dirty=False):
        self.close_live()
        self.circuit, self.path, self.dirty = circuit, (Path(path) if path else None), dirty

    def need_circuit(self):
        if self.circuit is None:
            raise WorkspaceError("no project is open",
                                 "call project_new or project_open first")
        return self.circuit

    def touch(self):
        """Mark the in-memory circuit as modified (call after every edit)."""
        self.dirty = True
        if self.session is not None:
            self.live_dirty = True

    def mark_saved(self, path):
        self.path, self.dirty = Path(path), False

    # ---- live session -----------------------------------------------
    def need_saved(self):
        self.need_circuit()
        if self.path is None or not self.path.exists():
            raise WorkspaceError("the project has not been saved to disk",
                                 "call save_project first, then retry")
        if self.dirty:
            raise WorkspaceError("the project has unsaved edits",
                                 "call save_project (use overwrite=true to replace the file) so Proteus sees them")
        return self.path

    def need_session(self):
        if self.session is None:
            raise WorkspaceError("Proteus is not open on a project",
                                 "call open_in_proteus first")
        return self.session

    def need_simulation(self):
        self.need_session()
        if self.simulation is None:
            raise WorkspaceError("simulation is not available", "call open_in_proteus first")
        return self.simulation

    def need_bridge(self):
        if self.bridge is None:
            raise WorkspaceError("the probe bridge is not connected",
                                 "add_probe, save, open_in_proteus, then sim_start; "
                                 "the bridge connects when the simulation runs")
        return self.bridge

    def attach_session(self, session, simulation, path):
        self.close_live()
        self.session, self.simulation = session, simulation
        self.live_path, self.live_dirty = (Path(path) if path else None), False

    def attach_bridge(self, bridge):
        self._close_bridge()
        self.bridge = bridge

    def _close_bridge(self):
        bridge, self.bridge = self.bridge, None
        if bridge is not None:
            # HOLD freezes Proteus inside our model callback. Release it before
            # asking the native simulator to stop, or Proteus kills that thread.
            try:
                if hasattr(bridge, "request"):
                    bridge.request("RUN", timeout=1)
                else:
                    bridge.run()
            except Exception:
                pass  # analog-only probes and stopped simulations have no RUN
            try:
                bridge.close()
            except Exception:
                pass

    def close_live(self, save=False):
        """Stop simulation, close the bridge and Proteus. Never raises."""
        errors = []
        self._close_bridge()
        session, simulation = self.session, self.simulation
        self.session = self.simulation = None
        self.live_path, self.live_dirty = None, False
        if session is None:
            return errors
        attached = getattr(session, "attached", False)
        try:
            # The user's own Proteus (attach mode) keeps its simulation state.
            if simulation is not None and not attached:
                simulation.stop()
        except Exception:
            pass
        if save:
            try:
                session.save()
            except Exception as exc:
                errors.append(f"save: {exc}")
        try:
            session.close(discard=not save)
        except Exception as exc:
            errors.append(f"close: {exc}")
            self._terminate(session)
        return errors

    @staticmethod
    def _terminate(session):
        try:
            process = session.process
            if process.poll() is None:
                process.terminate()
        except Exception:
            pass


WORKSPACE = Workspace()
atexit.register(lambda: WORKSPACE.close_live())
