"""Compare real UTF-8 content with a caller's previous SHA-256 digest."""

from hashlib import sha256

from akane_plugin import Plugin, Result


def create_plugin():
    plugin = Plugin("example.change-check")

    def compare(content: str, previous_digest: str = "") -> Result:
        """Hash content; only ask for a model followup when the content changed.

        Supply the previous result's digest to check the next version. An empty
        previous digest is the first observation, which requires a followup.
        """
        digest = sha256(content.encode("utf-8")).hexdigest()
        changed = digest != previous_digest
        return Result(value={"changed": changed, "digest": digest, "utf8_bytes": len(content.encode("utf-8"))},
                      followup="required" if changed else "none")

    plugin.tool(compare, name="check")
    plugin.tool(compare, name="check_background", execution_class="long_task")
    return plugin
