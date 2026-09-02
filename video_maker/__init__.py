__all__ = ["VideoPlayer"]


def __getattr__(name):
    """تحميل الواجهة عند طلبها فقط حتى تبقى الأدوات المستقلة قابلة للاختبار."""
    if name == "VideoPlayer":
        from video_maker.player import VideoPlayer
        return VideoPlayer
    raise AttributeError(name)
