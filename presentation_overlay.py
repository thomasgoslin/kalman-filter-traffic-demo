"""Presentation header for an already annotated video frame."""

from PIL import Image, ImageDraw, ImageFont


STATUS_ONLINE = "#77F477"
STATUS_OFFLINE = "#FF6269"
TIMELINE_ONLINE = "#39754C"
TIMELINE_OFFLINE = "#BE454F"


class PresentationOverlay:
    def __init__(self, width, frame_span, dropouts, colors):
        self.width = width
        self.frame_span = frame_span
        self.dropouts = dropouts
        self.colors = colors
        scale = max(0.28, min(width / 1100, 1.35))
        self.height = round(100 * scale)
        self.margin = round(24 * scale)
        self.title_font = ImageFont.load_default(size=round(32 * scale))
        self.body_font = ImageFont.load_default(size=round(21 * scale))
        self.body_y = round(47 * scale)
        self.legend_y = round(23 * scale)
        self.bar_y = round(74 * scale)

    def add_to(self, frame, index, withheld):
        """Add space above the image without resizing or drawing over it."""
        composite = Image.new("RGB", (self.width, frame.height + self.height), "#151C29")
        composite.paste(frame, (0, self.height))
        draw = ImageDraw.Draw(composite)
        accent = STATUS_OFFLINE if withheld else STATUS_ONLINE
        draw.text((self.margin, round(self.margin * 0.4)), "DETECTOR",
                  font=self.title_font, fill=accent)
        draw.text((self.margin, self.body_y), f"Source frame {index}",
                  font=self.body_font, fill="#F0F3F8")

        legend = (
            ("Kalman forecast", "forecast"),
            ("Detector measurement", "detector"),
            ("Kalman update", "update"),
        )
        swatch = round(self.margin * 0.9)
        swatch_gap = max(2, self.margin // 4)
        text_widths = [round(draw.textlength(text, font=self.body_font)) for text, _ in legend]
        legend_width = sum(text_widths) + len(legend) * (swatch + swatch_gap)
        legend_width += (len(legend) - 1) * self.margin
        x = self.width - self.margin - legend_width
        line_y = self.legend_y + round(self.body_font.size * 0.6)
        for (text, kind), text_width in zip(legend, text_widths):
            color = self.colors[kind]
            draw.line((x, line_y, x + swatch, line_y), fill=color, width=max(1, swatch // 6))
            x += swatch + swatch_gap
            draw.text((x, self.legend_y), text, font=self.body_font, fill=color)
            x += text_width + self.margin

        if self.frame_span:
            self._draw_timeline(draw, index)
        return composite

    def _draw_timeline(self, draw, index):
        start, end = self.frame_span.start, self.frame_span.stop
        right = self.width - self.margin - 1
        bottom = self.height - max(4, self.margin // 2)
        pixels = right - self.margin + 1
        # Keep blocks distinct on long excerpts by showing a window around the current frame.
        count = min(end - start, pixels // 2)
        if count < 1:
            return
        start = max(start, min(index - count // 2, end - count))
        gap = max(1, min(4, pixels // count // 5))

        for offset in range(count):
            frame = start + offset
            x0 = self.margin + offset * pixels // count
            x1 = self.margin + (offset + 1) * pixels // count - gap - 1
            withheld = any(frame in dropout for dropout in self.dropouts)
            fill = TIMELINE_OFFLINE if withheld else TIMELINE_ONLINE
            rect = (x0, self.bar_y, x1, bottom)
            draw.rectangle(rect, fill=fill)
            if frame == index:
                draw.rectangle((x0, self.bar_y - 3, x1, bottom + 3), outline="#FFFFFF")
