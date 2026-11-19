"""NovelAI image generation for Krita."""

__version__ = "0.1.0"

try:
    import krita as _krita  # type: ignore
except ImportError:
    _krita = None

# Only register with Krita when running inside it, so the Krita-free modules
# (api, request, cost, resolution, panels, settings) import cleanly in tests.
if _krita is not None:
    from krita import Krita, DockWidgetFactory, DockWidgetFactoryBase  # type: ignore

    from .extension import NovelAIExtension
    from .ui.docker import NovelAIDocker

    _app = Krita.instance()
    _app.addExtension(NovelAIExtension(_app))
    _pos = getattr(DockWidgetFactoryBase, "DockRight", None)
    if _pos is None:
        _pos = DockWidgetFactoryBase.DockPosition.DockRight
    _app.addDockWidgetFactory(DockWidgetFactory("nai_krita_docker", _pos, NovelAIDocker))
