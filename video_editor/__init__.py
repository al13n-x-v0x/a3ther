"""A3THER video editor — style clips/images from a folder into a short video.

Real implementation on top of OpenCV (cv2) when it's installed; honest,
actionable errors when it isn't. Rendered videos land in
``Output/videos/`` and are served by ``GET /api/video/file/{name}``.
"""

__version__ = "1.0.0"
