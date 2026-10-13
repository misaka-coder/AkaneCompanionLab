"""Compose runtime responsibilities; public constructors remain in web_browser."""

from .public_pages import OpenBrowserMixin
from .music_search import MusicSearchMixin
from .browser_inputs import BrowserInputsMixin
from .browser_authorization import BrowserAuthorizationMixin
from .browser_feedback import BrowserFeedbackMixin
from .browser_lifecycle import BrowserLifecycleMixin
from .common import BrowserCommonMixin
from .browser_execution import BrowserExecutionMixin
from .browser_snapshots import BrowserSnapshotsMixin
from .browser_artifacts import BrowserArtifactsMixin
from .browser_result import BrowserResultMixin
from .search_inputs import SearchInputsMixin
from .search_execution import SearchExecutionMixin
from .search_transport import SearchTransportMixin
from .search_readiness import SearchReadinessMixin
from .search_cursors import SearchCursorsMixin
from .search_results import SearchResultsMixin
from .search_document_paging import SearchDocumentPagingMixin
from .search_documents import SearchDocumentsMixin
from .search_payloads import SearchPayloadsMixin
from .search_evidence import SearchEvidenceMixin
from .search_feedback import SearchFeedbackMixin
from .search_redaction import SearchRedactionMixin
from .search_identity import SearchIdentityMixin


class BrowserPageRuntimeMixin(
    BrowserArtifactsMixin,
    BrowserResultMixin,
    BrowserExecutionMixin,
    BrowserSnapshotsMixin,
    BrowserInputsMixin,
    BrowserAuthorizationMixin,
    BrowserFeedbackMixin,
    BrowserLifecycleMixin,
):
    """Composition only; each behavior has one implementation owner."""


class WebSearchRuntimeMixin(
    SearchInputsMixin,
    SearchExecutionMixin,
    SearchTransportMixin,
    SearchReadinessMixin,
    SearchCursorsMixin,
    SearchResultsMixin,
    SearchDocumentPagingMixin,
    SearchDocumentsMixin,
    SearchPayloadsMixin,
    SearchEvidenceMixin,
    SearchFeedbackMixin,
    SearchRedactionMixin,
    SearchIdentityMixin,
):
    """Composition only; each behavior has one implementation owner."""
