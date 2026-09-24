"""The existing Satellite adapter with desktop-specific argument validation."""
from .adapters import DesktopSatelliteToolHandler
from ..computer_use.contracts import GUIDE, GUIDE_VERSION


class ComputerUseToolHandler(DesktopSatelliteToolHandler):
    default_exposure_mode = "on_demand"
    contract_revision = "guide:" + GUIDE_VERSION

    def __init__(self, *, offer_source=None):
        super().__init__(tool_id="computer_use", offer_source=offer_source)

    def build_prompt_instruction(self):
        return super().build_prompt_instruction() + "\n" + GUIDE

    def usage_guide(self):
        return {"version": GUIDE_VERSION, "text": GUIDE}

    def contract_reference(self, digest):
        """Shorter encoding of the entire digest; no aliases or weaker identity."""
        import base64
        return "cu_" + base64.urlsafe_b64encode(bytes.fromhex(digest)).decode("ascii").rstrip("=")
