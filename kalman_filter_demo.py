"""Compatibility entry point for the original single-video command."""

from traffic_lab.render import main
from traffic_lab.tracker import F, H, P0, Q, R, Detection, FrameTrack, Track, Tracker

__all__ = ["F", "H", "P0", "Q", "R", "Detection", "FrameTrack", "Track", "Tracker"]


if __name__ == "__main__":
    main()
