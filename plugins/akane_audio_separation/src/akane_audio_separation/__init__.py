"""Audio separation business package, independent of Akane host internals."""


def create_plugin():
    # Standalone ML/service consumers can import the business runtime without
    # importing the Akane SDK. Only plugin activation needs the host contract.
    from .plugin import AudioSeparationPlugin

    return AudioSeparationPlugin()


__all__ = ["create_plugin"]
