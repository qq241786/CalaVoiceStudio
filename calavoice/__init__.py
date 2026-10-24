"""CalaVoiceStudio core 层（纯 Python，不依赖 PySide6）。"""

__version__ = "0.1.0"


def capabilities() -> dict:
    """当前 core 的能力/构建指纹，供界面启动日志打印，用来确认跑的是不是新构建。"""
    return {
        "core_version": __version__,
        "text_norm": True,
        "adaptive_timeout": True,
        "auto_recover": True,
        "stale_backend_cleanup": True,
        "prewarm": True,
        "gpu_report": True,
        "synthesize_long": True,
        "merge_wavs": True,
        "discover_calaplayer": True,
        "discover_audition": True,
        "output_dir_resolver": True,
    }
